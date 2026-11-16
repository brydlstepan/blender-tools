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
checks = _load("checks", "unreal_tools/checks.py")


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


class CheckTests(unittest.TestCase):
    def test_name_clash_is_an_error_and_ignores_case(self):
        problems = checks.find_asset_name_clashes([("Crate.001", "Crate_001"), ("crate_001", "crate_001"), ("Barrel", "Barrel")])
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].severity, checks.ERROR)
        self.assertIn("Crate.001", problems[0].subject)
        self.assertIn("crate_001", problems[0].subject)

    def test_no_clash_no_problem(self):
        self.assertEqual(checks.find_asset_name_clashes([("A", "A"), ("B", "B")]), [])

    def test_only_explicitly_disabled_required_plugins_are_flagged(self):
        text = json.dumps({"Plugins": [
            {"Name": "PythonScriptPlugin", "Enabled": False},
            {"Name": "EditorScriptingUtilities", "Enabled": True},
            {"Name": "SomethingElse", "Enabled": False},
        ]})
        self.assertEqual(checks.disabled_required_plugins(text), ["PythonScriptPlugin"])

    def test_plugin_check_survives_bad_input(self):
        self.assertEqual(checks.disabled_required_plugins("not json"), [])
        self.assertEqual(checks.disabled_required_plugins("[1, 2]"), [])
        self.assertEqual(checks.disabled_required_plugins("{}"), [])

    def test_project_folder_that_cannot_be_read_gives_no_problems(self):
        self.assertEqual(checks.project_plugin_problems("Z:/definitely/not/here"), [])


class MessageTests(unittest.TestCase):
    def test_timeout_message_names_the_seconds_and_the_preference(self):
        # The text a user reads when Unreal takes too long.
        source = (ROOT / "unreal_tools" / "remote.py").read_text(encoding="utf-8")
        self.assertIn("raise the timeout in the add-on preferences", source)
        self.assertIn("It may be busy", source)


if __name__ == "__main__":
    unittest.main()
