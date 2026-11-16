"""Tests for the code that does not need Blender. Run: python -m unittest discover tests"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


convert = _load("convert", "unreal_tools/convert.py")
remote = _load("remote", "unreal_tools/remote.py")


class ConvertTests(unittest.TestCase):
    def test_location_flips_y_and_scales(self):
        self.assertEqual(convert.to_unreal_location(1, 2, 3, 100), [100, -200, 300])

    def test_rotation_flips_x_and_z(self):
        self.assertEqual(convert.to_unreal_rotation(0.1, 0.2, 0.3, 0.9), [-0.1, 0.2, -0.3, 0.9])

    def test_identity_rotation_stays_identity(self):
        self.assertEqual(convert.to_unreal_rotation(0, 0, 0, 1), [0, 0, 0, 1])

    def test_collapse_keeps_leading_blend_relative_marker(self):
        self.assertEqual(convert.collapse_slashes("//../src/3D/Level//"), "//../src/3D/Level/")
        self.assertEqual(convert.collapse_slashes("//a///b"), "//a/b")

    def test_collapse_plain_path(self):
        self.assertEqual(convert.collapse_slashes("C:/a//b"), "C:/a/b")


class WireFormatTests(unittest.TestCase):
    def test_roundtrip(self):
        raw = remote._encode("ping", "node-1", "node-2", {"k": "v"})
        message = remote._decode(raw)
        self.assertEqual(message["type"], "ping")
        self.assertEqual(message["dest"], "node-2")
        self.assertEqual(message["data"], {"k": "v"})

    def test_rejects_wrong_magic_version_and_garbage(self):
        good = json.loads(remote._encode("ping", "a"))
        self.assertIsNone(remote._decode(json.dumps({**good, "magic": "x"}).encode()))
        self.assertIsNone(remote._decode(json.dumps({**good, "version": 99}).encode()))
        self.assertIsNone(remote._decode(b"not json"))
        self.assertIsNone(remote._decode(b"[1, 2]"))

    def test_partial_message_does_not_parse(self):
        raw = remote._encode("command_result", "a", data={"success": True})
        self.assertIsNone(remote._decode(raw[:-5]))


if __name__ == "__main__":
    unittest.main()
