"""Shared FBX export helpers: one object at a time, optional pivot-to-origin."""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterable, Iterator

import bpy
from bpy.types import Context, Object
from mathutils import Matrix, Vector

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip(" .")
    return cleaned or "object"


def ensure_object_mode(context: Context) -> None:
    if context.mode == "OBJECT":
        return
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")


def iter_export_roots(
    objects: Iterable[Object],
    *,
    mesh_only: bool,
) -> list[Object]:
    roots: list[Object] = []
    for obj in objects:
        if mesh_only and obj.type != "MESH":
            continue
        roots.append(obj)
    # Stable, predictable order
    roots.sort(key=lambda o: o.name_full)
    return roots


def _select_for_export(obj: Object, *, include_children: bool) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    if include_children:
        for child in obj.children_recursive:
            child.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _move_origin_to_world_zero(obj: Object) -> Vector:
    """Shift obj so its origin (pivot) sits at world 0; returns the original location."""
    saved = obj.location.copy()
    if obj.parent is None:
        obj.location = (0.0, 0.0, 0.0)
    else:
        # location is expressed in parent space: world = parent @ parent_inverse @ location
        parent_space = obj.parent.matrix_world @ obj.matrix_parent_inverse
        obj.location = parent_space.inverted_safe() @ Vector((0.0, 0.0, 0.0))
    return saved


def _save_local_transform(obj: Object) -> tuple:
    return (
        obj.location.copy(),
        obj.rotation_euler.copy(),
        obj.rotation_quaternion.copy(),
        tuple(obj.rotation_axis_angle),
        obj.scale.copy(),
    )


def _restore_local_transform(obj: Object, saved: tuple) -> None:
    obj.location, obj.rotation_euler, obj.rotation_quaternion, axis_angle, obj.scale = saved
    obj.rotation_axis_angle = axis_angle


def _move_to_world_origin_keep_scale(obj: Object) -> None:
    """Pivot to world 0 and world rotation to identity; the world scale stays (gets baked)."""
    _loc, _rot, scale = obj.matrix_world.decompose()
    obj.matrix_world = Matrix.Diagonal(scale).to_4x4()


@contextmanager
def preserved_selection(context: Context) -> Iterator[None]:
    """Restore selection and active object after exporting one object at a time."""
    previous_selection = list(context.selected_objects)
    previous_active = context.view_layer.objects.active
    try:
        yield
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in previous_selection:
            try:
                obj.select_set(True)
            except ReferenceError:
                continue
        if previous_active is not None:
            try:
                context.view_layer.objects.active = previous_active
            except ReferenceError:
                pass


def export_object_fbx(
    context: Context,
    obj: Object,
    file_path: str,
    *,
    include_children: bool = False,
    center_at_origin: bool = True,
    reset_rotation: bool = False,
    global_scale: float = 1.0,
    axis_forward: str = "-Z",
    axis_up: str = "Y",
    use_mesh_modifiers: bool = True,
    mesh_smooth_type: str = "SMOOTH_GROUP",
    bake_anim: bool = False,
) -> bool:
    """Export obj (and optionally its children) to file_path; selection is left changed.

    center_at_origin: pivot at world 0. reset_rotation (with center_at_origin): also clear
    the world rotation, so only scale is baked into the mesh.
    """
    _select_for_export(obj, include_children=include_children)

    saved = None
    if center_at_origin:
        saved = _save_local_transform(obj)
        if reset_rotation:
            _move_to_world_origin_keep_scale(obj)
        else:
            _move_origin_to_world_zero(obj)
        context.view_layer.update()

    try:
        # Critical: always use_selection=True so each FBX gets only the
        # currently selected object(s), not the whole scene.
        result = bpy.ops.export_scene.fbx(
            filepath=file_path,
            check_existing=False,
            use_selection=True,
            use_active_collection=False,
            global_scale=global_scale,
            apply_unit_scale=True,
            apply_scale_options="FBX_SCALE_NONE",
            bake_space_transform=False,
            object_types={"EMPTY", "CAMERA", "LIGHT", "ARMATURE", "MESH", "OTHER"},
            use_mesh_modifiers=use_mesh_modifiers,
            use_mesh_modifiers_render=True,
            mesh_smooth_type=mesh_smooth_type,
            use_subsurf=False,
            use_mesh_edges=False,
            use_tspace=False,
            use_custom_props=False,
            add_leaf_bones=True,
            primary_bone_axis="Y",
            secondary_bone_axis="X",
            use_armature_deform_only=False,
            armature_nodetype="NULL",
            bake_anim=bake_anim,
            bake_anim_use_all_bones=True,
            bake_anim_use_nla_strips=True,
            bake_anim_use_all_actions=True,
            bake_anim_force_startend_keying=True,
            bake_anim_step=1.0,
            bake_anim_simplify_factor=1.0,
            path_mode="AUTO",
            embed_textures=False,
            batch_mode="OFF",
            use_batch_own_dir=True,
            use_metadata=True,
            axis_forward=axis_forward,
            axis_up=axis_up,
        )
    finally:
        if saved is not None:
            _restore_local_transform(obj, saved)
            context.view_layer.update()

    return result == {"FINISHED"}
