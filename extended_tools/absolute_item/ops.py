"""Undo-aware operators for absolute transform channels."""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator

from . import apply

_CHANNEL_LABEL = {
    "LOC": "Location",
    "ROT": "Rotation",
    "SCALE": "Scale",
}


class BLENDERTOOLS_OT_set_absolute_channel_axis(Operator):
    bl_idname = "blender_tools.set_absolute_channel_axis"
    bl_label = "Set Absolute Transform Axis"
    bl_description = (
        "Set one transform axis to an absolute value on every selected "
        "element (unlike the default Item median offset)"
    )
    bl_options = {"REGISTER", "UNDO"}

    channel: EnumProperty(
        name="Channel",
        items=(
            ("LOC", "Location", ""),
            ("ROT", "Rotation", ""),
            ("SCALE", "Scale", ""),
        ),
        default="LOC",
    )
    axis: IntProperty(name="Axis", min=0, max=2, default=0)
    value: FloatProperty(name="Value")
    space: EnumProperty(
        name="Space",
        items=(
            ("GLOBAL", "Global", ""),
            ("LOCAL", "Local", ""),
        ),
        default="GLOBAL",
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return context.mode in {"EDIT_MESH", "OBJECT"}

    def execute(self, context: bpy.types.Context):
        count = apply.set_channel_axis(
            context,
            self.channel,  # type: ignore[arg-type]
            self.axis,
            self.value,
            self.space,
        )
        axis_name = ("X", "Y", "Z")[self.axis]
        channel_name = _CHANNEL_LABEL.get(self.channel, self.channel)

        if count == 0:
            self.report({"WARNING"}, "Nothing selected to update")
            return {"CANCELLED"}

        display_value = self.value
        unit = ""
        if self.channel == "ROT":
            from math import degrees

            display_value = degrees(self.value)
            unit = "°"

        msg = f"Set {channel_name} {axis_name}={display_value:.6g}{unit} on {count}"
        self.report({"INFO"}, msg)

        props = getattr(context.scene, "blender_tools_absolute_item", None)
        if props is not None:
            props.last_status = f"{msg} element(s)"
        return {"FINISHED"}


classes = (BLENDERTOOLS_OT_set_absolute_channel_axis,)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
