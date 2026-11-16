"""Blender Tools: custom viewport modules for Blender 5.2+."""

from . import extended_tools, separate_fbx_export, unreal_tools

modules = (
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
