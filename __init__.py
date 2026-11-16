"""Blender Tools — custom viewport modules for Blender 5.x."""

bl_info = {
    "name": "Blender Tools",
    "author": "brydlst",
    "version": (0, 5, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Extended Tools / Unreal Tools · File > Export",
    "description": "Custom viewport tools that fix awkward Blender defaults",
    "category": "3D View",
}

from . import absolute_item
from . import extended_tools
from . import separate_fbx_export
from . import unreal_tools

modules = (
    absolute_item,
    extended_tools,
    separate_fbx_export,
    unreal_tools,
)


def register() -> None:
    for module in modules:
        module.register()


def unregister() -> None:
    for module in reversed(modules):
        module.unregister()
