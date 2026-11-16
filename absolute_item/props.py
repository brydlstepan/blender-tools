"""Scene properties driving absolute Location / Rotation / Scale fields."""

from __future__ import annotations

from math import radians

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

from .apply import Channel

# Prevent recursive updates when syncing UI text from the selection.
_syncing = False
_MIXED = "-"
# Channels waiting for a deferred absolute-set; skip overwriting those fields.
_pending: set[tuple[str, int]] = set()
_sync_timer_pending = False

_LOC_ATTRS = ("loc_x", "loc_y", "loc_z")
_ROT_ATTRS = ("rot_x", "rot_y", "rot_z")
_SCALE_ATTRS = ("scale_x", "scale_y", "scale_z")


def _parse_axis_text(text: str) -> float | None:
    stripped = text.strip().replace(",", ".")
    if not stripped or stripped == _MIXED:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


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


def _make_channel_update(channel: Channel, axis: int, attrs: tuple[str, str, str]):
    def _update(self: "AbsoluteItemProps", context: bpy.types.Context) -> None:
        global _syncing
        if _syncing:
            return

        raw = getattr(self, attrs[axis])
        parsed = _parse_axis_text(raw)
        if parsed is None:
            return

        # Rotation fields are edited in degrees; store/apply radians.
        value = radians(parsed) if channel == "ROT" else parsed
        _schedule_set(channel, axis, value, self.space)

        _syncing = True
        try:
            setattr(self, attrs[axis], f"{parsed:.6g}")
        finally:
            _syncing = False

    return _update


def _space_update(self: "AbsoluteItemProps", context: bpy.types.Context) -> None:
    sync_fields_from_selection(context, force=True)


class AbsoluteItemProps(PropertyGroup):
    space: EnumProperty(
        name="Space",
        description="Coordinate space used for absolute transform edits",
        items=(
            ("GLOBAL", "Global", "World space"),
            ("LOCAL", "Local", "Object-local space"),
        ),
        default="GLOBAL",
        update=_space_update,
    )

    loc_x: StringProperty(name="X", default="", update=_make_channel_update("LOC", 0, _LOC_ATTRS))
    loc_y: StringProperty(name="Y", default="", update=_make_channel_update("LOC", 1, _LOC_ATTRS))
    loc_z: StringProperty(name="Z", default="", update=_make_channel_update("LOC", 2, _LOC_ATTRS))

    rot_x: StringProperty(name="X", default="", update=_make_channel_update("ROT", 0, _ROT_ATTRS))
    rot_y: StringProperty(name="Y", default="", update=_make_channel_update("ROT", 1, _ROT_ATTRS))
    rot_z: StringProperty(name="Z", default="", update=_make_channel_update("ROT", 2, _ROT_ATTRS))

    scale_x: StringProperty(
        name="X", default="", update=_make_channel_update("SCALE", 0, _SCALE_ATTRS)
    )
    scale_y: StringProperty(
        name="Y", default="", update=_make_channel_update("SCALE", 1, _SCALE_ATTRS)
    )
    scale_z: StringProperty(
        name="Z", default="", update=_make_channel_update("SCALE", 2, _SCALE_ATTRS)
    )

    last_status: StringProperty(
        name="Status",
        default="",
        options={"SKIP_SAVE"},
    )


def _sync_channel(
    props: AbsoluteItemProps,
    channel: Channel,
    attrs: tuple[str, str, str],
    displays: tuple,
    *,
    force: bool,
    as_degrees: bool,
) -> None:
    texts = tuple(d.text(as_degrees=as_degrees) for d in displays)

    if not force:
        unchanged = True
        for axis, attr, text in zip(range(3), attrs, texts):
            if (channel, axis) in _pending:
                continue
            if getattr(props, attr) != text:
                unchanged = False
                break
        if unchanged:
            return

    for axis, attr, text in zip(range(3), attrs, texts):
        if not force and (channel, axis) in _pending:
            continue
        setattr(props, attr, text)


def sync_fields_from_selection(
    context: bpy.types.Context | None = None,
    *,
    force: bool = False,
) -> None:
    """Refresh transform fields from selection: value if uniform, '-' if mixed."""
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

    loc = apply.get_channel_displays(context, props.space, "LOC")
    rot = apply.get_channel_displays(context, props.space, "ROT")
    scale = apply.get_channel_displays(context, props.space, "SCALE")

    _syncing = True
    try:
        _sync_channel(props, "LOC", _LOC_ATTRS, loc, force=force, as_degrees=False)
        _sync_channel(props, "ROT", _ROT_ATTRS, rot, force=force, as_degrees=True)
        _sync_channel(props, "SCALE", _SCALE_ATTRS, scale, force=force, as_degrees=False)
    finally:
        _syncing = False


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
