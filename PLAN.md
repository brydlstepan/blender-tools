# Blender Tools plan

A Blender extension of viewport tools (absolute transform, clear parent, separate FBX export, Unreal import), published from GitHub with a one-page site where users drag the add-on into Blender.

## Goal

- Anyone can open the project's GitHub Page, drag the add-on into Blender, and have it installed.
- Every version tag is built and published automatically. Nobody builds or uploads a zip by hand.
- The code is tidy: one clear package layout, the audit findings fixed, a few tests on the risky parts.

## Users

- The author and teammates installing the tools in Blender 5.2+.
- Anyone who finds the repo and wants the add-on.

## Scope

**In:**

- Push to `https://github.com/brydlstepan/blender-tools.git`.
- A static GitHub Page with a drag-and-drop install link and an "add repository" link (updates inside Blender).
- A GitHub Actions workflow that builds the extension zip and redeploys the page on each version tag.
- Restructure and fix the findings from the 2026-11-16 audit (below).

**Out:**

- Publishing to extensions.blender.org (later, if wanted).
- New tools. Fixes and structure only.

## Stack and constraints

- Python 3 (`bpy`), Blender 5.2+ extension format (`blender_manifest.toml`).
- Build with `blender --command extension build`; repository index with `blender --command extension server-generate`.
- GitHub Pages (needs a public repo on a free plan) and GitHub Actions.
- No network or secrets needed in the workflow beyond the default `GITHUB_TOKEN`.

## First milestone

Push a version tag, and within a few minutes the Page shows the current version. Dragging its link onto a Blender window installs the add-on, and Blender lists the repository for updates.

## How publishing should work

1. Pushing a version tag `vX.Y.Z` runs the workflow. It downloads a pinned Blender (cached), builds the zip into `dist/`, and generates `index.json` and the HTML list with `server-generate`.
2. The workflow publishes `dist/` plus the site page (`site/index.html`) to GitHub Pages.
3. The page has two links: the zip URL (drag onto Blender to install) and a `blender://` add-repository link (Blender then offers updates).
4. The same run also makes a GitHub Release with the zip attached. The tag must match `version` in the manifest, and the workflow fails if it doesn't.

## Pipeline status

Live: tag `vX.Y.Z` builds the zip, creates a GitHub Release and deploys the Page (`site/template.html`) at https://brydlstepan.github.io/blender-tools/. One-time setup that was needed: Pages source set to GitHub Actions, and a **tag** rule `v*` on the `github-pages` environment.

## Audit findings (2026-11-16)

Status: all done in version 0.6.0 except 6 (deferred, decision 7). Also fixed along the way: Clear Parent baked float noise (1e-7) into the mesh and made its data unique.

Must fix:

1. Local rotation and scale are wrong for parented objects (`absolute_item/apply.py`): `matrix_local` includes the parent-inverse, but values are written to the plain fields.

Should fix:

2. Manifest build excludes miss `.agents/`, `.claude/`, `docs/`, `AGENTS.md`, so they would ship in the zip.
3. Edit mode rescans every selected vertex on each redraw and scene update; guard with `total_vert_sel`.
4. Move the shared FBX helpers out of `separate_fbx_export/ops.py` into a common module; move `absolute_item/` under `extended_tools/`.
5. Add tests for the Unreal position and rotation maths, export-path building, and the wire parser.
6. Unreal import blocks Blender's main thread for up to 10 minutes; run it in a thread.
7. Three version numbers (manifest, `bl_info`, `extended_tools/version.py`); keep only the manifest.
8. Clear Parent forces quaternion rotation mode; keep the user's mode via `matrix_basis`.

Nice to have:

9. Delete dead code (alias operator, unreachable `mode_set`, duplicate `place_actor`, unused `_created`), about 45 lines.
10. Use a unique temp file for the Unreal job and delete it after.
11. Fix the README build path, state one Blender version, declare manifest `permissions`.
12. Use `inverted_safe()` for edit-mode global moves.

Not checked in the audit: nothing was run in Blender or Unreal, so the FBX export arguments and the `unreal` API calls are unverified on Blender 5.2.

## Decisions

| # | Decision | Answer |
|---|---|---|
| 1 | Remote | `https://github.com/brydlstepan/blender-tools.git` |
| 2 | Hosting | GitHub Pages, deployed from Actions |
| 3 | Install method | Drag the zip link onto Blender, or add the repository link for updates |
| 4 | Build trigger | Publish only on version tags `vX.Y.Z` |
| 5 | Minimum Blender version | 5.2 (CI builds with 5.2) |
| 6 | Order of work | Restructure and fix first, then the publishing pipeline |
| 7 | Finding 6 (Unreal import in a thread) | Deferred: it can't be tested without a running Unreal Editor |

## Open questions

- Is the repo public? Pages on a free plan needs it public.
