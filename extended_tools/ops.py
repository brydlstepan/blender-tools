"""Operators for the Extended Tools panel."""

from __future__ import annotations

import bmesh
import bpy
from bpy.props import IntProperty
from bpy.types import Context, Operator

from . import clear_parent, loops
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


class BLENDERTOOLS_OT_select_next_loops(Operator):
    """With two edge loops selected, select the following loops at the same spacing"""

    bl_idname = "blender_tools.select_next_loops"
    bl_label = "Select Next Loops"
    bl_description = (
        "Select two loops (with any number of loops skipped between them): "
        "the direction and skip are repeated to select every following loop"
    )
    bl_options = {"REGISTER", "UNDO"}

    count: IntProperty(
        name="Count",
        description="How many loops to add (0 = until the end of the mesh)",
        default=0,
        min=0,
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.mode == "EDIT_MESH"

    def execute(self, context: Context):
        me = context.edit_object.data
        bm = bmesh.from_edit_mesh(me)
        try:
            history = [e for e in bm.select_history if isinstance(e, bmesh.types.BMEdge)]
            edges = loops.next_loops([e for e in bm.edges if e.select], self.count, history)
        except ValueError as err:
            self.report({"WARNING"}, str(err))
            return {"CANCELLED"}
        if not edges:
            self.report({"INFO"}, "No further loops")
            return {"CANCELLED"}
        for edge in edges:
            edge.select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(me)
        return {"FINISHED"}


classes = (BLENDERTOOLS_OT_clear_parent_keep_transform, BLENDERTOOLS_OT_select_next_loops)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
