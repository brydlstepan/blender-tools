"""UV-safe quad un-subdivision. Pure logic on bmesh-like objects (no bpy), so tests run without Blender.

Reverses one level of quad subdivision by selecting a maximum non-adjacent set of dissolvable edge
loops. Boundaries, poles, selection borders, and actual UV island borders stay KEPT so seams survive
and the result is guaranteed all-quad (even on odd spans like 143x206, where one quad strip stays
un-merged instead of failing or creating triangles).
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass

UV_EPS = 1e-5
_MAX_EXACT_CORE = 24
_MAX_EXACT_MATCHINGS = 256


class UnsubdivideError(ValueError):
    """Raised when the target faces cannot be cleanly un-subdivided into quads."""

    def __init__(self, message: str, problem_faces) -> None:
        super().__init__(message)
        self.problem_faces = list(problem_faces)


@dataclass
class UnsubdividePlan:
    """Topology elements to dissolve: face centers (val-4), remaining strip edges, then midpoints."""

    dissolve_edges: list
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


def _regular_boundary_val3_interior_edge(vert, face_set: set):
    """If `vert` is a clean valence-3 boundary vertex inside `face_set`, return its 1 interior edge."""
    edges = vert.link_edges
    if len(edges) != 3:
        return None
    boundary = 0
    interior_edge = None
    faces = set()
    for e in edges:
        lf = set(e.link_faces)
        if not lf or not lf.issubset(face_set):
            return None
        if len(lf) == 1:
            boundary += 1
        elif len(lf) == 2:
            interior_edge = e
        else:
            return None
        faces |= lf
    if boundary == 2 and interior_edge is not None and len(faces) == 2:
        return interior_edge
    return None


def _format_non_quad_error(non_quads: list, *, unfixable_tris: bool = False) -> str:
    tris = sum(1 for f in non_quads if len(f.verts) == 3)
    ngons = len(non_quads) - tris
    parts = []
    if tris:
        label = "unfixable triangle(s)" if unfixable_tris else "triangle(s)"
        parts.append(f"{tris} {label}")
    if ngons:
        parts.append(f"{ngons} n-gon(s)")
    return f"Cannot un-subdivide: found {', '.join(parts)}"


def _is_boundary_vert(vert) -> bool:
    return any(len(e.link_faces) == 1 for e in vert.link_edges)


def _match_triangle_component(comp: list, adj: dict, edge_length) -> list | None:
    """Pair every triangle in `comp` across shared edges, or return None if impossible."""
    if len(comp) % 2 == 1:
        return None

    chosen: list = []
    remaining: set = set(comp)

    def _reduce_leaves() -> bool:
        queue = deque(
            f for f in remaining if sum(1 for nb, _ in adj[f] if nb in remaining) <= 1
        )
        while queue:
            f = queue.popleft()
            if f not in remaining:
                continue
            nbs = [(nb, e) for nb, e in adj[f] if nb in remaining]
            if not nbs:
                return False
            nb, edge = nbs[0]
            remaining.remove(f)
            remaining.remove(nb)
            chosen.append(edge)
            for touched in (f, nb):
                for other, _ in adj[touched]:
                    if other in remaining:
                        deg = sum(1 for x, _ in adj[other] if x in remaining)
                        if deg <= 1:
                            queue.append(other)
        return True

    if not _reduce_leaves():
        return None
    if not remaining:
        return chosen

    # Core where every remaining triangle has degree >= 2.
    core = list(remaining)
    if len(core) <= _MAX_EXACT_CORE:
        matchings: list[list] = []
        used: set = set()

        def _search(picked: list) -> None:
            if len(matchings) >= _MAX_EXACT_MATCHINGS:
                return
            rem = [f for f in core if f not in used]
            if not rem:
                matchings.append(list(picked))
                return
            f0 = min(rem, key=lambda f: sum(1 for nb, _ in adj[f] if nb not in used))
            used.add(f0)
            for nb, edge in adj[f0]:
                if nb in remaining and nb not in used:
                    used.add(nb)
                    picked.append(edge)
                    _search(picked)
                    picked.pop()
                    used.remove(nb)
            used.remove(f0)

        _search([])
        if not matchings:
            return None
        verts = {v for f in core for v in f.verts}
        base_dec = Counter()
        for e in chosen:
            base_dec[e.verts[0]] += 1
            base_dec[e.verts[1]] += 1

        def _score(matching: list) -> tuple:
            dec = base_dec.copy()
            for e in matching:
                dec[e.verts[0]] += 1
                dec[e.verts[1]] += 1
            val_err = sum(
                abs((len(v.link_edges) - dec[v]) - (3 if _is_boundary_vert(v) else 4))
                for v in verts
            )
            neg_len = -sum(edge_length(e) for e in matching) if edge_length is not None else 0.0
            return (val_err, neg_len)

        best = min(matchings, key=_score)
        return chosen + best

    # Large core fallback: greedily pick the strongest diagonal (highest valence reduction, then
    # longest edge) and cascade forced leaves after each pick.
    dec = Counter()
    for e in chosen:
        dec[e.verts[0]] += 1
        dec[e.verts[1]] += 1

    while remaining:
        best_pair = None
        best_key = None
        for f in remaining:
            for nb, edge in adj[f]:
                if nb not in remaining:
                    continue
                v0, v1 = edge.verts
                over0 = (len(v0.link_edges) - dec[v0]) - (3 if _is_boundary_vert(v0) else 4)
                over1 = (len(v1.link_edges) - dec[v1]) - (3 if _is_boundary_vert(v1) else 4)
                key = (over0 + over1, edge_length(edge) if edge_length is not None else 0.0)
                if best_key is None or key > best_key:
                    best_key = key
                    best_pair = (f, nb, edge)
        if best_pair is None:
            return None
        f, nb, edge = best_pair
        remaining.remove(f)
        remaining.remove(nb)
        chosen.append(edge)
        dec[edge.verts[0]] += 1
        dec[edge.verts[1]] += 1
        prev_len = len(chosen)
        if not _reduce_leaves():
            return None
        for added in chosen[prev_len:]:
            dec[added.verts[0]] += 1
            dec[added.verts[1]] += 1

    return chosen


def _ideal_val(vert) -> int:
    return 3 if _is_boundary_vert(vert) else 4


def _match_odd_or_partial(comp: list, adj: dict, edge_length) -> list:
    """Best-effort diagonal pairing inside odd/irregular triangle clusters without dissolving grid edges."""
    rem = set(comp)
    dec = Counter()
    chosen: list = []
    while rem:
        best_pick = None
        best_key = None
        for tf in rem:
            for nb, edge in adj[tf]:
                if nb not in rem or id(tf) >= id(nb):
                    continue
                v0, v1 = edge.verts
                over0 = (len(v0.link_edges) - dec[v0]) - _ideal_val(v0)
                over1 = (len(v1.link_edges) - dec[v1]) - _ideal_val(v1)
                # Never dissolve an edge if neither endpoint is over-valent (that is a grid edge).
                if over0 <= 0 and over1 <= 0:
                    continue
                both_over = 1 if (over0 > 0 and over1 > 0) else 0
                length = edge_length(edge) if edge_length is not None else 0.0
                key = (both_over, over0 + over1, length)
                if best_key is None or key > best_key:
                    best_key = key
                    best_pick = (tf, nb, edge)
        if best_pick is None:
            break
        tf, nb, edge = best_pick
        rem.remove(tf)
        rem.remove(nb)
        chosen.append(edge)
        dec[edge.verts[0]] += 1
        dec[edge.verts[1]] += 1
    return chosen


def find_fixable_diagonals(faces, protected_edges=(), edge_length=None) -> list:
    """Find interior diagonal edges that cleanly merge triangle pairs into quads (best-effort).

    Never crosses `protected_edges` (e.g. UV island borders). Solves even clusters exactly and
    applies valence-safe partial matching inside odd clusters so remaining lone triangles are exposed.
    """
    tris = [f for f in faces if len(f.verts) == 3]
    if len(tris) < 2:
        return []

    protected_set = set(protected_edges)
    tri_set = set(tris)
    adj: dict = {f: [] for f in tris}
    for e in {e for f in tris for e in f.edges}:
        if e in protected_set:
            continue
        lf = e.link_faces
        if len(lf) != 2 or lf[0] not in tri_set or lf[1] not in tri_set:
            continue
        f1, f2 = lf[0], lf[1]
        if len(set(f1.edges) & set(f2.edges)) != 1:
            continue
        adj[f1].append((f2, e))
        adj[f2].append((f1, e))

    visited: set = set()
    diagonals: list = []
    for f in tris:
        if f in visited:
            continue
        comp = []
        queue = deque([f])
        visited.add(f)
        while queue:
            cur = queue.popleft()
            comp.append(cur)
            for nb, _ in adj[cur]:
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        if len(comp) < 2:
            continue
        if len(comp) % 2 == 0:
            matched = _match_triangle_component(comp, adj, edge_length)
            if matched is not None:
                diagonals.extend(matched)
                continue
        diagonals.extend(_match_odd_or_partial(comp, adj, edge_length))
    return diagonals


def _opposite_in_quad(quad, edge):
    vset = set(edge.verts)
    for oe in quad.edges:
        if oe is not edge and not (vset & set(oe.verts)):
            return oe
    return None


def find_slanted_chains(
    faces,
    protected_edges=(),
    *,
    edge_length,
    vert_dist,
    has_edge,
    max_k: int = 12,
) -> list[tuple[list, list[tuple]]]:
    """Find vertex-disjoint slanted parallelogram chains `t1 - q_1 - ... - q_k - t2` to re-quad.

    Each returned item is `(edges_to_dissolve, new_vert_pairs_to_connect)`. Dissolving the slanted
    diagonals and reconnecting the missing grid edges turns the two end triangles and k slanted
    quads into k+1 regular quads without crossing `protected_edges`.
    """
    face_set = set(faces)
    tris = [f for f in face_set if len(f.verts) == 3]
    if len(tris) < 2:
        return []
    tri_set = set(tris)
    protected_set = set(protected_edges)
    candidates = []

    for t1 in tris:
        for e0 in t1.edges:
            if e0 in protected_set or len(e0.link_faces) != 2:
                continue
            q = e0.link_faces[1] if e0.link_faces[0] is t1 else e0.link_faces[0]
            if q not in face_set or len(q.verts) != 4:
                continue

            chain_edges = [e0]
            chain_quads = []
            seen_faces = {t1}
            cur_f, cur_e = q, e0
            for _ in range(max_k):
                if cur_f in seen_faces or cur_f not in face_set or len(cur_f.verts) != 4:
                    break
                seen_faces.add(cur_f)
                chain_quads.append(cur_f)
                oe = _opposite_in_quad(cur_f, cur_e)
                if oe is None or oe in protected_set or len(oe.link_faces) != 2:
                    break
                chain_edges.append(oe)
                nxt_f = oe.link_faces[1] if oe.link_faces[0] is cur_f else oe.link_faces[0]
                if nxt_f in tri_set and nxt_f not in seen_faces:
                    if id(t1) < id(nxt_f):
                        a0, b0 = chain_edges[0].verts
                        rail_a, rail_b = [a0], [b0]
                        ok = True
                        for qi, qf in enumerate(chain_quads):
                            ea, eb = chain_edges[qi], chain_edges[qi + 1]
                            sides = [se for se in qf.edges if se is not ea and se is not eb]
                            if len(sides) != 2:
                                ok = False
                                break
                            if rail_a[-1] in sides[0].verts:
                                sa, sb = sides[0], sides[1]
                            elif rail_a[-1] in sides[1].verts:
                                sa, sb = sides[1], sides[0]
                            else:
                                ok = False
                                break
                            rail_a.append(sa.other_vert(rail_a[-1]))
                            rail_b.append(sb.other_vert(rail_b[-1]))
                        if ok and len(set(rail_a) | set(rail_b)) == 2 * len(chain_edges):
                            k = len(chain_quads)
                            dissolve_len = sum(edge_length(e) for e in chain_edges)
                            avg_old = dissolve_len / (k + 1)
                            for opt in (0, 1):
                                if opt == 0:
                                    d1, d2 = rail_b[0], rail_a[-1]
                                    new_pairs = [(rail_a[i], rail_b[i + 1]) for i in range(k)]
                                else:
                                    d1, d2 = rail_a[0], rail_b[-1]
                                    new_pairs = [(rail_b[i], rail_a[i + 1]) for i in range(k)]
                                o1 = len(d1.link_edges) - _ideal_val(d1)
                                o2 = len(d2.link_edges) - _ideal_val(d2)
                                if o1 < 0 or o2 < 0 or (o1 + o2) <= 0:
                                    continue
                                if any(has_edge(u, v) for u, v in new_pairs):
                                    continue
                                avg_new = sum(vert_dist(u, v) for u, v in new_pairs) / k
                                if avg_old <= 1e-12 or avg_new >= avg_old * 0.95:
                                    continue
                                score = (k, -(o1 + o2), avg_new / avg_old)
                                candidates.append((score, t1, nxt_f, chain_quads, chain_edges, new_pairs))
                    break
                cur_f, cur_e = nxt_f, oe

    candidates.sort(key=lambda c: c[0])
    used_faces: set = set()
    used_verts: set = set()
    result: list[tuple[list, list[tuple]]] = []
    for _, t1, t2, quads, edges, new_pairs in candidates:
        all_f = [t1, t2] + quads
        if any(f in used_faces for f in all_f):
            continue
        all_v = {v for f in all_f for v in f.verts}
        if all_v & used_verts:
            continue
        used_faces.update(all_f)
        used_verts.update(all_v)
        result.append((edges, new_pairs))
    return result


def _classify_and_find_collapse_conflicts(
    verts_in_target: set,
    edges_in_target: set,
    face_set: set,
    dissolve_edges: set,
) -> tuple[list, list, list]:
    """Classify vertices into face_centers / edge_midpoints and return any conflicting dissolved edges."""
    face_centers = []
    edge_midpoints = []
    v_orig: set = set()
    conflict_edges = []

    for v in verts_in_target:
        inc = [e for e in v.link_edges if e in edges_in_target]
        kept = [e for e in inc if e not in dissolve_edges]
        dissolved = [e for e in inc if e in dissolve_edges]

        if not dissolved:
            v_orig.add(v)
        elif not kept:
            v_faces = [f for f in v.link_faces if f in face_set]
            if len(inc) != 4 or len(v_faces) != 4:
                conflict_edges.append(dissolved[0])
                continue
            corners = {
                fv
                for f in v_faces
                for fv in f.verts
                if fv is not v and not any(fv in e.verts for e in inc)
            }
            if len(corners) != 4:
                conflict_edges.append(dissolved[0])
                continue
            face_centers.append(v)
        else:
            if len(kept) != 2:
                conflict_edges.append(dissolved[0])
                continue
            edge_midpoints.append((v, kept, dissolved))

    # Every coarse edge is either an existing KEPT edge between two V_orig verts,
    # or two collinear KEPT edges merged across an edge_midpoint. No self-loops or duplicates allowed.
    coarse_edges: set = set()
    for e in edges_in_target:
        if e not in dissolve_edges and e.verts[0] in v_orig and e.verts[1] in v_orig:
            coarse_edges.add(frozenset(e.verts))

    valid_midpoints = []
    for v, kept, dissolved in edge_midpoints:
        u1 = kept[0].other_vert(v)
        u2 = kept[1].other_vert(v)
        if u1 is u2:
            conflict_edges.append(dissolved[0])
            continue
        key = frozenset((u1, u2))
        if key in coarse_edges:
            conflict_edges.append(dissolved[0])
            continue
        coarse_edges.add(key)
        valid_midpoints.append(v)

    return face_centers, valid_midpoints, conflict_edges


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

    # Validate quads and record opposite edge pairs.
    quad_pairs: dict = {}
    degenerate_faces = []
    for f in target:
        pairs = _quad_opposite_pairs(f)
        if pairs is None:
            degenerate_faces.append(f)
        else:
            quad_pairs[f] = pairs
    if degenerate_faces:
        raise UnsubdivideError(
            f"Cannot un-subdivide: {len(degenerate_faces)} degenerate face(s)",
            degenerate_faces,
        )

    # Identify straight-through links at regular interior 4-valent vertices, and pinned edges.
    protected_set = set(protected_edges)
    pinned_edges: set = set()
    for e in edges_in_target:
        lf = e.link_faces
        if e in protected_set or len(lf) != 2 or not all(f in face_set for f in lf):
            pinned_edges.add(e)

    straight_adj: dict = {e: [] for e in edges_in_target}
    for v in verts_in_target:
        pairs = _regular_interior_pairs(v, face_set)
        if pairs is not None:
            for e1, e2 in pairs:
                straight_adj[e1].append(e2)
                straight_adj[e2].append(e1)
        else:
            # At a regular boundary val-3 vertex, only its 1 interior edge may be dissolved.
            # Every other non-regular vertex (poles, corners, selection borders) pins all its edges.
            allowed_interior = _regular_boundary_val3_interior_edge(v, face_set)
            for e in v.link_edges:
                if e in edges_in_target and e is not allowed_interior:
                    pinned_edges.add(e)

    # Partition edges into maximal straight edge loops.
    edge_to_loop: dict = {}
    loops: list[list] = []
    for e in edges_in_target:
        if e in edge_to_loop:
            continue
        lid = len(loops)
        comp = []
        queue = deque([e])
        edge_to_loop[e] = lid
        while queue:
            cur = queue.popleft()
            comp.append(cur)
            for nxt in straight_adj[cur]:
                if nxt not in edge_to_loop:
                    edge_to_loop[nxt] = lid
                    queue.append(nxt)
        loops.append(comp)

    # A loop is fixed KEPT if any edge in it is pinned or if it faces itself across a quad.
    fixed_kept: set[int] = {edge_to_loop[e] for e in pinned_edges}
    loop_adj: list[set[int]] = [set() for _ in range(len(loops))]
    for pairs in quad_pairs.values():
        for e1, e2 in pairs:
            l1 = edge_to_loop[e1]
            l2 = edge_to_loop[e2]
            if l1 == l2:
                fixed_kept.add(l1)
            else:
                loop_adj[l1].add(l2)
                loop_adj[l2].add(l1)

    # Pick a maximal independent set of dissolvable loops via BFS from fixed KEPT anchors
    # (anchors = distance 0 KEPT -> distance 1 DISSOLVE -> distance 2 KEPT -> ...).
    # On even spans this is exact 2:1; on odd spans one single quad strip stays KEPT in the middle.
    visited: set[int] = set()
    dissolved_loops: set[int] = set()

    def _bfs_select(seeds) -> None:
        queue = deque()
        for lid in seeds:
            if lid in visited:
                continue
            visited.add(lid)
            queue.append(lid)
        while queue:
            cur = queue.popleft()
            if cur not in fixed_kept and not (loop_adj[cur] & dissolved_loops):
                dissolved_loops.add(cur)
            for nxt in loop_adj[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)

    if fixed_kept:
        _bfs_select(fixed_kept)
    for lid in range(len(loops)):
        if lid not in visited:
            # Unpinned closed component (e.g. seamless torus): keep seed loop so its neighbors dissolve.
            visited.add(lid)
            _bfs_select(loop_adj[lid])

    # Verify no dissolved loop would collapse a thin ring into duplicate edges; drop one at a time.
    while True:
        dissolve_edges = {e for lid in dissolved_loops for e in loops[lid]}
        face_centers, edge_midpoints, conflicts = _classify_and_find_collapse_conflicts(
            verts_in_target, edges_in_target, face_set, dissolve_edges
        )
        if not conflicts:
            break
        dropped = {edge_to_loop[e] for e in conflicts} & dissolved_loops
        if not dropped:
            break
        dissolved_loops.discard(max(dropped))

    if not dissolved_loops:
        # Report the faces that couldn't be un-subdivided (e.g. poles, selection borders, or minimal mesh).
        raise UnsubdivideError(
            f"Cannot un-subdivide further while keeping quads and UV borders "
            f"({len(target)} face(s) selected)",
            target,
        )

    return UnsubdividePlan(
        dissolve_edges=list(dissolve_edges),
        face_centers=face_centers,
        edge_midpoints=edge_midpoints,
    )
