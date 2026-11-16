# Blender Tools plan

What is still to do for Blender Tools. Finished work is removed from this file, not kept.

## To do

1. **Run the Unreal import in a thread.** Import / Reimport blocks Blender's main thread for up to 10 minutes, so big imports make Windows show "Not responding". Run the socket work in a thread and poll its result from a `bpy.app.timers` callback. Needs a running Unreal Editor to test.
2. **Check the install in Blender 5.2.** Drag the button from the Page onto Blender, confirm both steps (repository, then add-on) work, and enable the add-on.
3. **Check the Page in a browser.** Look at the button, spacing and dark mode against blender.org.
4. **Update the GitHub Actions versions.** `actions/cache`, `checkout`, `upload-artifact` and `deploy-pages` still target Node.js 20, which GitHub is deprecating.
5. **Optional: bundle the Inter font** on the Page, so the text matches blender.org exactly instead of falling back to the system font.

## Open questions

- Publish to extensions.blender.org later?
