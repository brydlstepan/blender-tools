"""Reference-loop quad reconstruction for triangulated all-quad meshes. Pure Python (no bpy/bmesh).

Propagates GRID (0) vs DIAGONAL (1) edge labels outward from user-selected reference edge/face
loop(s) using exact triangle rules (2 GRID -> 1 DIAG; 1 DIAG -> 2 GRID) and pole-safe 4-valent
vertex fan rules, falling back to a trail-rollback frontier tie-breaker only on all-parallel-diagonal
rings. Intended strictly for meshes whose true underlying topology is all-quad.
"""

from __future__ import annotations

import math
from collections import deque

GRID = 0
DIAG = 1
_POLE_STRAIGHT_DOT = -0.25  # flanking grid edges must continue > ~104° to rule out 3-valent poles


def _unit_dir(a, b) -> tuple[float, float, float]:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    dz = b[2] - a[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length <= 1e-12:
        return (0.0, 0.0, 0.0)
    inv = 1.0 / length
    return (dx * inv, dy * inv, dz * inv)


def _dot(u: tuple[float, float, float], v: tuple[float, float, float]) -> float:
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def _dist(a, b) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    dz = b[2] - a[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _cyclic_vert_edges(vert) -> list | None:
    """Incident edges of a manifold interior vertex in cyclic fan order, or None if boundary/non-manifold."""
    edges = list(vert.link_edges)
    if len(edges) < 3 or len(vert.link_faces) != len(edges):
        return None
    if any(len(e.link_faces) != 2 for e in edges):
        return None
    ordered = [edges[0]]
    used = {edges[0]}
    cur_e = edges[0]
    cur_f = cur_e.link_faces[0]
    for _ in range(len(edges) - 1):
        nxt_e = None
        for fe in cur_f.edges:
            if fe is not cur_e and vert in fe.verts:
                nxt_e = fe
                break
        if nxt_e is None or nxt_e in used:
            return None
        ordered.append(nxt_e)
        used.add(nxt_e)
        f0, f1 = nxt_e.link_faces
        cur_f = f1 if f0 is cur_f else f0
        cur_e = nxt_e
    return ordered


def _sectors_between(wheel: list, g_idx: list[int]) -> list[list]:
    n = len(wheel)
    k = len(g_idx)
    sectors = []
    for i in range(k):
        a, b = g_idx[i], g_idx[(i + 1) % k]
        sec = []
        pos = (a + 1) % n
        while pos != b:
            sec.append(wheel[pos])
            pos = (pos + 1) % n
        sectors.append(sec)
    return sectors


def solve_quads_from_reference(
    faces,
    ref_grid_edges,
    *,
    ref_loop_verts=(),
    uv_borders=(),
    get_co,
    max_tie_breaks: int = 4096,
) -> list:
    """Return diagonal edges to dissolve to reconstruct quads across all triangles in `faces`.

    - `ref_grid_edges`: trusted GRID edges (from selected clean edge loop(s) and/or selected quad faces).
    - `ref_loop_verts`: vertices where 2 edges of a selected straight-through reference edge loop meet.
    - `uv_borders`: actual UV island border edges (treated as GRID unless a triangle has 3 UV borders).
    - `get_co`: callable `vert -> (x, y, z)` used only for pole guards and parallel-ring tie-breaking.
    """
    all_faces = list(faces)
    tris = [f for f in all_faces if len(f.verts) == 3]
    if not tris:
        return []

    tri_set = set(tris)
    tri_edges = {e for f in tris for e in f.edges}
    verts = {v for f in tris for v in f.verts}
    wheels = {v: w for v in verts if (w := _cyclic_vert_edges(v)) is not None}
    ref_loop_vset = set(ref_loop_verts)

    label: dict = {}
    frontier: set = set()

    def _assign(edge, val: int, q: deque, trail: list) -> bool:
        cur = label.get(edge)
        if cur is not None:
            return cur == val
        label[edge] = val
        trail.append(edge)
        q.append(edge)
        for v in edge.verts:
            if v in wheels:
                frontier.add(v)
        return True

    def _apply_one_grid(sec: list, q: deque, trail: list) -> bool:
        m = len(sec)
        if m == 1:
            return _assign(sec[0], GRID, q, trail)
        if m == 3:
            return (
                _assign(sec[0], DIAG, q, trail)
                and _assign(sec[1], GRID, q, trail)
                and _assign(sec[2], DIAG, q, trail)
            )
        if m == 2:
            l0, l1 = label.get(sec[0]), label.get(sec[1])
            if l0 is not None and l1 is None:
                return _assign(sec[1], 1 - l0, q, trail)
            if l1 is not None and l0 is None:
                return _assign(sec[0], 1 - l1, q, trail)
        return True

    def _check_vertex(v, q: deque, trail: list) -> bool:
        w = wheels.get(v)
        if w is None:
            return True
        n = len(w)
        g_idx = [i for i, ed in enumerate(w) if label.get(ed) == GRID]

        if len(g_idx) == 4:
            # Skip 5+ poles (any sector with >= 2 edges means another GRID edge may lie inside).
            if any((g_idx[(k + 1) % 4] - g_idx[k] - 1) % n >= 2 for k in range(4)):
                return True
            for ed in w:
                if ed not in label and not _assign(ed, DIAG, q, trail):
                    return False
            return True

        if len(g_idx) == 3:
            sectors = _sectors_between(w, g_idx)
            big = [idx for idx, s in enumerate(sectors) if len(s) >= 2]
            if len(big) > 1:
                return True  # pole vertex
            resolved = [len(s) == 0 or (len(s) == 1 and label.get(s[0]) == DIAG) for s in sectors]
            if len(big) == 1:
                open_idx = big[0]
                for idx, s in enumerate(sectors):
                    if idx != open_idx and len(s) == 1 and not _assign(s[0], DIAG, q, trail):
                        return False
            elif sum(resolved) == 2:
                open_idx = resolved.index(False)
            else:
                return True

            open_sec = sectors[open_idx]
            if len(open_sec) == 1:
                # Guard against 3-valent poles (e.g. cube corners): only force a 4th GRID edge
                # when the two flanking GRID edges continue roughly straight across v.
                co_v = get_co(v)
                dl = _unit_dir(co_v, get_co(w[g_idx[open_idx]].other_vert(v)))
                dr = _unit_dir(co_v, get_co(w[g_idx[(open_idx + 1) % 3]].other_vert(v)))
                if _dot(dl, dr) > _POLE_STRAIGHT_DOT:
                    return True
            return _apply_one_grid(open_sec, q, trail)

        if len(g_idx) == 2 and v in ref_loop_vset:
            s1, s2 = _sectors_between(w, g_idx)
            if 1 <= len(s1) <= 3 and 1 <= len(s2) <= 3:
                return _apply_one_grid(s1, q, trail) and _apply_one_grid(s2, q, trail)

        return True

    def _propagate(q: deque, trail: list) -> bool:
        while q:
            edge = q.popleft()
            val = label[edge]
            for f in edge.link_faces:
                if f not in tri_set:
                    continue
                fe = list(f.edges)
                if val == DIAG:
                    for oe in fe:
                        if oe is not edge and not _assign(oe, GRID, q, trail):
                            return False
                else:
                    g_edges = [oe for oe in fe if label.get(oe) == GRID]
                    d_edges = [oe for oe in fe if label.get(oe) == DIAG]
                    u_edges = [oe for oe in fe if oe not in label]
                    if len(g_edges) == 3 or len(d_edges) > 1:
                        return False
                    if len(g_edges) == 2 and len(u_edges) == 1:
                        if not _assign(u_edges[0], DIAG, q, trail):
                            return False
                    elif len(d_edges) == 1 and u_edges:
                        for oe in u_edges:
                            if not _assign(oe, GRID, q, trail):
                                return False
            for v in edge.verts:
                if not _check_vertex(v, q, trail):
                    return False
        return True

    # Seed trusted GRID edges: reference edges, existing quad edges, open/selection boundaries.
    hard_seeds: set = set(ref_grid_edges)
    for f in all_faces:
        if len(f.verts) == 4:
            hard_seeds.update(f.edges)
    for e in tri_edges:
        if len(e.link_faces) != 2 or any(f not in tri_set and len(f.verts) != 4 for f in e.link_faces):
            hard_seeds.add(e)

    # Add UV borders only when they do not force all 3 edges of a triangle to GRID (disconnected UVs).
    seeds = set(hard_seeds)
    for e in uv_borders:
        if e in tri_edges and e not in seeds:
            if not any(
                f in tri_set and all(oe is e or oe in seeds for oe in f.edges)
                for f in e.link_faces
            ):
                seeds.add(e)

    q0: deque = deque()
    t0: list = []
    for e in seeds:
        _assign(e, GRID, q0, t0)
    if not _propagate(q0, t0):
        # If an optional UV border caused a conflict, retry strictly with hard seeds only.
        label.clear()
        frontier.clear()
        q0 = deque()
        t0 = []
        for e in hard_seeds:
            _assign(e, GRID, q0, t0)
        if not _propagate(q0, t0):
            return []

    # Trail-rollback tie-breaker on frontier vertices ONLY when pure propagation stalls
    # (e.g. seamless closed parallel-diagonal rings).
    failed_verts: set = set()
    tie_breaks = 0
    while frontier and tie_breaks < max_tie_breaks:
        best = None
        stale = []
        for v in frontier:
            if v in failed_verts:
                stale.append(v)
                continue
            w = wheels[v]
            if all(ed in label for ed in w):
                stale.append(v)
                continue
            g_idx = [i for i, ed in enumerate(w) if label.get(ed) == GRID]
            if len(g_idx) == 3:
                sectors = _sectors_between(w, g_idx)
                resolved = [
                    len(s) == 0 or (len(s) == 1 and label.get(s[0]) == DIAG) for s in sectors
                ]
                if sum(resolved) == 2:
                    op_idx = resolved.index(False)
                    os = sectors[op_idx]
                    if len(os) == 2 and all(ed not in label for ed in os):
                        co_v = get_co(v)
                        dl = _unit_dir(co_v, get_co(w[g_idx[op_idx]].other_vert(v)))
                        dr = _unit_dir(co_v, get_co(w[g_idx[(op_idx + 1) % 3]].other_vert(v)))
                        co0 = get_co(os[0].other_vert(v))
                        co1 = get_co(os[1].other_vert(v))
                        u0 = _unit_dir(co_v, co0)
                        u1 = _unit_dir(co_v, co1)
                        l0 = max(_dist(co_v, co0), 1e-12)
                        l1 = max(_dist(co_v, co1), 1e-12)
                        g0 = -(abs(_dot(u0, dl)) + abs(_dot(u0, dr))) - 0.5 * (l0 / l1)
                        g1 = -(abs(_dot(u1, dl)) + abs(_dot(u1, dr))) - 0.5 * (l1 / l0)
                        diag_first, grid_first = (os[1], os[0]) if g0 >= g1 else (os[0], os[1])
                        score = abs(g0 - g1)
                        if best is None or score > best[0]:
                            best = (score, v, diag_first, grid_first)
            elif len(g_idx) == 2 and v in ref_loop_vset:
                co_v = get_co(v)
                for sec in _sectors_between(w, g_idx):
                    if len(sec) == 2 and all(ed not in label for ed in sec):
                        l0 = max(_dist(co_v, get_co(sec[0].other_vert(v))), 1e-12)
                        l1 = max(_dist(co_v, get_co(sec[1].other_vert(v))), 1e-12)
                        diag_first, grid_first = (sec[0], sec[1]) if l0 >= l1 else (sec[1], sec[0])
                        score = max(l0, l1) / min(l0, l1)
                        if best is None or score > best[0]:
                            best = (score, v, diag_first, grid_first)

        for v in stale:
            frontier.discard(v)
        if best is None:
            break

        _, best_v, first_d, second_d = best
        tie_breaks += 1

        trail1: list = []
        q1: deque = deque()
        if _assign(first_d, DIAG, q1, trail1) and _propagate(q1, trail1):
            continue
        for ed in trail1:
            del label[ed]

        trail2: list = []
        q2: deque = deque()
        if _assign(second_d, DIAG, q2, trail2) and _propagate(q2, trail2):
            continue
        for ed in trail2:
            del label[ed]

        failed_verts.add(best_v)
        frontier.discard(best_v)

    return [e for e, val in label.items() if val == DIAG and e in tri_edges]
