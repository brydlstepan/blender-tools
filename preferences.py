"""Add-on preferences (Preferences > Extensions > Blender Tools)."""

import bpy
from bpy.props import IntProperty

DEFAULT_UNREAL_TIMEOUT = 600


class BlenderToolsPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    unreal_timeout: IntProperty(
        name="Unreal Timeout",
        description=(
            "Seconds to wait for Unreal to finish an import or reimport before giving up. "
            "Unreal keeps working after Blender gives up"
        ),
        default=DEFAULT_UNREAL_TIMEOUT,
        min=10,
        max=7200,
    )

    def draw(self, _context):
        self.layout.prop(self, "unreal_timeout")


def unreal_timeout(context) -> int:
    """The configured Unreal timeout in seconds (default when the preferences are not available)."""
    addon = context.preferences.addons.get(__package__)
    return addon.preferences.unreal_timeout if addon else DEFAULT_UNREAL_TIMEOUT


def register() -> None:
    bpy.utils.register_class(BlenderToolsPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(BlenderToolsPreferences)
