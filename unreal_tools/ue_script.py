"""Runs inside Unreal Editor (sent by Unreal Tools > Import / Reimport), never in Blender.

Expects a global ``BLENDER_TOOLS_DATA`` pointing at the JSON job written by Blender:
    {"items": [{"name", "key", "file", "asset_name", "location"}], "options": {...}}

1. For each FBX, look for an existing mesh asset whose import source is that exact file
   (asset registry) and reimport it in place; otherwise import into the active
   Content Browser folder.
2. Optionally place one actor per mesh and/or build a Blueprint actor holding all meshes.
Prints one ``BLENDER_TOOLS_RESULT`` JSON line for Blender.
"""

import json
import os

import unreal

RESULT_PREFIX = "BLENDER_TOOLS_RESULT "
MESH_TAG = "BlenderTools:"
ACTOR_TAG = "BlenderTools.Actor:"
FALLBACK_FOLDER = "/Game/BlenderTools"
ZERO_ROTATION = unreal.Rotator(0.0, 0.0, 0.0)
ZERO_LOCATION = unreal.Vector(0.0, 0.0, 0.0)


def _blender_transform(item, restore):
    """(location, rotation) from Blender, or the origin when world positions are not kept."""
    if not restore:
        return ZERO_LOCATION, ZERO_ROTATION
    x, y, z, w = item.get("rotation", (0.0, 0.0, 0.0, 1.0))
    return unreal.Vector(*item["location"]), unreal.Quat(x, y, z, w).rotator()


def _norm(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


# ------------------------------------------------------------------- assets


def _content_browser_folder():
    try:
        path = unreal.EditorUtilityLibrary.get_current_content_browser_path()
    except AttributeError:
        path = None
    if isinstance(path, tuple):
        path = path[-1] if path and path[0] else None
    if not path:
        return FALLBACK_FOLDER
    path = str(path)
    if path.startswith("/All/"):
        path = path[len("/All"):]
    return path.rstrip("/") or FALLBACK_FOLDER


def _registry_assets(class_names):
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    asset_filter = unreal.ARFilter(
        class_paths=[unreal.TopLevelAssetPath("/Script/Engine", name) for name in class_names],
        package_paths=["/Game"],
        recursive_paths=True,
    )
    return registry.get_assets(asset_filter)


def _source_files(asset):
    import_data = asset.get_editor_property("asset_import_data")
    if import_data is None:
        return []
    return [str(f) for f in import_data.extract_filenames() if f]


def _find_existing_meshes(items):
    """Map normalised FBX path -> mesh asset previously imported from exactly that file."""
    wanted = {}
    for item in items:
        wanted.setdefault(os.path.basename(item["file"]).lower(), set()).add(_norm(item["file"]))

    found = {}
    for asset_data in _registry_assets(["StaticMesh", "SkeletalMesh"]):
        tag = asset_data.get_tag_value("AssetImportData")
        if not tag:
            continue
        tag = tag.replace("\\\\", "/").replace("\\", "/").lower()
        # Cheap pre-filter on the file name, then verify the resolved source path.
        if not any(name in tag for name in wanted):
            continue
        asset = asset_data.get_asset()
        if asset is None:
            continue
        for source in _source_files(asset):
            source = _norm(source)
            if source in wanted.get(os.path.basename(source).lower(), ()) and source not in found:
                found[source] = asset
    return found


def _fbx_options(import_materials=True):
    options = unreal.FbxImportUI()
    options.set_editor_property("import_mesh", True)
    options.set_editor_property("import_materials", import_materials)
    options.set_editor_property("import_textures", import_materials)
    options.set_editor_property("import_animations", False)
    options.set_editor_property("automated_import_should_detect_type", True)
    mesh_data = options.get_editor_property("static_mesh_import_data")
    # One FBX -> one mesh asset with the object's rotation/scale baked in
    # (Blender already moved the pivot to 0).
    mesh_data.set_editor_property("combine_meshes", True)
    mesh_data.set_editor_property("transform_vertex_to_absolute", True)
    mesh_data.set_editor_property("bake_pivot_in_vertex", False)
    return options


def _material_slots_property(mesh):
    return "static_materials" if isinstance(mesh, unreal.StaticMesh) else "materials"


def _assigned_materials(mesh):
    """Slot name -> material for every slot that has a material set."""
    assigned = {}
    for slot in mesh.get_editor_property(_material_slots_property(mesh)):
        material = slot.get_editor_property("material_interface")
        if material is not None:
            assigned[str(slot.get_editor_property("material_slot_name"))] = material
    return assigned


def _has_empty_slot(mesh):
    return any(
        slot.get_editor_property("material_interface") is None
        for slot in mesh.get_editor_property(_material_slots_property(mesh))
    )


def _restore_materials(mesh, assigned):
    """Put back materials that were set before the reimport (matched by slot name)."""
    if not assigned:
        return
    prop = _material_slots_property(mesh)
    slots = list(mesh.get_editor_property(prop))
    changed = False
    for slot in slots:
        previous = assigned.get(str(slot.get_editor_property("material_slot_name")))
        if previous is not None and slot.get_editor_property("material_interface") != previous:
            slot.set_editor_property("material_interface", previous)
            changed = True
    if changed:
        mesh.set_editor_property(prop, slots)


def _import_meshes(items, folder, result):
    existing = _find_existing_meshes(items)
    tasks = []
    reimports = []
    kept_materials = []
    for item in items:
        previous = existing.get(_norm(item["file"]))
        reimports.append(previous is not None)
        kept_materials.append(_assigned_materials(previous) if previous is not None else {})
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", item["file"])
        if previous is not None:
            # Reimport over the asset wherever it lives now (it may have been moved/renamed).
            task.set_editor_property("destination_path", previous.get_path_name().rsplit("/", 1)[0])
            task.set_editor_property("destination_name", previous.get_name())
        else:
            task.set_editor_property("destination_path", folder)
            task.set_editor_property("destination_name", item["asset_name"])
        task.set_editor_property("replace_existing", True)
        task.set_editor_property("replace_existing_settings", False)
        task.set_editor_property("automated", True)
        task.set_editor_property("save", False)
        # Reimport: keep materials already set on the mesh; only bring in materials
        # (and textures) when some slot is still empty.
        import_materials = previous is None or _has_empty_slot(previous)
        task.set_editor_property("options", _fbx_options(import_materials))
        tasks.append(task)

    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)

    meshes = []
    for task, reimport, assigned in zip(tasks, reimports, kept_materials):
        mesh = None
        for path in task.get_editor_property("imported_object_paths") or []:
            asset = unreal.load_asset(str(path))
            if isinstance(asset, (unreal.StaticMesh, unreal.SkeletalMesh)):
                mesh = asset
                break
        if mesh is not None:
            if reimport:
                _restore_materials(mesh, assigned)
            result["reimported" if reimport else "imported"] += 1
        meshes.append(mesh)
    return meshes


# ------------------------------------------------------------ level actors


def _tagged_actors(actor_subsystem, prefix):
    actors = {}
    for actor in actor_subsystem.get_all_level_actors():
        for tag in actor.get_editor_property("tags"):
            tag = str(tag)
            if tag.startswith(prefix):
                actors[tag[len(prefix):]] = actor
    return actors


def _assign_mesh(actor, mesh):
    if isinstance(mesh, unreal.StaticMesh) and isinstance(actor, unreal.StaticMeshActor):
        actor.get_editor_property("static_mesh_component").set_static_mesh(mesh)
        return True
    if isinstance(mesh, unreal.SkeletalMesh) and isinstance(actor, unreal.SkeletalMeshActor):
        actor.get_editor_property("skeletal_mesh_component").set_skinned_asset_and_update(mesh)
        return True
    return False


def _place_meshes(items, meshes, options, actor_subsystem, result):
    existing = _tagged_actors(actor_subsystem, MESH_TAG)
    restore = options["restore_world_positions"]
    for item, mesh in zip(items, meshes):
        if mesh is None:
            continue
        location, rotation = _blender_transform(item, restore)
        actor = existing.get(item["key"])
        if actor is not None and not _assign_mesh(actor, mesh):
            # Mesh type changed (static <-> skeletal): replace the actor.
            actor_subsystem.destroy_actor(actor)
            actor = None

        if actor is None:
            actor = actor_subsystem.spawn_actor_from_object(mesh, location, rotation)
            if actor is None:
                result["failed"].append(item["name"])
                continue
            actor.set_editor_property("tags", [unreal.Name(MESH_TAG + item["key"])])
            actor.set_actor_label(item["name"])
            result["placed"] += 1
        else:
            result["moved"] += 1

        if restore:
            # Scale is baked into the mesh; location and rotation live on the actor.
            actor.set_actor_location_and_rotation(location, rotation, False, True)
            actor.set_actor_scale3d(unreal.Vector(1.0, 1.0, 1.0))


# --------------------------------------------------------- blueprint actor


def _find_or_create_blueprint(name, folder):
    for asset_data in _registry_assets(["Blueprint"]):
        if str(asset_data.asset_name) == name:
            return asset_data.get_asset()
    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", unreal.Actor)
    return unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.Blueprint, factory)


def _blueprint_components(subsystem, blueprint):
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    handles = subsystem.k2_gather_subobject_data_for_blueprint(blueprint)
    components = {}
    for handle in handles:
        data = lib.get_data(handle)
        component = lib.get_object_for_blueprint(data, blueprint)
        if isinstance(component, unreal.MeshComponent):
            components[str(lib.get_variable_name(data))] = (component, data)
    return handles[0], components


def _add_component(subsystem, blueprint, root_handle, component_class, name):
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    params = unreal.AddNewSubobjectParams(
        parent_handle=root_handle,
        new_class=component_class,
        blueprint_context=blueprint,
    )
    handle, _fail_reason = subsystem.add_new_subobject(params)
    if not lib.is_handle_valid(handle):
        return None, None
    subsystem.rename_subobject(handle, unreal.Text(name))
    data = lib.get_data(handle)
    return lib.get_object_for_blueprint(data, blueprint), data


def _relative_transform(component):
    return unreal.Transform(
        component.get_editor_property("relative_location"),
        component.get_editor_property("relative_rotation"),
        component.get_editor_property("relative_scale3d"),
    )


def _set_actor_space_transform(blueprint, data, component, location, rotation):
    """Place component at `location`/`rotation` in actor space (= world space for an actor at 0),
    whatever components the user parented it under in the Blueprint hierarchy."""
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    if lib.is_root_component(data):
        return  # the root's transform is the actor's own transform

    # Parent chain up to (not including) the root component.
    chain = []
    current = data
    while True:
        parent_handle = lib.get_parent_handle(current)
        if not lib.is_handle_valid(parent_handle):
            break
        parent_data = lib.get_data(parent_handle)
        parent = lib.get_object_for_blueprint(parent_data, blueprint)
        if not isinstance(parent, unreal.SceneComponent) or lib.is_root_component(parent_data):
            break
        chain.append(parent)
        current = parent_data

    parent_to_actor = unreal.Transform()
    for parent in reversed(chain):  # top-most first
        parent_to_actor = unreal.MathLibrary.compose_transforms(_relative_transform(parent), parent_to_actor)

    # Scale is baked into the mesh, so in actor space it is location + rotation only.
    target = unreal.Transform(location, rotation, unreal.Vector(1.0, 1.0, 1.0))
    relative = unreal.MathLibrary.make_relative_transform(target, parent_to_actor)
    component.set_editor_property("relative_location", relative.translation)
    component.set_editor_property("relative_rotation", relative.rotation.rotator())
    component.set_editor_property("relative_scale3d", relative.scale3d)


def _build_actor(items, meshes, options, folder, actor_subsystem, result):
    name = options["actor_name"]
    blueprint = _find_or_create_blueprint(name, folder)
    if blueprint is None:
        result["failed"].append(name)
        return

    restore = options["restore_world_positions"]
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    root_handle, components = _blueprint_components(subsystem, blueprint)

    for item, mesh in zip(items, meshes):
        if mesh is None:
            continue
        is_static = isinstance(mesh, unreal.StaticMesh)
        component_class = unreal.StaticMeshComponent if is_static else unreal.SkeletalMeshComponent
        component, data = components.get(item["asset_name"], (None, None))
        if component is not None and not isinstance(component, component_class):
            component = None  # type changed: add a fresh component next to the old one
        new_component = component is None
        if new_component:
            component, data = _add_component(subsystem, blueprint, root_handle, component_class, item["asset_name"])
            if component is None:
                result["failed"].append(item["name"])
                continue

        component.set_editor_property("static_mesh" if is_static else "skeletal_mesh_asset", mesh)
        # Keep World Positions: match Blender in actor space even if the user re-parented
        # components in the Blueprint. Off: new components at 0, existing ones untouched.
        if restore or new_component:
            location, rotation = _blender_transform(item, restore)
            _set_actor_space_transform(blueprint, data, component, location, rotation)

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    result["actor"] = blueprint.get_path_name().split(".")[0]

    # Placing the actor is part of "Create Actor".
    placed = _tagged_actors(actor_subsystem, ACTOR_TAG).get(name)
    if placed is None:
        actor_class = unreal.BlueprintEditorLibrary.generated_class(blueprint)
        placed = actor_subsystem.spawn_actor_from_class(actor_class, unreal.Vector(0.0, 0.0, 0.0), ZERO_ROTATION)
        if placed is not None:
            placed.set_editor_property("tags", [unreal.Name(ACTOR_TAG + name)])
            placed.set_actor_label(name)
            result["placed"] += 1
    elif restore:
        placed.set_actor_location_and_rotation(unreal.Vector(0.0, 0.0, 0.0), ZERO_ROTATION, False, True)
        placed.set_actor_scale3d(unreal.Vector(1.0, 1.0, 1.0))
        result["moved"] += 1


# --------------------------------------------------------------------- main


def run(data_path):
    with open(data_path, encoding="utf-8") as handle:
        job = json.load(handle)

    items = job["items"]
    options = job["options"]
    folder = _content_browser_folder()
    result = {
        "folder": folder,
        "imported": 0,
        "reimported": 0,
        "placed": 0,
        "moved": 0,
        "actor": None,
        "failed": [],
    }

    meshes = _import_meshes(items, folder, result)
    result["failed"].extend(item["name"] for item, mesh in zip(items, meshes) if mesh is None)

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    with unreal.ScopedEditorTransaction("Blender Tools: Import / Reimport"):
        if options["place_meshes"]:
            _place_meshes(items, meshes, options, actor_subsystem, result)
        if options["create_actor"]:
            _build_actor(items, meshes, options, folder, actor_subsystem, result)

    print(RESULT_PREFIX + json.dumps(result))


run(BLENDER_TOOLS_DATA)  # noqa: F821 - injected by the Blender add-on
