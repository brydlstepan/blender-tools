# Blender Tools plan

What is still to do for Blender Tools. Finished work is removed from this file, not kept.

## To do

Unreal bridge (ideas from reviewing Epic's Send to Unreal; borrow the ideas, not their in-Unreal RPC server or error handling):

1. **Keep Blender responsive during an Unreal import.** Import / Reimport blocks Blender's main thread for up to 10 minutes. Export the FBX files on the main thread, run the socket work in a worker thread, poll its result from a `bpy.app.timers` callback, and show progress with a way to cancel. Needs a running Unreal Editor to test.
2. **Optional Unreal features, each separate.** Import materials and textures toggle, collision shapes from a naming convention (`UCX_`), LODs, and sockets from Blender empties (replace a same-named socket so reimport stays idempotent).

Other:

3. **Check the install in Blender 5.2.** Drag the button from the Page onto Blender, confirm both steps (repository, then add-on) work, and enable the add-on.
4. **Check the Page in a browser.** Look at the button, spacing and dark mode against blender.org.
5. **Update the GitHub Actions versions.** `actions/cache`, `checkout`, `upload-artifact` and `deploy-pages` still target Node.js 20, which GitHub is deprecating.
6. **Optional: bundle the Inter font** on the Page, so the text matches blender.org exactly instead of falling back to the system font.

## Open questions

- Publish to extensions.blender.org later?
