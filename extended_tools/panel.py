"""Sidebar panel: Extended Tools."""

from __future__ import annotations

import bpy
from bpy.types import Context, Panel

from .version import VERSION


def _draw_absolute_transform(layout: bpy.types.UILayout, context: Context) -> None:
    props = getattr(context.scene, "blender_tools_absolute_item", None)
    box = layout.box()
    box.label(text="Absolute Transform", icon="ORIENTATION_GLOBAL")

    if props is None:
        box.label(text="Reload Blender Tools add-on", icon="ERROR")
        return

    if context.mode not in {"EDIT_MESH", "OBJECT"}:
        box.label(text="Available in Object / Edit Mesh", icon="INFO")
        return

    # Never write RNA from draw() — schedule sync instead.
    from ..absolute_item import props as abs_props

    abs_props.schedule_sync()

    col = box.column(align=True)
    col.prop(props, "space", expand=True)

    loc = box.column(align=True)
    loc.label(text="Location")
    loc.prop(props, "loc_x", text="X")
    loc.prop(props, "loc_y", text="Y")
    loc.prop(props, "loc_z", text="Z")

    if context.mode == "OBJECT":
        rot = box.column(align=True)
        rot.label(text="Rotation")
        rot.prop(props, "rot_x", text="X")
        rot.prop(props, "rot_y", text="Y")
        rot.prop(props, "rot_z", text="Z")

        scale = box.column(align=True)
        scale.label(text="Scale")
        scale.prop(props, "scale_x", text="X")
        scale.prop(props, "scale_y", text="Y")
        scale.prop(props, "scale_z", text="Z")
    else:
        hint = box.column()
        hint.scale_y = 0.85
        hint.label(text="Rotation / Scale: Object mode", icon="INFO")

    help_col = box.column()
    help_col.scale_y = 0.85
    help_col.label(text="Same value shown · mixed shows '-'")
    help_col.label(text="Editing sets all selected absolutely")

    if props.last_status:
        box.label(text=props.last_status, icon="CHECKMARK")


class VIEW3D_PT_blender_tools_extended(Panel):
    bl_label = "Extended Tools"
    bl_idname = "VIEW3D_PT_blender_tools_extended"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Extended Tools"

    def draw(self, context: Context) -> None:
        layout = self.layout

        _draw_absolute_transform(layout, context)

        box = layout.box()
        box.label(text="Parenting", icon="LINKED")
        col = box.column(align=True)
        col.operator(
            "blender_tools.clear_parent_keep_transform",
            icon="X",
        )

        selected = context.selected_objects
        parented = sum(1 for obj in selected if obj.parent is not None)
        help_col = box.column()
        help_col.scale_y = 0.85
        help_col.label(
            text=f"{parented}/{len(selected)} selected have a parent",
            icon="INFO",
        )

        footer = layout.row()
        footer.alignment = "RIGHT"
        footer.label(text=VERSION)


classes = (VIEW3D_PT_blender_tools_extended,)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
