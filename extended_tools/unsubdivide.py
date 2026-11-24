"""UV-safe quad un-subdivision. Pure logic on bmesh-like objects (no bpy), so tests run without Blender.

Reverses one level of quad subdivision (2x2 quads -> 1 quad) by 2-coloring edges into KEPT (0) and
DISSOLVE (1). Boundaries, poles, and actual UV island borders are pinned to KEPT so seams survive
and the result is guaranteed all-quad; any conflict raises UnsubdivideError with the offending faces.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

KEPT = 0
DISSOLVE = 1
UV_EPS = 1e-5


class UnsubdivideError(ValueError):
    """Raised when the target faces cannot be cleanly un-subdivided into quads."""

    def __init__(self, message: str, problem_faces) -> None:
        super().__init__(message)
        self.problem_faces = list(problem_faces)


@dataclass
class UnsubdividePlan:
    """Vertices to dissolve in two passes: face centers first, then collinear edge midpoints."""

    face_centers: list
    edge_midpoints: list


def uv_border_edges(faces, get_uv, eps: float = UV_EPS) -> set:
    """Interior edges of `faces` whose adjacent faces disagree in UV coordinates at either endpoint."""
    face_set = set(faces)
    seen: dict = {}
    borders: set = set()
    for face in face_set:
        for loop in face.loops:
            edge = loop.edge
            nxt = loop.link_loop_next
            uv_a = get_uv(loop)
            uv_b = get_uv(nxt)
            pair = (loop.vert, (uv_a[0], uv_a[1]), nxt.vert, (uv_b[0], uv_b[1]))
            prev = seen.get(edge)
            if prev is None:
                seen[edge] = pair
                continue
            pv_a, puv_a, pv_b, puv_b = prev
            if pv_a is loop.vert:
                diff = (
                    abs(puv_a[0] - uv_a[0]) > eps
                    or abs(puv_a[1] - uv_a[1]) > eps
                    or abs(puv_b[0] - uv_b[0]) > eps
                    or abs(puv_b[1] - uv_b[1]) > eps
                )
            else:
                diff = (
                    abs(puv_a[0] - uv_b[0]) > eps
                    or abs(puv_a[1] - uv_b[1]) > eps
                    or abs(puv_b[0] - uv_a[0]) > eps
                    or abs(puv_b[1] - uv_a[1]) > eps
                )
            if diff:
                borders.add(edge)
    return borders


def _quad_opposite_pairs(face) -> tuple[tuple, tuple] | None:
    """Return the two pairs of vertex-disjoint opposite edges of a quad, or None if degenerate."""
    edges = list(face.edges)
    if len(edges) != 4:
        return None
    e0 = edges[0]
    v0 = set(e0.verts)
    opp0 = [e for e in edges[1:] if not (v0 & set(e.verts))]
    if len(opp0) != 1:
        return None
    e2 = opp0[0]
    rest = [e for e in edges[1:] if e is not e2]
    if len(rest) != 2 or (set(rest[0].verts) & set(rest[1].verts)):
        return None
    return ((e0, e2), (rest[0], rest[1]))


def _regular_interior_pairs(vert, face_set: set) -> tuple[tuple, tuple] | None:
    """Straight-through edge pairs at a manifold interior 4-valent vertex, or None if not regular."""
    edges = vert.link_edges
    if len(edges) != 4:
        return None
    faces = set()
    edge_faces = {}
    for e in edges:
        lf = set(e.link_faces)
        if len(lf) != 2 or not lf.issubset(face_set):
            return None
        edge_faces[e] = lf
        faces |= lf
    if len(faces) != 4:
        return None
    e0 = edges[0]
    opp0 = [e for e in edges[1:] if not (edge_faces[e0] & edge_faces[e])]
    if len(opp0) != 1:
        return None
    e2 = opp0[0]
    rest = [e for e in edges[1:] if e is not e2]
    if len(rest) != 2 or (edge_faces[rest[0]] & edge_faces[rest[1]]):
        return None
    return ((e0, e2), (rest[0], rest[1]))


def _is_regular_boundary_val3(vert, face_set: set) -> bool:
    """True when `vert` is a clean valence-3 boundary vertex (2 boundary edges, 1 interior edge)."""
    edges = vert.link_edges
    if len(edges) != 3:
        return False
    boundary = 0
    interior = 0
    faces = set()
    for e in edges:
        lf = set(e.link_faces)
        if not lf or not lf.issubset(face_set):
            return False
        if len(lf) == 1:
            boundary += 1
        elif len(lf) == 2:
            interior += 1
        else:
            return False
        faces |= lf
    return boundary == 2 and interior == 1 and len(faces) == 2


def _chain(edge, parent: dict) -> list:
    path = []
    while edge is not None:
        path.append(edge)
        edge = parent[edge]
    return path


def _conflict_faces_between(a, b, via_face_ab, parent: dict, via_face: dict) -> set:
    """Faces along the shortest conflicting path (between two pins) or odd cycle (up to LCA)."""
    faces = set()
    if via_face_ab is not None:
        faces.add(via_face_ab)
    chain_a = _chain(a, parent)
    chain_b = _chain(b, parent)
    ancestors_a = set(chain_a)
    lca = next((e for e in chain_b if e in ancestors_a), None)
    for e in chain_a:
        if e is lca:
            break
        if via_face[e] is not None:
            faces.add(via_face[e])
    for e in chain_b:
        if e is lca:
            break
        if via_face[e] is not None:
            faces.add(via_face[e])
    return faces


def _format_non_quad_error(non_quads: list) -> str:
    tris = sum(1 for f in non_quads if len(f.verts) == 3)
    ngons = len(non_quads) - tris
    parts = []
    if tris:
        parts.append(f"{tris} triangle(s)")
    if ngons:
        parts.append(f"{ngons} n-gon(s)")
    return f"Cannot un-subdivide: found {', '.join(parts)}"


def plan_unsubdivide(faces, protected_edges=()) -> UnsubdividePlan:
    """Compute a 1-level quad un-subdivision plan, or raise UnsubdivideError with problem faces."""
    target = list(faces)
    if not target:
        raise UnsubdivideError("No faces to un-subdivide", [])

    non_quads = [f for f in target if len(f.verts) != 4]
    if non_quads:
        raise UnsubdivideError(_format_non_quad_error(non_quads), non_quads)

    face_set = set(target)
    edges_in_target: set = set()
    verts_in_target: set = set()
    for f in target:
        edges_in_target.update(f.edges)
        verts_in_target.update(f.verts)

    # Protected edges must stay KEPT: open/selection boundaries, non-manifold edges, UV borders.
    pinned: set = set()
    protected_set = set(protected_edges)
    for e in edges_in_target:
        lf = e.link_faces
        if e in protected_set or len(lf) != 2 or not all(f in face_set for f in lf):
            pinned.add(e)

    # Build constraint graph on edges: (neighbor, same_color, face_crossed_or_None).
    adj: dict = {e: [] for e in edges_in_target}
    degenerate_faces = []
    for f in target:
        pairs = _quad_opposite_pairs(f)
        if pairs is None:
            degenerate_faces.append(f)
            continue
        for e1, e2 in pairs:
            adj[e1].append((e2, False, f))
            adj[e2].append((e1, False, f))
    if degenerate_faces:
        raise UnsubdivideError(
            f"Cannot un-subdivide: {len(degenerate_faces)} degenerate face(s)",
            degenerate_faces,
        )

    # Vertices that cannot be V_face (val 4 interior) or V_edge (val 4 interior / val 3 boundary)
    # must stay V_orig, so every incident edge in target is pinned to KEPT.
    for v in verts_in_target:
        pairs = _regular_interior_pairs(v, face_set)
        if pairs is not None:
            for e1, e2 in pairs:
                adj[e1].append((e2, True, None))
                adj[e2].append((e1, True, None))
        elif not _is_regular_boundary_val3(v, face_set):
            for e in v.link_edges:
                if e in edges_in_target:
                    pinned.add(e)

    # 2-color the constraint graph: multi-source BFS from all pinned KEPT edges first,
    # then seed-based BFS for any completely unpinned component (e.g. a seamless torus).
    color: dict = {}
    parent: dict = {}
    via_face: dict = {}
    conflict_faces: set = set()

    def _run_bfs(seeds) -> None:
        queue = deque()
        for e in seeds:
            if e in color:
                continue
            color[e] = KEPT
            parent[e] = None
            via_face[e] = None
            queue.append(e)
        while queue:
            cur = queue.popleft()
            c_cur = color[cur]
            for nxt, same, face in adj[cur]:
                expected = c_cur if same else 1 - c_cur
                if nxt not in color:
                    color[nxt] = expected
                    parent[nxt] = cur
                    via_face[nxt] = face
                    queue.append(nxt)
                elif color[nxt] != expected:
                    found = _conflict_faces_between(cur, nxt, face, parent, via_face)
                    if not found:
                        found = {f for f in cur.link_faces if f in face_set}
                    conflict_faces.update(found)

    if pinned:
        _run_bfs(pinned)
    for e in edges_in_target:
        if e not in color:
            _run_bfs([e])

    if conflict_faces:
        raise UnsubdivideError(
            f"Cannot un-subdivide cleanly without creating triangles or breaking UV borders "
            f"({len(conflict_faces)} conflicting face(s) selected)",
            conflict_faces,
        )

    # Classify vertices from the valid edge coloring and verify no degenerate merges occur.
    face_centers = []
    edge_midpoints = []
    bad_faces: set = set()
    merged_edge_pairs: dict = {}

    for v in verts_in_target:
        inc = [e for e in v.link_edges if e in edges_in_target]
        kept = [e for e in inc if color[e] == KEPT]
        dissolved = [e for e in inc if color[e] == DISSOLVE]

        if not dissolved:
            continue  # V_orig

        if not kept:
            # V_face: must be interior valence-4 with 4 distinct V_orig corners.
            v_faces = [f for f in v.link_faces if f in face_set]
            if len(inc) != 4 or len(v_faces) != 4:
                bad_faces.update(v_faces)
                continue
            corners = set()
            for f in v_faces:
                for fv in f.verts:
                    if fv is not v and not any(
                        fv in e.verts for e in inc
                    ):
                        corners.add(fv)
            if len(corners) != 4:
                bad_faces.update(v_faces)
                continue
            face_centers.append(v)
        else:
            # V_edge: must connect 2 distinct V_orig vertices with no duplicate coarse edge.
            if len(kept) != 2:
                bad_faces.update(f for f in v.link_faces if f in face_set)
                continue
            u1 = kept[0].other_vert(v)
            u2 = kept[1].other_vert(v)
            if u1 is u2:
                bad_faces.update(f for f in v.link_faces if f in face_set)
                continue
            key = frozenset((u1, u2))
            prev = merged_edge_pairs.get(key)
            if prev is not None:
                bad_faces.update(f for f in v.link_faces if f in face_set)
                bad_faces.update(f for f in prev.link_faces if f in face_set)
                continue
            merged_edge_pairs[key] = v
            edge_midpoints.append(v)

    if bad_faces:
        raise UnsubdivideError(
            f"Cannot un-subdivide: topology would collapse or create duplicate edges "
            f"({len(bad_faces)} face(s) selected)",
            bad_faces,
        )

    if not face_centers:
        raise UnsubdivideError("Nothing to un-subdivide", target)

    return UnsubdividePlan(face_centers=face_centers, edge_midpoints=edge_midpoints)
