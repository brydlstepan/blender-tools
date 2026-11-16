"""Operators for the Extended Tools panel."""

from __future__ import annotations

import bpy
from bpy.types import Context, Operator

from . import clear_parent
from ..version import VERSION


class BLENDERTOOLS_OT_clear_parent_keep_transform(Operator):
    """Clear parents on selected objects and keep their world transforms exactly"""

    bl_idname = "blender_tools.clear_parent_keep_transform"
    bl_label = "Clear Parent (Keep Transform)"
    bl_description = (
        "Clear parent for all selected objects while preserving exact world placement"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return (
            context.mode == "OBJECT"
            and bool(context.selected_objects)
            and any(obj.parent is not None for obj in context.selected_objects)
        )

    def execute(self, context: Context):
        selected = list(context.selected_objects)
        cleared, baked = clear_parent.clear_parents_keep_world(
            selected,
            view_layer=context.view_layer,
        )

        if cleared == 0:
            self.report({"WARNING"}, "No selected objects have a parent")
            return {"CANCELLED"}

        msg = f"Cleared parent on {cleared} object(s) [{VERSION}]"
        if baked:
            msg += f" · baked shear into {baked} datablock(s)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


classes = (BLENDERTOOLS_OT_clear_parent_keep_transform,)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
