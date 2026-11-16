# Blender Tools

Custom Blender **5.2** extension modules for viewport workflows that Blender’s defaults get wrong.

## Install (Blender 5.2)

Do **not** install the lone `__init__.py` file. Blender must get the **whole folder** (manifest + package).

### Option A — Extensions (recommended)

1. Zip the **contents** of this folder (so the zip root has `blender_manifest.toml` and `__init__.py`), **or** build one:

   ```bash
   blender --command extension build --source-dir "C:\Users\brydlst\Documents\_Local\Projects\_tools\Blender_Tools"
   ```

2. Blender → **Edit → Preferences → Get Extensions** (Extensions tab)
3. Top-right **▼** → **Install from Disk…**
4. Pick the `.zip`
5. Enable **Blender Tools**

### Option B — Dev install (live edits)

Copy or junction this project into:

`%APPDATA%\Blender Foundation\Blender\5.2\extensions\user_default\blender_tools`

Then in Preferences → Extensions, enable **Blender Tools**. Restart Blender if it does not appear yet.

### Wrong path (what happened before)

**Preferences → Add-ons → Install** on `__init__.py` only copies that one file into `scripts\addons` and will **not** show a usable module.

## Modules

### Extended Tools (`Extended Tools` sidebar)

**Where:** 3D Viewport → Sidebar (`N`) → **Extended Tools** · version tag bottom-right

#### Absolute Transform

Replaces the awkward default **Item → Transform** median-offset behaviour.

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
