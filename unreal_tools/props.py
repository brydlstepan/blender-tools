"""Per-scene Unreal Tools settings (saved with the .blend)."""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import Object, PropertyGroup, Scene

from ..separate_fbx_export.ops import sanitize_filename

DEFAULT_EXPORT_DIR = "//../../../source/3D/{blend}/{collection}/"


def blend_name() -> str:
    return bpy.path.display_name_from_filepath(bpy.data.filepath) if bpy.data.filepath else ""


def collection_folder(obj: Object) -> str:
    """Folder name for {collection}: empty when the file has a single collection
    (or the object sits directly in the Scene Collection)."""
    if len(bpy.data.collections) <= 1:
        return ""
    for collection in obj.users_collection:
        if collection.name in bpy.data.collections:  # skip scene master collections
            return sanitize_filename(collection.name)
    return ""


def resolve_export_dir(template: str, obj: Object) -> str:
    """Expand {blend}/{collection}/{object} and make the path absolute (// = folder of the .blend)."""
    path = template.replace("{blend}", sanitize_filename(blend_name() or "untitled"))
    path = path.replace("{collection}", collection_folder(obj))
    path = path.replace("{object}", sanitize_filename(obj.name))
    # An empty token leaves "a//b": collapse it, but keep the leading "//" (blend-relative).
    prefix, rest = ("//", path[2:]) if path.startswith("//") else ("", path)
    while "//" in rest:
        rest = rest.replace("//", "/")
    return os.path.normpath(bpy.path.abspath(prefix + rest))


class BlenderToolsUnrealSettings(PropertyGroup):
    export_dir: StringProperty(
        name="Export Folder",
        description=(
            "Where FBX files are written. // = folder of the .blend; "
            "{blend} = .blend name, {collection} = object's collection "
            "(left out when the file has only one collection), {object} = object name"
        ),
        default=DEFAULT_EXPORT_DIR,
    )

    restore_world_positions: BoolProperty(
        name="Keep World Positions",
        description=(
            "Meshes are exported with their pivot at 0 and no rotation (scale is baked); give each "
            "placed actor / actor component its Blender world position and rotation so Unreal looks "
            "like Blender. Off: new ones go to 0, existing ones keep their transform in Unreal"
        ),
        default=True,
    )

    place_mode: EnumProperty(
        name="In Unreal",
        description="What to do in Unreal after importing / reimporting the assets",
        items=(
            ("NONE", "Do Nothing", "Only import / reimport the assets"),
            ("MESHES", "Place Static Meshes in Scene", "One Static Mesh actor per object in the open level"),
            (
                "ACTOR",
                "Create Actor",
                "Create (or update) a Blueprint actor holding all meshes as components and place it in the open level",
            ),
        ),
        default="ACTOR",
    )

    actor_name: StringProperty(
        name="Actor Name",
        description="Blueprint asset name; {blend} = .blend name. An existing Blueprint with this name is updated (positions in world space, so re-parented components keep their place)",
        default="BP_{blend}",
    )

    use_mesh_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Apply modifiers (preview resolution) on export",
        default=True,
    )


def register() -> None:
    bpy.utils.register_class(BlenderToolsUnrealSettings)
    Scene.blender_tools_unreal = PointerProperty(type=BlenderToolsUnrealSettings)


def unregister() -> None:
    del Scene.blender_tools_unreal
    bpy.utils.unregister_class(BlenderToolsUnrealSettings)
