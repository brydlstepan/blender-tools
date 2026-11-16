"""Exact clear-parent helpers (quaternion LRS + shear bake)."""

from __future__ import annotations

from collections.abc import Iterable

import bpy
from mathutils import Matrix

# Object types whose geometry can absorb non-LRS residual (shear from scaled parents).
_GEOMETRY_TYPES = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "LATTICE"})
_SHEAR_EPS = 1e-7


def objects_with_parents(objects: Iterable[bpy.types.Object]) -> list[bpy.types.Object]:
    return [obj for obj in objects if obj.parent is not None]


def _matrix_near_identity(matrix: Matrix, *, eps: float = _SHEAR_EPS) -> bool:
    identity = Matrix.Identity(4)
    for row in range(4):
        for col in range(4):
            if abs(matrix[row][col] - identity[row][col]) > eps:
                return False
    return True


def _ensure_unique_data(obj: bpy.types.Object) -> None:
    """Avoid transforming shared datablocks when baking residual shear."""
    data = obj.data
    if data is None or data.users <= 1:
        return
    obj.data = data.copy()


def _clear_delta_transforms(obj: bpy.types.Object) -> None:
    obj.delta_location = (0.0, 0.0, 0.0)
    obj.delta_rotation_euler = (0.0, 0.0, 0.0)
    obj.delta_rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    obj.delta_scale = (1.0, 1.0, 1.0)


def _apply_world_matrix_quaternion(obj: bpy.types.Object, world: Matrix) -> bool:
    """Apply world matrix via quaternion LRS; bake leftover shear into geometry.

    Returns True if geometry was modified to absorb residual shear.
    """
    obj.rotation_mode = "QUATERNION"
    _clear_delta_transforms(obj)

    loc, rot, scale = world.decompose()
    lrs = Matrix.LocRotScale(loc, rot, scale)
    residual = lrs.inverted() @ world

    obj.location = loc
    obj.rotation_quaternion = rot
    obj.scale = scale
    obj.matrix_parent_inverse.identity()

    if _matrix_near_identity(residual):
        return False

    if obj.type not in _GEOMETRY_TYPES or obj.data is None:
        return False

    _ensure_unique_data(obj)
    obj.data.transform(residual)
    if hasattr(obj.data, "update"):
        obj.data.update()
    return True


def clear_parents_keep_world(
    objects: Iterable[bpy.types.Object],
    *,
    view_layer: bpy.types.ViewLayer | None = None,
) -> tuple[int, int]:
    """Clear parents while preserving world appearance (quaternion method).

    1. Snapshot every target's ``matrix_world``
    2. Clear all parents
    3. Re-apply via quaternion location/rotation/scale
    4. Bake leftover shear into mesh/curve data when needed

    Returns ``(cleared_count, geometry_baked_count)``.
    """
    targets = objects_with_parents(objects)
    if not targets:
        return (0, 0)

    if view_layer is not None:
        view_layer.update()

    world_matrices: dict[bpy.types.Object, Matrix] = {
        obj: obj.matrix_world.copy() for obj in targets
    }

    for obj in targets:
        obj.parent = None
        obj.parent_type = "OBJECT"
        obj.parent_bone = ""
        obj.matrix_parent_inverse.identity()
        _clear_delta_transforms(obj)

    if view_layer is not None:
        view_layer.update()

    baked = 0
    for obj, matrix in world_matrices.items():
        if _apply_world_matrix_quaternion(obj, matrix):
            baked += 1

    if view_layer is not None:
        view_layer.update()

    return (len(targets), baked)
