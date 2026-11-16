"""Read / write absolute location, rotation, and scale."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

AxisIndex = int  # 0=X, 1=Y, 2=Z
Channel = Literal["LOC", "ROT", "SCALE"]
_AXIS_EPS = 1e-6
_ROT_EPS = 1e-5  # radians
_EULER_ORDER = "XYZ"
# Above this many selected vertices the edit-mode location is not scanned (it shows "-").
_SCAN_LIMIT = 20_000


@dataclass(frozen=True)
class AxisDisplay:
    """Per-axis UI state for a multi-selection."""

    has_selection: bool
    uniform: bool
    value: float = 0.0

    def text(self, *, as_degrees: bool = False) -> str:
        if not self.has_selection:
            return ""
        if not self.uniform:
            return "-"
        value = self.value
        if as_degrees:
            from math import degrees

            value = degrees(value)
        return f"{value:.6g}"


def _is_global(space: str) -> bool:
    return space == "GLOBAL"


def iter_edit_meshes(context: bpy.types.Context):
    for obj in context.objects_in_mode_unique_data:
        if obj.type != "MESH":
            continue
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        yield obj, bm


def selected_locations(context: bpy.types.Context, space: str) -> list[Vector]:
    if context.mode == "EDIT_MESH":
        coords: list[Vector] = []
        for obj, bm in iter_edit_meshes(context):
            if _is_global(space):
                matrix = obj.matrix_world
                for vert in bm.verts:
                    if vert.select:
                        coords.append(matrix @ vert.co)
            else:
                for vert in bm.verts:
                    if vert.select:
                        coords.append(vert.co.copy())
        return coords

    if context.mode == "OBJECT":
        coords = []
        for obj in context.selected_objects:
            if _is_global(space):
                coords.append(obj.matrix_world.translation.copy())
            else:
                coords.append(obj.location.copy())
        return coords

    return []


def selected_rotations(context: bpy.types.Context, space: str) -> list[Euler]:
    """Object rotations as XYZ Euler (radians). Empty in mesh edit mode."""
    if context.mode != "OBJECT":
        return []

    rotations: list[Euler] = []
    for obj in context.selected_objects:
        if _is_global(space):
            rotations.append(obj.matrix_world.to_euler(_EULER_ORDER))
        else:
            # matrix_basis = what the N-panel edits (no parent inverse, no parenting).
            rotations.append(obj.matrix_basis.to_euler(_EULER_ORDER))
    return rotations


def selected_scales(context: bpy.types.Context, space: str) -> list[Vector]:
    """Object scales. Empty in mesh edit mode."""
    if context.mode != "OBJECT":
        return []

    scales: list[Vector] = []
    for obj in context.selected_objects:
        if _is_global(space):
            _loc, _rot, scale = obj.matrix_world.decompose()
            scales.append(scale)
        else:
            scales.append(obj.scale.copy())
    return scales


def axis_display(
    values: list[float],
    *,
    eps: float = _AXIS_EPS,
) -> AxisDisplay:
    if not values:
        return AxisDisplay(has_selection=False, uniform=True, value=0.0)

    first = values[0]
    for value in values[1:]:
        if abs(value - first) > eps:
            return AxisDisplay(has_selection=True, uniform=False, value=0.0)

    return AxisDisplay(has_selection=True, uniform=True, value=first)


def _too_many_selected_verts(context: bpy.types.Context) -> bool:
    if context.mode != "EDIT_MESH":
        return False
    total = sum(
        obj.data.total_vert_sel for obj in context.objects_in_mode_unique_data if obj.type == "MESH"
    )
    return total > _SCAN_LIMIT


def get_channel_displays(
    context: bpy.types.Context,
    space: str,
    channel: Channel,
) -> tuple[AxisDisplay, AxisDisplay, AxisDisplay]:
    if channel == "LOC":
        if _too_many_selected_verts(context):
            mixed = AxisDisplay(has_selection=True, uniform=False)
            return (mixed, mixed, mixed)
        vectors = selected_locations(context, space)
        return (
            axis_display([v.x for v in vectors]),
            axis_display([v.y for v in vectors]),
            axis_display([v.z for v in vectors]),
        )

    if channel == "ROT":
        eulers = selected_rotations(context, space)
        return (
            axis_display([e.x for e in eulers], eps=_ROT_EPS),
            axis_display([e.y for e in eulers], eps=_ROT_EPS),
            axis_display([e.z for e in eulers], eps=_ROT_EPS),
        )

    vectors = selected_scales(context, space)
    return (
        axis_display([v.x for v in vectors]),
        axis_display([v.y for v in vectors]),
        axis_display([v.z for v in vectors]),
    )


def set_edit_mesh_location_axis(
    context: bpy.types.Context,
    axis: AxisIndex,
    value: float,
    space: str,
) -> int:
    count = 0
    for obj, bm in iter_edit_meshes(context):
        matrix = obj.matrix_world
        inverse = matrix.inverted_safe()
        changed = False

        for vert in bm.verts:
            if not vert.select:
                continue

            if _is_global(space):
                world = matrix @ vert.co
                world[axis] = value
                vert.co = inverse @ world
            else:
                vert.co[axis] = value

            changed = True
            count += 1

        if changed:
            bmesh.update_edit_mesh(obj.data)

    return count


def set_object_location_axis(
    context: bpy.types.Context,
    axis: AxisIndex,
    value: float,
    space: str,
) -> int:
    count = 0
    for obj in context.selected_objects:
        if _is_global(space):
            matrix = obj.matrix_world.copy()
            translation = matrix.translation.copy()
            translation[axis] = value
            matrix.translation = translation
            obj.matrix_world = matrix
        else:
            location = obj.location.copy()
            location[axis] = value
            obj.location = location
        count += 1
    return count


def set_object_rotation_axis(
    context: bpy.types.Context,
    axis: AxisIndex,
    value: float,
    space: str,
) -> int:
    """Set one XYZ-Euler rotation axis (radians) on every selected object."""
    count = 0
    for obj in context.selected_objects:
        if _is_global(space):
            matrix = obj.matrix_world.copy()
            loc, rot_q, scale = matrix.decompose()
            euler = rot_q.to_euler(_EULER_ORDER)
            euler[axis] = value
            obj.matrix_world = Matrix.LocRotScale(loc, euler.to_quaternion(), scale)
        else:
            loc, rot_q, scale = obj.matrix_basis.decompose()
            euler = rot_q.to_euler(_EULER_ORDER)
            euler[axis] = value
            # The setter writes into the object's own rotation mode.
            obj.matrix_basis = Matrix.LocRotScale(loc, euler.to_quaternion(), scale)
        count += 1
    return count


def set_object_scale_axis(
    context: bpy.types.Context,
    axis: AxisIndex,
    value: float,
    space: str,
) -> int:
    count = 0
    for obj in context.selected_objects:
        if _is_global(space):
            matrix = obj.matrix_world.copy()
            loc, rot_q, scale = matrix.decompose()
            scale[axis] = value
            obj.matrix_world = Matrix.LocRotScale(loc, rot_q, scale)
        else:
            scale = obj.scale.copy()
            scale[axis] = value
            obj.scale = scale
        count += 1
    return count


def set_channel_axis(
    context: bpy.types.Context,
    channel: Channel,
    axis: AxisIndex,
    value: float,
    space: str,
) -> int:
    if channel == "LOC":
        if context.mode == "EDIT_MESH":
            return set_edit_mesh_location_axis(context, axis, value, space)
        if context.mode == "OBJECT":
            return set_object_location_axis(context, axis, value, space)
        return 0

    if context.mode != "OBJECT":
        return 0

    if channel == "ROT":
        return set_object_rotation_axis(context, axis, value, space)
    if channel == "SCALE":
        return set_object_scale_axis(context, axis, value, space)
    return 0
