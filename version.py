"""Add-on version, read from blender_manifest.toml (the only place it is written)."""

import tomllib
from pathlib import Path


def _read() -> str:
    try:
        with open(Path(__file__).with_name("blender_manifest.toml"), "rb") as handle:
            return "v" + tomllib.load(handle)["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "v?"


VERSION = _read()
