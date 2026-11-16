"""Sidebar tab: Unreal Tools."""

from __future__ import annotations

import bpy
from bpy.types import Context, Panel

from ..separate_fbx_export.ops import iter_export_roots
from .props import resolve_export_dir


class VIEW3D_PT_blender_tools_unreal(Panel):
    bl_label = "Unreal Tools"
    bl_idname = "VIEW3D_PT_blender_tools_unreal"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Unreal Tools"

    def draw(self, context: Context) -> None:
        layout = self.layout
        settings = context.scene.blender_tools_unreal

        meshes = iter_export_roots(context.selected_objects, mesh_only=True)

        box = layout.box()
        box.label(text="Unreal", icon="WORLD")
        col = box.column(align=True)
        col.prop(settings, "restore_world_positions")
        col.separator()
        col.prop(settings, "place_mode", text="")
        if settings.place_mode == "ACTOR":
            col.prop(settings, "actor_name", text="Name")

        header, body = layout.panel("blender_tools_unreal_export_settings", default_closed=True)
        header.label(text="Export Settings", icon="FILE_FOLDER")
        if body is not None:
            body.prop(settings, "export_dir", text="")
            hint = body.column()
            hint.scale_y = 0.8
            if settings.export_dir.startswith("//") and not bpy.data.filepath:
                hint.label(text="Save the .blend to resolve // paths", icon="ERROR")
            elif meshes:
                hint.label(text=resolve_export_dir(settings.export_dir, meshes[0]))
            hint.label(text="Tokens: {blend} {collection} {object} · // = .blend folder")
            body.prop(settings, "use_mesh_modifiers")

        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("blender_tools.unreal_import", icon="IMPORT")
        layout.operator("blender_tools.unreal_export", icon="EXPORT")
        info = layout.column()
        info.scale_y = 0.8
        info.label(text=f"{len(meshes)} selected mesh(es)", icon="INFO")


classes = (VIEW3D_PT_blender_tools_unreal,)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
