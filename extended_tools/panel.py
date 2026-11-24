"""Sidebar panel: Extended Tools."""

from __future__ import annotations

import bpy
from bpy.types import Context, Panel

from ..version import VERSION


_LOCK_PROP = {"LOC": "lock_location", "ROT": "lock_rotation", "SCALE": "lock_scale"}


def _draw_axis_rows(layout, props, channel, title, obj) -> None:
    """One block like Item > Transform: "Title:" then X / Y / Z rows with the lock icon on the right."""
    from .absolute_item import props as abs_props

    layout.label(text=f"{title}:")
    col = layout.column(align=True)
    for axis in range(3):
        row = col.row(align=True)
        state = abs_props.axis_state(channel, axis)
        if state == "mixed":
            row.prop(props, abs_props.prop_name(channel, axis, mixed=True), text="XYZ"[axis])
        else:
            sub = row.row(align=True)
            sub.enabled = state == "value"
            sub.prop(props, abs_props.prop_name(channel, axis), text="XYZ"[axis])
        if obj is not None:
            row.prop(obj, _LOCK_PROP[channel], index=axis, text="", emboss=False)


def _draw_absolute_transform(layout: bpy.types.UILayout, context: Context) -> None:
    props = getattr(context.scene, "blender_tools_absolute_item", None)
    if props is None:
        layout.label(text="Reload Blender Tools add-on", icon="ERROR")
        return

    if context.mode not in {"EDIT_MESH", "OBJECT"}:
        layout.label(text="Available in Object / Edit Mesh", icon="INFO")
        return

    # Never write RNA from draw() — schedule sync instead.
    from .absolute_item import props as abs_props

    abs_props.schedule_sync()

    layout.row().prop(props, "space", expand=True)

    object_mode = context.mode == "OBJECT"
    obj = context.object if object_mode else None

    layout.use_property_split = False
    _draw_axis_rows(layout, props, "LOC", "Location", obj)
    if object_mode:
        _draw_axis_rows(layout, props, "ROT", "Rotation", obj)
        _draw_axis_rows(layout, props, "SCALE", "Scale", obj)
    else:
        hint = layout.column()
        hint.scale_y = 0.85
        hint.label(text="Rotation / Scale: Object mode", icon="INFO")

    if props.last_status:
        layout.label(text=props.last_status, icon="CHECKMARK")


class VIEW3D_PT_blender_tools_item_extended(Panel):
    bl_label = "Absolute Transform"
    bl_idname = "VIEW3D_PT_blender_tools_item_extended"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Item"  # sits under Blender's own Transform panel

    def draw(self, context: Context) -> None:
        _draw_absolute_transform(self.layout, context)


class VIEW3D_PT_blender_tools_extended(Panel):
    bl_label = "Extended Tools"
    bl_idname = "VIEW3D_PT_blender_tools_extended"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Extended Tools"

    def draw(self, context: Context) -> None:
        layout = self.layout

        layout.label(text="Parenting:")
        layout.operator("blender_tools.clear_parent_keep_transform", icon="X")

        selected = context.selected_objects
        parented = sum(1 for obj in selected if obj.parent is not None)
        help_col = layout.column()
        help_col.scale_y = 0.85
        help_col.label(text=f"{parented}/{len(selected)} selected have a parent", icon="INFO")

        layout.separator()
        layout.label(text="Loops:")
        layout.operator("blender_tools.select_next_loops", icon="MOD_ARRAY")
        loop_help = layout.column()
        loop_help.scale_y = 0.85
        loop_help.label(text="Edit Mesh: select loop 1, skip, loop 2", icon="INFO")

        layout.separator()
        layout.label(text="Mesh:")
        layout.operator("blender_tools.fix_quad_topology", icon="MESH_GRID")
        layout.operator("blender_tools.unsubdivide_safe", icon="MOD_DECIM")
        layout.operator("blender_tools.adaptive_quad_remesh", icon="MOD_REMESH")
        unsub_help = layout.column()
        unsub_help.scale_y = 0.85
        unsub_help.label(text="Edit Mesh: keeps UV island borders", icon="INFO")

        footer = layout.row()
        footer.alignment = "RIGHT"
        footer.label(text=VERSION)


classes = (VIEW3D_PT_blender_tools_item_extended, VIEW3D_PT_blender_tools_extended)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
