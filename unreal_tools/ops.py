"""Export selected objects and import / reimport them into a running Unreal Editor."""

from __future__ import annotations

import json
import os
import re
import tempfile

import bpy
from bpy.props import EnumProperty
from bpy.types import Context, Object, Operator, Scene
from mathutils import Quaternion, Vector

from ..separate_fbx_export.ops import (
    ensure_object_mode,
    export_object_fbx,
    iter_export_roots,
    preserved_selection,
    sanitize_filename,
)
from .props import blend_name, resolve_export_dir
from .remote import RemoteExecution, RemoteExecutionError, UnrealNode

_UE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ue_script.py")
_RESULT_PREFIX = "BLENDER_TOOLS_RESULT "
_INVALID_ASSET_CHARS = re.compile(r"[^A-Za-z0-9_\-]")

# Editors found by the last discovery; Blender needs enum items kept alive in Python.
_nodes: list[UnrealNode] = []
_node_enum_items: list[tuple[str, str, str]] = []
_last_node_id: str | None = None


def unreal_asset_name(name: str) -> str:
    cleaned = _INVALID_ASSET_CHARS.sub("_", name).strip("_")
    return cleaned or "Object"


def blender_to_unreal_location(scene: Scene, location: Vector) -> list[float]:
    """World position in Blender -> Unreal centimetres (same unit handling as the FBX exporter).

    Unreal's FBX import (Convert Scene) maps Blender X/Y/Z to Unreal X/-Y/Z.
    """
    units = scene.unit_settings
    k = 1.0 if units.system == "NONE" else 100.0 * units.scale_length
    return [location.x * k, -location.y * k, location.z * k]


def blender_to_unreal_rotation(rotation: Quaternion) -> list[float]:
    """World rotation in Blender -> Unreal quaternion [x, y, z, w].

    Mirroring Y (Blender -> Unreal) turns rotation R into M R M, i.e. the quaternion
    (w, x, y, z) becomes (w, -x, y, -z).
    """
    return [-rotation.x, rotation.y, -rotation.z, rotation.w]


def _selected_meshes(context: Context) -> list[Object]:
    return iter_export_roots(context.selected_objects, mesh_only=True)


def _check_can_export(context: Context) -> str | None:
    settings = context.scene.blender_tools_unreal
    if settings.export_dir.startswith("//") and not bpy.data.filepath:
        return "Save the .blend first (export folder is relative to it)"
    if not _selected_meshes(context):
        return "Select one or more mesh objects"
    return None


def export_selected(context: Context) -> tuple[list[dict], list[str]]:
    """Export every selected mesh to its own FBX; returns (items for Unreal, failed names)."""
    settings = context.scene.blender_tools_unreal
    ensure_object_mode(context)
    blend = blend_name() or "untitled"

    items: list[dict] = []
    failed: list[str] = []
    with preserved_selection(context):
        for obj in _selected_meshes(context):
            folder = resolve_export_dir(settings.export_dir, obj)
            os.makedirs(folder, exist_ok=True)
            file_path = os.path.join(folder, f"{sanitize_filename(obj.name)}.fbx")
            world_origin, world_rotation, _scale = obj.matrix_world.decompose()

            if not export_object_fbx(
                context,
                obj,
                file_path,
                center_at_origin=True,
                reset_rotation=True,  # rotation goes on the actor, scale stays baked
                use_mesh_modifiers=settings.use_mesh_modifiers,
            ):
                failed.append(obj.name)
                continue

            items.append(
                {
                    "name": obj.name,
                    "key": f"{blend}/{obj.name}",
                    "file": file_path,
                    "asset_name": unreal_asset_name(obj.name),
                    "location": blender_to_unreal_location(context.scene, world_origin),
                    "rotation": blender_to_unreal_rotation(world_rotation),
                }
            )
    return items, failed


class BLENDERTOOLS_OT_unreal_export(Operator):
    """Export each selected mesh to its own FBX (pivot at 0) into the Unreal Tools export folder"""

    bl_idname = "blender_tools.unreal_export"
    bl_label = "Export FBX Only"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        problem = _check_can_export(context)
        if problem:
            cls.poll_message_set(problem)
        return problem is None

    def execute(self, context: Context):
        items, failed = export_selected(context)
        if failed:
            self.report({"WARNING"}, f"Exported {len(items)} FBX(s); failed: {', '.join(failed)}")
        else:
            self.report({"INFO"}, f"Exported {len(items)} FBX file(s)")
        return {"FINISHED"} if items else {"CANCELLED"}


# --------------------------------------------------------------------- import


def _instance_items(_self, _context):
    global _node_enum_items
    labels = [node.label for node in _nodes]
    _node_enum_items = []
    for node, label in zip(_nodes, labels):
        if labels.count(label) > 1:
            label = f"{label} · {node.node_id[:8]}"
        description = f"{node.project_root}\n{node.user}@{node.machine}"
        _node_enum_items.append((node.node_id, label, description))
    if not _node_enum_items:
        _node_enum_items = [("NONE", "No Unreal Editor found", "")]
    return _node_enum_items


def _discover() -> list[UnrealNode]:
    global _nodes
    with RemoteExecution() as remote:
        _nodes = remote.discover()
    return _nodes


def _write_job_file(context: Context, items: list[dict]) -> str:
    settings = context.scene.blender_tools_unreal
    blend = blend_name() or "untitled"
    job = {
        "items": items,
        "options": {
            "restore_world_positions": settings.restore_world_positions,
            "place_meshes": settings.place_mode == "MESHES",
            "create_actor": settings.place_mode == "ACTOR",
            "place_actor": settings.place_mode == "ACTOR",
            "actor_name": unreal_asset_name(settings.actor_name.replace("{blend}", blend)),
        },
    }
    path = os.path.join(tempfile.gettempdir(), "blender_tools_unreal_job.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(job, handle)
    return path


def _parse_result(result: dict) -> dict | None:
    for entry in result.get("output") or []:
        line = str(entry.get("output", "")).strip()
        if line.startswith(_RESULT_PREFIX):
            try:
                return json.loads(line[len(_RESULT_PREFIX):])
            except ValueError:
                return None
    return None


def _summary_message(summary: dict) -> str:
    parts = [f"{summary['imported']} imported to {summary['folder']}", f"{summary['reimported']} reimported"]
    if summary.get("placed") or summary.get("moved"):
        parts.append(f"{summary['placed']} placed, {summary['moved']} updated in level")
    if summary.get("actor"):
        parts.append(f"actor {summary['actor']}")
    return "Unreal: " + ", ".join(parts)


class BLENDERTOOLS_OT_unreal_import(Operator):
    """Export the selected meshes and import them into Unreal. Assets already imported from the same FBX path are reimported in place; new ones go to the open Content Browser folder"""

    bl_idname = "blender_tools.unreal_import"
    bl_label = "Import / Reimport"
    bl_options = {"REGISTER"}

    instance: EnumProperty(
        name="Unreal Editor",
        description="Running Unreal Editor to import into",
        items=_instance_items,
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        problem = _check_can_export(context)
        if problem:
            cls.poll_message_set(problem)
        return problem is None

    def invoke(self, context: Context, _event):
        try:
            nodes = _discover()
        except OSError as exc:
            self.report({"ERROR"}, f"Unreal discovery failed: {exc}")
            return {"CANCELLED"}

        if not nodes:
            self.report(
                {"ERROR"},
                "No Unreal Editor found. Enable Project Settings > Python > Enable Remote Execution",
            )
            return {"CANCELLED"}

        if len(nodes) == 1:
            self.instance = nodes[0].node_id
            return self.execute(context)

        if any(node.node_id == _last_node_id for node in nodes):
            self.instance = _last_node_id
        return context.window_manager.invoke_props_dialog(
            self, title="Import to Unreal", confirm_text="Import"
        )

    def draw(self, _context: Context) -> None:
        self.layout.prop(self, "instance")

    def execute(self, context: Context):
        global _last_node_id

        if not _nodes:
            try:
                _discover()
            except OSError as exc:
                self.report({"ERROR"}, f"Unreal discovery failed: {exc}")
                return {"CANCELLED"}
        node_id = self.instance if any(n.node_id == self.instance for n in _nodes) else None
        if node_id is None:
            if len(_nodes) != 1:
                self.report({"ERROR"}, "Pick a running Unreal Editor")
                return {"CANCELLED"}
            node_id = _nodes[0].node_id
        _last_node_id = node_id

        context.window.cursor_set("WAIT")
        try:
            items, export_failed = export_selected(context)
            if not items:
                self.report({"ERROR"}, "FBX export failed")
                return {"CANCELLED"}

            data_path = _write_job_file(context, items)
            command = (
                f"BLENDER_TOOLS_DATA = {data_path!r}\n"
                f"exec(compile(open({_UE_SCRIPT!r}, encoding='utf-8').read(), {_UE_SCRIPT!r}, 'exec'))\n"
            )
            with RemoteExecution() as remote:
                remote.connect(node_id)
                result = remote.run(command)
        except (OSError, RemoteExecutionError) as exc:
            self.report({"ERROR"}, f"Unreal: {exc}")
            return {"CANCELLED"}
        finally:
            context.window.cursor_set("DEFAULT")

        if not result.get("success"):
            error = str(result.get("result") or "").strip().splitlines()
            self.report({"ERROR"}, f"Unreal script failed: {error[-1] if error else 'unknown error'}")
            print("[Blender Tools] Unreal error:\n" + str(result.get("result")))
            return {"CANCELLED"}

        summary = _parse_result(result)
        if summary is None:
            self.report({"WARNING"}, "Sent to Unreal, but it returned no summary")
            return {"FINISHED"}

        message = _summary_message(summary)
        failed = export_failed + (summary.get("failed") or [])
        if failed:
            self.report({"WARNING"}, f"{message}; failed: {', '.join(failed)}")
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}


classes = (BLENDERTOOLS_OT_unreal_export, BLENDERTOOLS_OT_unreal_import)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
