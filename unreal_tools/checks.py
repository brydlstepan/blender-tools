"""Checks run before meshes are sent to Unreal.

The helpers that need no Blender (name clashes, the .uproject plugin list) can be
unit-tested without it. The object checks take Blender objects but only import
``bpy`` when they run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

ERROR = "ERROR"
WARNING = "WARNING"

# Check ids, shown as toggles in the panel.
CHECK_GEOMETRY = "GEOMETRY"
CHECK_NAMES = "NAMES"
CHECK_MATERIALS = "MATERIALS"
CHECK_TEXTURES = "TEXTURES"
CHECK_ARMATURE = "ARMATURE"

CHECK_ITEMS = (
    (CHECK_GEOMETRY, "Geometry", "Mesh has no faces"),
    (CHECK_NAMES, "Asset Names", "Two objects would get the same Unreal asset name"),
    (CHECK_MATERIALS, "Material Slots", "Empty material slots, or faces using a slot that does not exist"),
    (CHECK_TEXTURES, "Texture Files", "Image textures whose file is missing on disk"),
    (CHECK_ARMATURE, "Armature", "Armature modifier on a mesh without vertex groups"),
)
ALL_CHECKS = frozenset(item[0] for item in CHECK_ITEMS)

# Plugins the import script relies on. Only flagged when the project file turns them off.
REQUIRED_PLUGINS = ("PythonScriptPlugin", "EditorScriptingUtilities")


@dataclass(frozen=True)
class Problem:
    severity: str
    subject: str
    message: str

    def text(self) -> str:
        return f"{self.subject}: {self.message}"


# --------------------------------------------------------------- no Blender needed


def find_asset_name_clashes(pairs: list[tuple[str, str]]) -> list[Problem]:
    """pairs = (object name, Unreal asset name). Unreal asset names ignore case."""
    by_asset: dict[str, list[str]] = {}
    for object_name, asset_name in pairs:
        by_asset.setdefault(asset_name.lower(), []).append(object_name)
    return [
        Problem(ERROR, ", ".join(sorted(names)), f"would all become the Unreal asset '{asset}'")
        for asset, names in sorted(by_asset.items())
        if len(names) > 1
    ]


def disabled_required_plugins(uproject_text: str) -> list[str]:
    """Required plugins that the .uproject explicitly turns off."""
    try:
        plugins = json.loads(uproject_text).get("Plugins", [])
    except (ValueError, AttributeError):
        return []
    return [
        entry["Name"]
        for entry in plugins
        if isinstance(entry, dict) and entry.get("Name") in REQUIRED_PLUGINS and entry.get("Enabled") is False
    ]


def project_plugin_problems(project_root: str) -> list[Problem]:
    """Read the .uproject in the editor's project folder and flag disabled required plugins."""
    try:
        names = [n for n in os.listdir(project_root) if n.lower().endswith(".uproject")]
    except OSError:
        return []  # not reachable from here (other machine, odd path): nothing to say
    problems = []
    for name in names:
        try:
            with open(os.path.join(project_root, name), encoding="utf-8") as handle:
                disabled = disabled_required_plugins(handle.read())
        except OSError:
            continue
        for plugin in disabled:
            problems.append(
                Problem(ERROR, name, f"plugin {plugin} is turned off in the project; enable it and restart Unreal")
            )
    return problems


# ------------------------------------------------------------------- Blender objects


def _material_problems(obj) -> list[Problem]:
    problems = []
    slots = list(obj.material_slots)
    for index, slot in enumerate(slots):
        if slot.material is None:
            problems.append(Problem(WARNING, obj.name, f"material slot {index + 1} is empty"))
    polygons = obj.data.polygons
    if len(polygons):
        highest = max(polygon.material_index for polygon in polygons)
        if highest >= max(len(slots), 1):  # no slots at all is fine: index 0 is the default
            problems.append(
                Problem(WARNING, obj.name, f"faces use material slot {highest + 1} but the object has {len(slots)}")
            )
    return problems


def _texture_problems(obj) -> list[Problem]:
    import bpy

    problems = []
    seen = set()
    for slot in obj.material_slots:
        material = slot.material
        if material is None or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            image = getattr(node, "image", None) if node.type == "TEX_IMAGE" else None
            if image is None or image.source != "FILE" or image.packed_file or image.name in seen:
                continue
            seen.add(image.name)
            if not os.path.exists(bpy.path.abspath(image.filepath)):
                problems.append(Problem(WARNING, obj.name, f"texture file missing: {image.filepath}"))
    return problems


def check_objects(objects, enabled=ALL_CHECKS, asset_name=lambda name: name) -> list[Problem]:
    """Problems for the objects about to be exported. Errors block the send; warnings do not."""
    problems: list[Problem] = []
    if CHECK_NAMES in enabled:
        problems += find_asset_name_clashes([(obj.name, asset_name(obj.name)) for obj in objects])
    for obj in objects:
        if CHECK_GEOMETRY in enabled and len(obj.data.polygons) == 0:
            problems.append(Problem(ERROR, obj.name, "mesh has no faces"))
            continue
        if CHECK_MATERIALS in enabled:
            problems += _material_problems(obj)
        if CHECK_TEXTURES in enabled:
            problems += _texture_problems(obj)
        if CHECK_ARMATURE in enabled and not obj.vertex_groups:
            if any(modifier.type == "ARMATURE" for modifier in obj.modifiers):
                problems.append(Problem(WARNING, obj.name, "has an Armature modifier but no vertex groups"))
    return problems
