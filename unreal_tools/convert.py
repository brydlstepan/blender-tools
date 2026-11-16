"""Pure conversions between Blender and Unreal (no bpy, so they can be unit-tested)."""

from __future__ import annotations


def to_unreal_location(x: float, y: float, z: float, unit_scale: float) -> list[float]:
    """Blender position -> Unreal. Unreal's FBX import maps Blender X/Y/Z to X/-Y/Z.

    unit_scale is Blender units -> Unreal units (100 per metre, times the scene's scale).
    """
    return [x * unit_scale, -y * unit_scale, z * unit_scale]


def to_unreal_rotation(x: float, y: float, z: float, w: float) -> list[float]:
    """Blender quaternion -> Unreal quaternion [x, y, z, w].

    Mirroring Y turns rotation R into M R M, i.e. the quaternion (w, x, y, z)
    becomes (w, -x, y, -z).
    """
    return [-x, y, -z, w]


def collapse_slashes(path: str) -> str:
    """Collapse "a//b" to "a/b", keeping a leading "//" (blend-relative path).

    An empty {collection} token leaves a double slash behind.
    """
    prefix, rest = ("//", path[2:]) if path.startswith("//") else ("", path)
    while "//" in rest:
        rest = rest.replace("//", "/")
    return prefix + rest
