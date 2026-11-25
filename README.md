# Blender Tools

Custom Blender **5.2+** extension modules for viewport workflows that Blender’s defaults get wrong.

## Install (Blender 5.2+)

Blender must get the **whole extension**, never the lone `__init__.py`.

- **From the project page (once published):** drag the zip link onto a Blender window, or add the repository link in Preferences → Get Extensions to get updates.
- **From disk:** Preferences → Get Extensions → top-right **▼** → **Install from Disk…** → pick the `.zip`.
- **Dev install (live edits):** copy or junction this folder to `%APPDATA%\Blender Foundation\Blender\5.2\extensions\user_default\blender_tools`, then enable **Blender Tools** in Preferences → Extensions.

Build the zip from this folder:

```bash
blender --command extension build --source-dir .
```

Preferences → Add-ons → Install on a lone `__init__.py` copies one file into `scripts\addons` and does not give a usable module.

## Development

```bash
python -m unittest discover tests
```

Tests cover the code that does not need Blender (Unreal conversions and the remote-execution wire format). The version lives only in `blender_manifest.toml`.

## Release

Pushing a version tag publishes a new version:

1. Bump `version` in `blender_manifest.toml` and commit.
2. `git tag v0.6.1 && git push origin main v0.6.1` (the tag must match the manifest, or the run fails).
3. The `Release` workflow runs the tests, builds the zip with Blender 5.2, creates a GitHub Release, and redeploys the Page at `https://brydlstepan.github.io/blender-tools/`.

One-time setup: Settings → Pages → Source: **GitHub Actions**. The repo must be public for Pages on a free plan.

## Layout

| Path | What |
| --- | --- |
| `extended_tools/` | Sidebar panel, Clear Parent, and `absolute_item/` (absolute transform fields) |
| `separate_fbx_export/` | File → Export → FBX Separate Objects |
| `unreal_tools/` | Export and import into a running Unreal Editor |
| `fbx_common.py` | FBX export helpers shared by the two export tools |
| `version.py` | Reads the version from the manifest |

## Modules

### Extended Tools (`Extended Tools` sidebar)

**Where:** 3D Viewport → Sidebar (`N`) → **Item** (an *Absolute Transform* panel under Blender's own Transform) and **Extended Tools** (parenting, version tag bottom-right)

#### Absolute Transform

Replaces the awkward default **Item → Transform** median-offset behaviour. Laid out like the default Item panel (units, drag, lock icons next to each field).

| Selection | Field shows |
| --- | --- |
| One element / all share the same axis value | That value (editable) |
| Multiple with different values on an axis | `-` (type a number to override all) |

| Channel | Modes | Notes |
| --- | --- | --- |
| Location | Object + Edit Mesh | Absolute per vertex / object |
| Rotation | Object | Degrees, XYZ Euler |
| Scale | Object | Absolute per axis |

**Space:** Global / Local · Editing a field sets **all** selected elements absolutely.

#### Parenting

| Tool | What it does |
| --- | --- |
| Clear Parent (Keep Transform) | Clears parents on all selected objects with exact world placement |

#### Loops

| Tool | What it does |
| --- | --- |
| Select Next Loops | Edit Mesh: select loop 1, skip any number of loops, then select loop 2 (last). Click the button to select every following loop at the same spacing. Redo panel `Count` limits how many (0 = to the end) |

#### Mesh

| Tool | What it does |
| --- | --- |
| Fix Quad Topology | Edit Mesh: checks quad topology and auto-fixes diagonally split quad triangles and slanted parallelogram chains (two triangles separated by slanted quads with a missing grid edge) while keeping actual UV island borders intact; selects any remaining unfixable non-quads |
| Quads from Reference | Edit Mesh (**true all-quad topology only**): select one or more clean reference edge loops or quad loops on a triangulated mesh to propagate exact quad flow across the rest of the mesh. Redo panel (`F9`): `Retriangulate Unselected Quads` (rebuild messy Tris-to-Quads areas from scratch using your reference) and `Respect UV Borders` |
| UV-Safe Un-Subdivide | Edit Mesh: un-subdivides one level on clean quads (selected faces, or all visible faces if none selected) while keeping actual UV island borders intact (odd quad spans keep one un-merged quad strip). Aborts without modifying the mesh and selects any non-quads or un-reducible faces |
| Adaptive Quad Reduce | Edit Mesh: reduces and slides quad edge loops smoothly along the surface on clean quads while keeping 100% quads and actual UV island borders. Redo panel (`F9`): `Reduction` (0–95% loops removed; 0% slides only) and `Uniformity` (1 = even arc-length spacing, 0 = dense loops on detailed/curved areas and wider loops on flat areas) |

### Separate FBX Export

Exports **each object to its own `.fbx`**.

**Where:** `File → Export → FBX Separate Objects (.fbx)`

Pick any filename in the target folder — each object is written as `{ObjectName}.fbx`.

| Option | Default | Notes |
| --- | --- | --- |
| Selection Only | on | Off = every object in the scene |
| Mesh Objects Only | on | Skip cameras/lights/empties |
| Include Children | off | Put children in the same FBX as the parent |
| Export at Origin | on | Each object's origin (pivot) becomes 0,0,0 in its FBX; set a custom pivot with *Set Origin* |
| Apply Modifiers | on | |
| Smoothing | Smoothing Groups | |
| Bake Animation | off | |

### Unreal Tools (`Unreal Tools` sidebar)

**Where:** 3D Viewport → Sidebar (`N`) → **Unreal Tools**

Exports the **selected meshes** (one FBX each, pivot at 0) and imports them into a running Unreal Editor.

**Export Folder** (saved per scene). `//` = folder of the .blend; tokens `{blend}` (blend name), `{collection}` (the object's collection — left out when the file has only one collection or the object is directly in the Scene Collection) and `{object}` (object name).
Default `//../../../source/3D/{blend}/{collection}/` → a blend at `project/projectfiles/blend/art/Level01.blend` writes
`project/source/3D/Level01/Props/Crate.fbx` (or `project/source/3D/Level01/Crate.fbx` with a single collection).

| Option | Default | What it does |
| --- | --- | --- |
| Keep World Positions | on | Pivots are at 0 (no rotation) in the assets; actors / Blueprint components get their Blender world position and rotation so Unreal matches Blender. Off: new ones go to 0, existing ones keep their transform in Unreal |
| In Unreal | Create Actor | **Do Nothing** (import / reimport only) · **Place Static Meshes in Scene** (one Static Mesh actor per object, tagged `BlenderTools:<blend>/<object>`, updated on reimport) · **Create Actor** (Blueprint `BP_{blend}` with one mesh component per object, placed once in the level; an existing Blueprint with that name is updated; with Keep World Positions, component positions are set in actor/world space, so components you re-parent under each other in the Blueprint don't drift) |
| Apply Modifiers | on | |
| Checks | all on | Before sending, looks for meshes without faces, two objects that would get the same Unreal asset name (errors, stop the send), and empty or out-of-range material slots, missing texture files, an Armature modifier without vertex groups (warnings). Each can be switched off |

The import also reads the open project's `.uproject` and stops if Python Script or Editor Scripting Utilities is turned off there. The time Blender waits for Unreal (default 600 s) is set in Preferences → Extensions → Blender Tools.

| Button | What it does |
| --- | --- |
| **Import / Reimport** | Exports, then in Unreal: a mesh asset whose import source is the **same FBX path** (found via the asset registry) is reimported in place, wherever it lives; otherwise it is imported into the **open Content Browser folder**. If several editors run, a dialog asks which one |
| Export FBX Only | Just writes the FBX files |

**Unreal setup (once per project):** Project Settings → Plugins → **Python** → enable **Remote Execution** (defaults: multicast `239.0.0.1:6766`, bind address `127.0.0.1`).

Notes:
- Each mesh is exported with its pivot at 0 and no rotation; **scale is baked** into the mesh. Actors and Blueprint components get the Blender world **position and rotation** (so pivot orientation survives).
- Reimport keeps the materials already assigned on a mesh (matched by slot name). Materials/textures are only imported again when a slot is empty (e.g. a new material slot from Blender).
- Unreal's default FBX import (Convert Scene): Blender X/Y/Z → Unreal X/−Y/Z, metres → cm.
- Assets and Blueprints are left unsaved in Unreal; use **Save All**. Undo in Unreal reverts actor placement, not the import.
