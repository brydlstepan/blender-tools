"""Export each object to its own FBX file."""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Context, Operator
from bpy_extras.io_utils import ExportHelper

from ..fbx_common import (
    ensure_object_mode,
    export_object_fbx,
    iter_export_roots,
    preserved_selection,
    sanitize_filename,
)


class BLENDERTOOLS_OT_export_separate_fbx(Operator, ExportHelper):
    """Export each object as its own FBX into the chosen folder"""

    bl_idname = "blender_tools.export_separate_fbx"
    bl_label = "Separate FBX Export"
    bl_options = {"PRESET"}

    filename_ext = ".fbx"

    filter_glob: StringProperty(
        default="*.fbx",
        options={"HIDDEN"},
        maxlen=255,
    )

    use_selection: BoolProperty(
        name="Selection Only",
        description="Export selected objects only (disable to export every object in the scene)",
        default=True,
    )

    mesh_only: BoolProperty(
        name="Mesh Objects Only",
        description="Skip cameras, lights, empties, etc.",
        default=True,
    )

    include_children: BoolProperty(
        name="Include Children",
        description="Also select and export each object's children into the same FBX",
        default=False,
    )

    center_at_origin: BoolProperty(
        name="Export at Origin",
        description="Move each object so its origin (pivot) is at world 0 in the exported FBX",
        default=True,
    )

    use_mesh_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Apply modifiers (preview resolution) on export",
        default=True,
    )

    mesh_smooth_type: EnumProperty(
        name="Smoothing",
        description="How smoothing is written into the FBX",
        items=(
            ("SMOOTH_GROUP", "Smoothing Groups", "Write face smoothing groups"),
            ("FACE", "Face", "Write face smoothing"),
            ("EDGE", "Edge", "Write edge smoothing"),
            ("OFF", "Normals Only", "Export normals only (no smoothing groups)"),
        ),
        default="SMOOTH_GROUP",
    )

    bake_anim: BoolProperty(
        name="Bake Animation",
        description="Export baked keyframe animation",
        default=False,
    )

    axis_forward: EnumProperty(
        name="Forward",
        items=(
            ("X", "X Forward", ""),
            ("Y", "Y Forward", ""),
            ("Z", "Z Forward", ""),
            ("-X", "-X Forward", ""),
            ("-Y", "-Y Forward", ""),
            ("-Z", "-Z Forward", ""),
        ),
        default="-Z",
    )

    axis_up: EnumProperty(
        name="Up",
        items=(
            ("X", "X Up", ""),
            ("Y", "Y Up", ""),
            ("Z", "Z Up", ""),
            ("-X", "-X Up", ""),
            ("-Y", "-Y Up", ""),
            ("-Z", "-Z Up", ""),
        ),
        default="Y",
    )

    global_scale: FloatProperty(
        name="Scale",
        min=0.01,
        max=1000.0,
        default=1.0,
    )

    def draw(self, _context: Context) -> None:
        layout = self.layout
        layout.use_property_split = True

        col = layout.column(heading="Include")
        col.prop(self, "use_selection")
        col.prop(self, "mesh_only")
        col.prop(self, "include_children")

        col = layout.column(heading="Transform")
        col.prop(self, "center_at_origin")
        col.prop(self, "global_scale")
        col.prop(self, "axis_forward")
        col.prop(self, "axis_up")

        col = layout.column(heading="Geometry")
        col.prop(self, "use_mesh_modifiers")
        col.prop(self, "mesh_smooth_type")

        col = layout.column(heading="Animation")
        col.prop(self, "bake_anim")

        layout.separator()
        layout.label(text="Pick any name — each object becomes its own .fbx", icon="INFO")

    def execute(self, context: Context):
        folder_path = os.path.dirname(bpy.path.abspath(self.filepath))
        if not folder_path or not os.path.isdir(folder_path):
            self.report({"ERROR"}, "Choose a valid export folder")
            return {"CANCELLED"}

        ensure_object_mode(context)

        source = list(context.selected_objects) if self.use_selection else list(context.scene.objects)
        export_roots = iter_export_roots(source, mesh_only=self.mesh_only)

        if not export_roots:
            self.report({"WARNING"}, "Nothing to export")
            return {"CANCELLED"}

        exported = 0
        failed: list[str] = []

        with preserved_selection(context):
            for obj in export_roots:
                file_path = os.path.join(folder_path, f"{sanitize_filename(obj.name)}.fbx")
                ok = export_object_fbx(
                    context,
                    obj,
                    file_path,
                    include_children=self.include_children,
                    center_at_origin=self.center_at_origin,
                    global_scale=self.global_scale,
                    axis_forward=self.axis_forward,
                    axis_up=self.axis_up,
                    use_mesh_modifiers=self.use_mesh_modifiers,
                    mesh_smooth_type=self.mesh_smooth_type,
                    bake_anim=self.bake_anim,
                )
                if ok:
                    exported += 1
                else:
                    failed.append(obj.name)

        if failed:
            self.report(
                {"WARNING"},
                f"Exported {exported} FBX(s); failed: {', '.join(failed)}",
            )
        else:
            self.report({"INFO"}, f"Exported {exported} FBX file(s) to {folder_path}")

        return {"FINISHED"}


def menu_func(self, _context: Context) -> None:
    self.layout.operator(
        BLENDERTOOLS_OT_export_separate_fbx.bl_idname,
        text="FBX Separate Objects (.fbx)",
    )


classes = (BLENDERTOOLS_OT_export_separate_fbx,)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_func)


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_export.remove(menu_func)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
