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


class _V:
    def __init__(self):
        self.link_edges = []
        self.link_faces = []


class _E:
    def __init__(self, a, b):
        self.verts = (a, b)
        self.link_faces = []
        a.link_edges.append(self)
        b.link_edges.append(self)

    def other_vert(self, v):
        return self.verts[1] if v is self.verts[0] else self.verts[0]


class _Loop:
    def __init__(self, vert, edge, uv=(0.0, 0.0)):
        self.vert = vert
        self.edge = edge
        self.uv = uv
        self.link_loop_next = None


class _F:
    def __init__(self, edges, verts=None, uvs=None):
        self.edges = list(edges)
        self.verts = list(verts) if verts is not None else list({v for e in edges for v in e.verts})
        for e in edges:
            e.link_faces.append(self)
        for v in self.verts:
            v.link_faces.append(self)
        self.loops = []
        if verts is not None:
            n = len(verts)
            edge_map = {frozenset(e.verts): e for e in edges}
            for i in range(n):
                a, b = verts[i], verts[(i + 1) % n]
                uv = uvs[i] if uvs is not None else (0.0, 0.0)
                self.loops.append(_Loop(a, edge_map[frozenset((a, b))], uv))
            for i in range(n):
                self.loops[i].link_loop_next = self.loops[(i + 1) % n]


def _quad_grid(cols, rows, *, wrap_u=False, wrap_v=False, uv_fn=None):
    """Build a quad grid (optionally wrapped into a cylinder or torus).

    Returns (verts_2d, vert_cols, horiz_rows, faces_2d).
    """
    vc = cols if wrap_u else cols + 1
    vr = rows if wrap_v else rows + 1
    v = [[_V() for _ in range(vc)] for _ in range(vr)]
    edge_map = {}

    def _get_edge(a, b):
        key = frozenset((a, b))
        e = edge_map.get(key)
        if e is None:
            e = _E(a, b)
            edge_map[key] = e
        return e

    faces_2d = []
    vert_cols = [set() for _ in range(vc)]
    horiz_rows = [set() for _ in range(vr)]
    for r in range(rows):
        row_faces = []
        r1 = (r + 1) % vr
        for c in range(cols):
            c1 = (c + 1) % vc
            quad_v = [v[r][c], v[r][c1], v[r1][c1], v[r1][c]]
            e_bot = _get_edge(quad_v[0], quad_v[1])
            e_right = _get_edge(quad_v[1], quad_v[2])
            e_top = _get_edge(quad_v[2], quad_v[3])
            e_left = _get_edge(quad_v[3], quad_v[0])
            horiz_rows[r].add(e_bot)
            horiz_rows[r1].add(e_top)
            vert_cols[c].add(e_left)
            vert_cols[c1].add(e_right)
            uvs = uv_fn(r, c) if uv_fn is not None else None
            row_faces.append(_F([e_bot, e_right, e_top, e_left], verts=quad_v, uvs=uvs))
        faces_2d.append(row_faces)
    return v, vert_cols, horiz_rows, faces_2d


def _subdivided_cube():
    """A 1-level subdivided cube: 26 verts (8 val-3 corners, 12 midpoints, 6 face centers), 24 quads."""
    coords = [
        (x, y, z)
        for x in (-1, 0, 1)
        for y in (-1, 0, 1)
        for z in (-1, 0, 1)
        if max(abs(x), abs(y), abs(z)) == 1
    ]
    verts = {p: _V() for p in coords}
    edges = {}

    def _edge(p, q):
        key = frozenset((p, q))
        e = edges.get(key)
        if e is None:
            e = _E(verts[p], verts[q])
            edges[key] = e
        return e

    faces = []
    for axis in range(3):
        u_ax, v_ax = [i for i in range(3) if i != axis]
        for side in (-1, 1):
            for u0 in (-1, 0):
                for v0 in (-1, 0):
                    pts = []
                    for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        p = [0, 0, 0]
                        p[axis] = side
                        p[u_ax] = u0 + du
                        p[v_ax] = v0 + dv
                        pts.append(tuple(p))
                    es = [_edge(pts[i], pts[(i + 1) % 4]) for i in range(4)]
                    faces.append(_F(es, verts=[verts[p] for p in pts]))
    return verts, faces


def _grid(cols, rows):
    """Quad grid; returns (vertical edges by column, all edges)."""
    v = [[_V() for _ in range(cols + 1)] for _ in range(rows + 1)]
    vert = {}
    horiz = {}
    for r in range(rows + 1):
        for c in range(cols):
            horiz[r, c] = _E(v[r][c], v[r][c + 1])
    for r in range(rows):
        for c in range(cols + 1):
            vert[r, c] = _E(v[r][c], v[r + 1][c])
    for r in range(rows):
        for c in range(cols):
            _F([horiz[r, c], horiz[r + 1, c], vert[r, c], vert[r, c + 1]])
    columns = [{vert[r, c] for r in range(rows)} for c in range(cols + 1)]
    return columns


class LoopTests(unittest.TestCase):
    loops = _load("loops", "extended_tools/loops.py")

    def test_skip_one_selects_every_second_loop(self):
        cols = _grid(10, 3)
        got = self.loops.next_loops(cols[1] | cols[3], history=[next(iter(cols[3]))])
        self.assertEqual(got, cols[5] | cols[7] | cols[9])

    def test_adjacent_loops_select_all_the_rest(self):
        cols = _grid(5, 2)
        got = self.loops.next_loops(cols[0] | cols[1], history=[next(iter(cols[1]))])
        self.assertEqual(got, cols[2] | cols[3] | cols[4] | cols[5])

    def test_direction_follows_first_to_second(self):
        cols = _grid(10, 2)
        got = self.loops.next_loops(cols[8] | cols[6], history=[next(iter(cols[6]))])
        self.assertEqual(got, cols[4] | cols[2] | cols[0])

    def test_count_limits_result(self):
        cols = _grid(10, 2)
        self.assertEqual(self.loops.next_loops(cols[1] | cols[3], 1, [next(iter(cols[3]))]), cols[5])

    def test_needs_exactly_two_loops(self):
        cols = _grid(6, 2)
        with self.assertRaises(ValueError):
            self.loops.next_loops(cols[1])
        with self.assertRaises(ValueError):
            self.loops.next_loops(cols[0] | cols[2] | cols[4])


class UnsubdivideTests(unittest.TestCase):
    unsub = _load("unsubdivide", "extended_tools/unsubdivide.py")

    def test_even_grid_plan(self):
        verts, _, _, faces_2d = _quad_grid(4, 4)
        all_faces = [f for row in faces_2d for f in row]
        plan = self.unsub.plan_unsubdivide(all_faces)
        self.assertEqual(
            set(plan.face_centers),
            {verts[1][1], verts[1][3], verts[3][1], verts[3][3]},
        )
        self.assertEqual(len(plan.edge_midpoints), 12)

    def test_subdivided_cube_locks_parity_from_poles_without_uvs(self):
        verts, faces = _subdivided_cube()
        plan = self.unsub.plan_unsubdivide(faces)
        expected_centers = {
            verts[(1, 0, 0)],
            verts[(-1, 0, 0)],
            verts[(0, 1, 0)],
            verts[(0, -1, 0)],
            verts[(0, 0, 1)],
            verts[(0, 0, -1)],
        }
        self.assertEqual(set(plan.face_centers), expected_centers)
        self.assertEqual(len(plan.edge_midpoints), 12)

    def test_torus_preserves_uv_border_loops_regardless_of_parity(self):
        for seam_col, seam_row in ((0, 0), (1, 1), (3, 2)):
            with self.subTest(seam_col=seam_col, seam_row=seam_row):
                def _uv(r, c, sc=seam_col, sr=seam_row):
                    u0 = (c - sc) % 8
                    v0 = (r - sr) % 8
                    return [(u0, v0), (u0 + 1, v0), (u0 + 1, v0 + 1), (u0, v0 + 1)]

                _, vcols, hrows, faces_2d = _quad_grid(8, 8, wrap_u=True, wrap_v=True, uv_fn=_uv)
                all_faces = [f for row in faces_2d for f in row]
                borders = self.unsub.uv_border_edges(all_faces, lambda loop: loop.uv)
                self.assertEqual(borders, vcols[seam_col] | hrows[seam_row])

                plan = self.unsub.plan_unsubdivide(all_faces, borders)
                self.assertEqual(len(plan.face_centers), 16)
                dissolved = {e for v in plan.face_centers for e in v.link_edges}
                self.assertTrue(borders.isdisjoint(dissolved))

    def test_uv_border_ignores_welded_edges(self):
        def _uv(r, c):
            # Continuous across the entire 4x4 grid except a vertical split at column 2.
            shift = 10.0 if c >= 2 else 0.0
            return [
                (c + shift, r),
                (c + 1 + shift, r),
                (c + 1 + shift, r + 1),
                (c + shift, r + 1),
            ]

        _, vcols, _, faces_2d = _quad_grid(4, 4, uv_fn=_uv)
        all_faces = [f for row in faces_2d for f in row]
        borders = self.unsub.uv_border_edges(all_faces, lambda loop: loop.uv)
        self.assertEqual(borders, vcols[2])

    def test_rejects_triangles_and_selects_only_non_quads(self):
        _, _, _, faces_2d = _quad_grid(4, 4)
        all_faces = [f for row in faces_2d for f in row]
        a, b, c = _V(), _V(), _V()
        tri = _F([_E(a, b), _E(b, c), _E(c, a)], verts=[a, b, c])
        with self.assertRaises(self.unsub.UnsubdivideError) as ctx:
            self.unsub.plan_unsubdivide(all_faces + [tri])
        self.assertEqual(ctx.exception.problem_faces, [tri])
        self.assertIn("triangle", str(ctx.exception))

    def test_odd_strip_between_uv_border_and_boundary_selects_only_odd_side(self):
        # 9x4 grid with UV border at col 3 -> left side is 3x4 (odd!), right side is 6x4 (even!).
        def _uv(r, c):
            shift = 10.0 if c >= 3 else 0.0
            return [
                (c + shift, r),
                (c + 1 + shift, r),
                (c + 1 + shift, r + 1),
                (c + shift, r + 1),
            ]

        _, _, _, faces_2d = _quad_grid(9, 4, uv_fn=_uv)
        all_faces = [f for row in faces_2d for f in row]
        borders = self.unsub.uv_border_edges(all_faces, lambda loop: loop.uv)
        with self.assertRaises(self.unsub.UnsubdivideError) as ctx:
            self.unsub.plan_unsubdivide(all_faces, borders)
        expected_odd = {faces_2d[r][c] for r in range(4) for c in range(3)}
        self.assertEqual(set(ctx.exception.problem_faces), expected_odd)

    def test_subdivided_triangle_errors_and_selects_the_three_quads(self):
        # 1 triangle subdivided once -> 3 quads around a valence-3 interior vertex.
        center = _V()
        corners = [_V() for _ in range(3)]
        mids = [_V() for _ in range(3)]
        spoke = [_E(center, mids[i]) for i in range(3)]
        rim_a = [_E(corners[i], mids[i]) for i in range(3)]
        rim_b = [_E(mids[i], corners[(i + 1) % 3]) for i in range(3)]
        quads = [
            _F(
                [spoke[i], rim_b[i], rim_a[(i + 1) % 3], spoke[(i + 1) % 3]],
                verts=[center, mids[i], corners[(i + 1) % 3], mids[(i + 1) % 3]],
            )
            for i in range(3)
        ]
        with self.assertRaises(self.unsub.UnsubdivideError) as ctx:
            self.unsub.plan_unsubdivide(quads)
        self.assertEqual(set(ctx.exception.problem_faces), set(quads))

    def test_odd_closed_cylinder_errors_and_selects_conflict(self):
        _, _, _, faces_2d = _quad_grid(7, 4, wrap_u=True, wrap_v=False)
        all_faces = [f for row in faces_2d for f in row]
        with self.assertRaises(self.unsub.UnsubdivideError) as ctx:
            self.unsub.plan_unsubdivide(all_faces)
        self.assertTrue(len(ctx.exception.problem_faces) >= 7)

    def test_four_segment_closed_tube_rejects_duplicate_edge_collapse(self):
        _, _, _, faces_2d = _quad_grid(4, 2, wrap_u=True, wrap_v=False)
        all_faces = [f for row in faces_2d for f in row]
        with self.assertRaises(self.unsub.UnsubdivideError) as ctx:
            self.unsub.plan_unsubdivide(all_faces)
        self.assertTrue(ctx.exception.problem_faces)

    def test_partial_selection_touching_unselected_faces_errors(self):
        _, _, _, faces_2d = _quad_grid(6, 4)
        left_half = [faces_2d[r][c] for r in range(4) for c in range(4)]
        with self.assertRaises(self.unsub.UnsubdivideError) as ctx:
            self.unsub.plan_unsubdivide(left_half)
        self.assertTrue(ctx.exception.problem_faces)

    def test_disconnected_valid_and_invalid_islands_aborts_and_selects_only_invalid(self):
        _, _, _, good_2d = _quad_grid(4, 4)
        _, _, _, bad_2d = _quad_grid(3, 4)
        good = [f for row in good_2d for f in row]
        bad = [f for row in bad_2d for f in row]
        with self.assertRaises(self.unsub.UnsubdivideError) as ctx:
            self.unsub.plan_unsubdivide(good + bad)
        self.assertEqual(set(ctx.exception.problem_faces), set(bad))


class MessageTests(unittest.TestCase):
    def test_timeout_message_names_the_seconds_and_the_preference(self):
        # The text a user reads when Unreal takes too long.
        source = (ROOT / "unreal_tools" / "remote.py").read_text(encoding="utf-8")
        self.assertIn("raise the timeout in the add-on preferences", source)
        self.assertIn("It may be busy", source)


if __name__ == "__main__":
    unittest.main()
