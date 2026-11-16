"""Extended Tools: improved parenting and related object utilities."""

from . import ops, panel


def register() -> None:
    ops.register()
    panel.register()


def unregister() -> None:
    panel.unregister()
    ops.unregister()
