"""Scene properties driving the absolute Location / Rotation / Scale fields.

Each axis is a real float property, so the fields look and behave like Blender's own
Item > Transform fields (units, drag, arrows, Ctrl+wheel). When the selection does not
share one value on an axis, the panel draws a text field showing "-" instead; typing a
number there sets every selected element to it.
"""

from __future__ import annotations

from math import radians

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, FloatProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

from .apply import Channel

# Prevent recursive updates when syncing the fields from the selection.
_syncing = False
_MIXED = "-"
# Channels waiting for a deferred absolute-set; skip overwriting those fields.
_pending: set[tuple[str, int]] = set()
_sync_timer_pending = False
# (channel, axis) -> "none" (nothing selected), "mixed" or "value". Read by the panel.
_state: dict[tuple[str, int], str] = {}

_PREFIX = {"LOC": "loc", "ROT": "rot", "SCALE": "scale"}
_CHANNELS = ("LOC", "ROT", "SCALE")


def prop_name(channel: Channel, axis: int, *, mixed: bool = False) -> str:
    return f"{_PREFIX[channel]}_{'xyz'[axis]}" + ("_mixed" if mixed else "")


def axis_state(channel: Channel, axis: int) -> str:
    return _state.get((channel, axis), "none")


def _parse_number(text: str) -> float | None:
    stripped = text.strip().replace(",", ".")
    if not stripped or stripped == _MIXED:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def _parse_length(text: str, scene: bpy.types.Scene) -> float | None:
    """Text typed into a mixed Location field -> Blender units. Plain numbers use the scene's unit."""
    units = scene.unit_settings
    if units.system != "NONE":
        try:
            meters = bpy.utils.units.to_value(units.system, "LENGTH", text, str_ref_unit=units.length_unit)
            return meters / units.scale_length
        except Exception:  # Blender evaluates the text as an expression; anything odd means "not a number"
            pass
    return _parse_number(text)


def _schedule_set(channel: Channel, axis: int, value: float, space: str) -> None:
    key = (channel, axis)
    _pending.add(key)

    def _run_set() -> float | None:
        try:
            try:
                bpy.ops.blender_tools.set_absolute_channel_axis(
                    channel=channel,
                    axis=axis,
                    value=value,
                    space=space,
                )
            except RuntimeError:
                from . import apply

                apply.set_channel_axis(bpy.context, channel, axis, value, space)
        finally:
            _pending.discard(key)
            schedule_sync(force=True)
        return None

    bpy.app.timers.register(_run_set, first_interval=0.0)


def _make_value_update(channel: Channel, axis: int):
    """The user edited a float field: set that axis absolutely on every selected element."""

    def _update(self: "AbsoluteItemProps", _context: bpy.types.Context) -> None:
        if _syncing:
            return
        _schedule_set(channel, axis, getattr(self, prop_name(channel, axis)), self.space)

    return _update


def _make_mixed_text(channel: Channel, axis: int):
    """Text field shown while the selection is mixed: reads "-", typing a number sets all."""

    def _get(_self: "AbsoluteItemProps") -> str:
        return _MIXED

    def _set(self: "AbsoluteItemProps", text: str) -> None:
        if channel == "LOC":
            value = _parse_length(text, bpy.context.scene)
        else:
            value = _parse_number(text)
            if value is not None and channel == "ROT":
                value = radians(value)
        if value is not None:
            _schedule_set(channel, axis, value, self.space)

    return _get, _set


def _space_update(self: "AbsoluteItemProps", context: bpy.types.Context) -> None:
    sync_fields_from_selection(context, force=True)


def _float_property(channel: Channel, axis: int) -> FloatProperty:
    common = dict(name="XYZ"[axis], update=_make_value_update(channel, axis))
    if channel == "LOC":
        return FloatProperty(subtype="DISTANCE", unit="LENGTH", **common)
    if channel == "ROT":
        return FloatProperty(subtype="ANGLE", unit="ROTATION", **common)
    return FloatProperty(default=1.0, precision=3, **common)


def _mixed_property(channel: Channel, axis: int) -> StringProperty:
    getter, setter = _make_mixed_text(channel, axis)
    return StringProperty(name="XYZ"[axis], get=getter, set=setter)


def _build_annotations() -> dict:
    annotations = {
        "space": EnumProperty(
            name="Space",
            description="Coordinate space used for absolute transform edits",
            items=(
                ("GLOBAL", "Global", "World space"),
                ("LOCAL", "Local", "Object-local space"),
            ),
            default="GLOBAL",
            update=_space_update,
        ),
        "last_status": StringProperty(name="Status", default="", options={"SKIP_SAVE"}),
    }
    for channel in _CHANNELS:
        for axis in range(3):
            annotations[prop_name(channel, axis)] = _float_property(channel, axis)
            annotations[prop_name(channel, axis, mixed=True)] = _mixed_property(channel, axis)
    return annotations


# Blender reads PropertyGroup properties from __annotations__.
AbsoluteItemProps = type(
    "AbsoluteItemProps",
    (PropertyGroup,),
    {"__annotations__": _build_annotations(), "__module__": __name__},
)


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def _sync_channel(props: AbsoluteItemProps, channel: Channel, displays: tuple, *, force: bool) -> bool:
    """Copy the selection's values into the fields. Returns True if the panel needs a redraw."""
    redraw = False
    for axis, display in enumerate(displays):
        key = (channel, axis)
        if not force and key in _pending:
            continue
        if not display.has_selection:
            state = "none"
        elif not display.uniform:
            state = "mixed"
        else:
            state = "value"
        if _state.get(key) != state:
            _state[key] = state
            redraw = True
        if state == "value":
            name = prop_name(channel, axis)
            value = display.value + 0.0  # turns -0.0 into 0.0 so the field never shows "-0"
            if not _same(getattr(props, name), value):
                setattr(props, name, value)
    return redraw


def sync_fields_from_selection(
    context: bpy.types.Context | None = None,
    *,
    force: bool = False,
) -> None:
    """Refresh transform fields from selection: value if uniform, "-" if mixed."""
    global _syncing

    if context is None:
        context = bpy.context
    if context is None or context.scene is None:
        return
    if context.mode not in {"EDIT_MESH", "OBJECT"}:
        return

    props = getattr(context.scene, "blender_tools_absolute_item", None)
    if props is None:
        return

    from . import apply

    redraw = False
    _syncing = True
    try:
        for channel in _CHANNELS:
            displays = apply.get_channel_displays(context, props.space, channel)
            redraw |= _sync_channel(props, channel, displays, force=force)
    finally:
        _syncing = False

    if redraw:
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


def schedule_sync(*, force: bool = False) -> None:
    """Queue a sync outside of draw/RNA-restricted contexts."""
    global _sync_timer_pending
    if _sync_timer_pending:
        return
    _sync_timer_pending = True

    def _run() -> float | None:
        global _sync_timer_pending
        _sync_timer_pending = False
        try:
            sync_fields_from_selection(force=force)
        except Exception:
            import traceback

            traceback.print_exc()
        return None

    bpy.app.timers.register(_run, first_interval=0.0)


@persistent
def _on_depsgraph_update(scene: bpy.types.Scene, _depsgraph: bpy.types.Depsgraph) -> None:
    schedule_sync()


classes = (AbsoluteItemProps,)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blender_tools_absolute_item = PointerProperty(type=AbsoluteItemProps)

    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)

    schedule_sync(force=True)


def unregister() -> None:
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)

    del bpy.types.Scene.blender_tools_absolute_item
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
