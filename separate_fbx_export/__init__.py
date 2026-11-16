"""Separate FBX export: one .fbx file per object."""

from . import ops


def register() -> None:
    ops.register()


def unregister() -> None:
    ops.unregister()
