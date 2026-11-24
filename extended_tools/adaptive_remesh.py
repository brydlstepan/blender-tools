"""Adaptive quad-loop remesher (dissolve + surface sliding). Pure Python (no bpy/bmesh).

Given a clean quad mesh, extracts parallel strips of dissolvable edge loops (anchored by boundaries,
poles, selection borders, and actual UV island borders), weights each loop by its average vertex
turning angle across the strip, and resamples each strip to a reduced loop count:
- uniformity = 1.0 -> evenly spaced loops by 3D arc length
- uniformity = 0.0 -> dense loops on curved/detailed regions, widely spaced loops on flat regions
Kept loops slide smoothly along the exact original mesh edges (interpolating both 3D positions and
UVs per face-strip so UV borders stay intact).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from . import unsubdivide

_DETAIL_FLOOR_FRACTION = 0.05  # keeps flat spans from collapsing to zero density at uniformity=0


@dataclass
class Rib:
    """One perpendicular polyline crossing a strip of n interior loops (n+2 vertices, n+1 spans)."""

    verts: list  # length n + 2: [v_anchor0, v_1, ..., v_n, v_anchor1]
    sides: list  # 1 or 2 lists, each holding n + 1 faces flanking the rib edges
    side_loops: list  # matching `sides`: each is a list of n + 2 loops along that side of the rib


@dataclass
class Strip:
    """An ordered parallel family of dissolvable loops (either an open path or a closed ring)."""

    loops: list[list]  # ordered list of edge lists [L_1, ..., L_n]
    closed: bool  # True if L_n is adjacent to L_1 with no fixed anchor between them
    same_anchor: bool  # True for an open path whose two boundary anchors are the same loop (1-seam ring)
    ribs: list[Rib]


@dataclass
class StripRemeshPlan:
    """Slide samples (applied before dissolving) and topology elements to dissolve for one strip."""

    kept_edges: list
    dissolve_edges: list
    dissolve_verts: list
    vert_slides: list  # (kept_vert, src_v_a, src_v_b, alpha)
    uv_slides: list  # (target_loops_list, src_loop_a, src_loop_b, alpha)


def _turning_angle(p_prev, p_cur, p_next) -> float:
    ax = p_cur[0] - p_prev[0]
    ay = p_cur[1] - p_prev[1]
    az = p_cur[2] - p_prev[2]
    bx = p_next[0] - p_cur[0]
    by = p_next[1] - p_cur[1]
    bz = p_next[2] - p_cur[2]
    la = math.sqrt(ax * ax + ay * ay + az * az)
    lb = math.sqrt(bx * bx + by * by + bz * bz)
    if la <= 1e-12 or lb <= 1e-12:
        return 0.0
    cos_a = (ax * bx + ay * by + az * bz) / (la * lb)
    cos_a = max(-1.0, min(1.0, cos_a))
    return math.acos(cos_a)


def _dist(a, b) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    dz = b[2] - a[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def compute_target_params(
    span_lengths: list[float],
    loop_curvatures: list[float],
    keep_count: int,
    uniformity: float,
) -> list[float]:
    """Return `keep_count` monotonic target parameters in (0, n+1) along a strip of n interior loops.

    `span_lengths` has length n+1 (spans 0..n between anchors); `loop_curvatures` has length n.
    `uniformity` = 1.0 spaces by arc length; `uniformity` = 0.0 spaces by curvature density.
    """
    n = len(loop_curvatures)
    num_spans = n + 1
    if keep_count <= 0 or n <= 0:
        return []

    u = max(0.0, min(1.0, uniformity))
    total_len = sum(span_lengths)
    if total_len > 1e-12:
        u_weights = [l / total_len for l in span_lengths]
    else:
        u_weights = [1.0 / num_spans] * num_spans

    # Span curvature averages the turning angles of its two endpoints (clamped at anchor ends).
    span_curv = []
    for i in range(num_spans):
        c_left = loop_curvatures[i - 1] if i > 0 else loop_curvatures[0]
        c_right = loop_curvatures[i] if i < n else loop_curvatures[-1]
        span_curv.append(0.5 * (c_left + c_right))

    total_curv = sum(span_curv)
    if total_curv > 1e-12:
        floor = _DETAIL_FLOOR_FRACTION * (total_curv / num_spans)
        raw_d = [c + floor for c in span_curv]
        sum_d = sum(raw_d)
        d_weights = [w / sum_d for w in raw_d]
    else:
        d_weights = list(u_weights)

    weights = [u * uw + (1.0 - u) * dw for uw, dw in zip(u_weights, d_weights)]
    # Build CDF F[0..num_spans] with F[0] = 0, F[num_spans] = 1.
    cdf = [0.0]
    acc = 0.0
    for w in weights:
        acc += w
        cdf.append(acc)
    cdf[-1] = 1.0

    targets = []
    span_idx = 0
    for m in range(1, keep_count + 1):
        q = m / (keep_count + 1)
        while span_idx < num_spans - 1 and cdf[span_idx + 1] < q:
            span_idx += 1
        w = cdf[span_idx + 1] - cdf[span_idx]
        alpha = (q - cdf[span_idx]) / w if w > 1e-12 else 0.5
        alpha = max(0.0, min(1.0, alpha))
        targets.append(span_idx + alpha)
    return targets


def _pick_kept_indices(targets: list[float], n: int) -> list[int]:
    """Choose len(targets) strictly increasing 1-based loop indices in [1, n] closest to `targets`."""
    k = len(targets)
    if k == 0:
        return []
    indices = [max(1, min(n, round(t))) for t in targets]
    for i in range(1, k):
        if indices[i] <= indices[i - 1]:
            indices[i] = indices[i - 1] + 1
    if indices[-1] > n:
        indices[-1] = n
        for i in range(k - 2, -1, -1):
            if indices[i] >= indices[i + 1]:
                indices[i] = indices[i + 1] - 1
    return indices


def _loop_at_vert(face, vert):
    for loop in face.loops:
        if loop.vert is vert:
            return loop
    return None


def _build_ribs_for_path(
    ordered_loops: list[list],
    face_set: set,
    edge_to_loop: dict,
    quad_pairs: dict,
) -> list[Rib]:
    """Trace every perpendicular rib across an ordered open strip [L_1, ..., L_n]."""
    n = len(ordered_loops)
    loop_ids = [edge_to_loop[l[0]] for l in ordered_loops]
    loop_id_set = set(loop_ids)

    # For each vertex on L_1, find the two quads/rib-edges going backward (anchor 0) and forward (L_2).
    l1_edges = set(ordered_loops[0])
    l1_verts = []
    seen_v = set()
    for e in ordered_loops[0]:
        for v in e.verts:
            if v not in seen_v:
                seen_v.add(v)
                l1_verts.append(v)

    def _perp_edges(vert, lid):
        """Return the 2 edges at `vert` that do NOT belong to loop `lid` (the rib edges)."""
        return [e for e in vert.link_edges if edge_to_loop.get(e) != lid]

    def _step_forward_edge(vert, lid, next_lid):
        """Pick the perpendicular edge at `vert` that leads toward `next_lid` (or None)."""
        perp = _perp_edges(vert, lid)
        if len(perp) != 2:
            return None, None
        for pe in perp:
            other = pe.other_vert(vert)
            if any(edge_to_loop.get(oe) == next_lid for oe in other.link_edges):
                other_pe = perp[1] if perp[0] is pe else perp[0]
                return other_pe, pe
        return None, None

    # Orient L_1 consistently across all ribs if n == 1 (no L_2 to disambiguate forward vs backward).
    orient_side: dict = {}
    if n == 1:
        # Walk along L_1 edges propagating consistent "side 0" vs "side 1" faces.
        start_e = ordered_loops[0][0]
        f_back = start_e.link_faces[0]
        orient_side[start_e] = f_back
        q = deque([start_e])
        while q:
            ce = q.popleft()
            cb = orient_side[ce]
            for v in ce.verts:
                for ne in v.link_edges:
                    if ne in l1_edges and ne not in orient_side:
                        # The face on `ne` sharing a perpendicular edge with `cb` at `v` is the same side.
                        cb_edges = set(cb.edges)
                        match = [
                            nf
                            for nf in ne.link_faces
                            if any(pe in cb_edges and pe is not ce and pe is not ne for pe in nf.edges)
                        ]
                        if match:
                            orient_side[ne] = match[0]
                            q.append(ne)

    ribs: list[Rib] = []
    for v1 in l1_verts:
        lid1 = loop_ids[0]
        if n >= 2:
            e_back, e_fwd = _step_forward_edge(v1, lid1, loop_ids[1])
        else:
            perp = _perp_edges(v1, lid1)
            if len(perp) != 2:
                return []
            inc_l1 = [e for e in v1.link_edges if e in l1_edges]
            ref_e = inc_l1[0]
            ref_back = orient_side.get(ref_e)
            if ref_back is None:
                return []
            if perp[0] in ref_back.edges:
                e_back, e_fwd = perp[0], perp[1]
            elif perp[1] in ref_back.edges:
                e_back, e_fwd = perp[1], perp[0]
            else:
                return []
        if e_back is None or e_fwd is None:
            return []

        v0 = e_back.other_vert(v1)
        verts = [v0, v1]
        rib_edges = [e_back, e_fwd]
        cur_v = e_fwd.other_vert(v1)
        verts.append(cur_v)

        ok = True
        for k in range(1, n):
            lid_k = loop_ids[k]
            perp = _perp_edges(cur_v, lid_k)
            if len(perp) != 2 or rib_edges[-1] not in perp:
                ok = False
                break
            nxt_e = perp[1] if perp[0] is rib_edges[-1] else perp[0]
            rib_edges.append(nxt_e)
            cur_v = nxt_e.other_vert(cur_v)
            verts.append(cur_v)
        if not ok or len(verts) != n + 2:
            return []

        # Collect the 1 or 2 continuous quad strips flanking `rib_edges` (for per-side UV sliding).
        first_faces = [f for f in rib_edges[0].link_faces if f in face_set]
        sides = []
        side_loops = []
        for f0 in first_faces:
            strip_faces = [f0]
            cur_f = f0
            side_ok = True
            for k in range(1, len(rib_edges)):
                re = rib_edges[k]
                # Across loop edge separating span k-1 and span k, find the face on `re` sharing a loop edge with `cur_f`.
                cur_edges = set(cur_f.edges)
                cands = [
                    nf
                    for nf in re.link_faces
                    if nf in face_set
                    and any(
                        le in cur_edges and edge_to_loop.get(le) in loop_id_set
                        for le in nf.edges
                    )
                ]
                if len(cands) != 1:
                    side_ok = False
                    break
                cur_f = cands[0]
                strip_faces.append(cur_f)
            if not side_ok:
                return []
            # Loops at each vertex along this side (if loops exist; pure tests without loops skip UVs).
            if strip_faces[0].loops:
                sl = [_loop_at_vert(strip_faces[0], verts[0])]
                for k in range(len(strip_faces)):
                    sl.append(_loop_at_vert(strip_faces[k], verts[k + 1]))
                if any(l is None for l in sl):
                    return []
                side_loops.append(sl)
            sides.append(strip_faces)

        ribs.append(Rib(verts=verts, sides=sides, side_loops=side_loops))

    return ribs


def extract_strips(
    faces,
    protected_edges=(),
    skip_edges=(),
) -> list[Strip]:
    """Extract all dissolvable parallel loop strips from an all-quad target face set.

    Any loop containing an edge in `skip_edges` is treated as fixed KEPT (used to avoid re-reducing
    a strip that was already processed in an earlier pass).
    """
    target = list(faces)
    if not target:
        raise unsubdivide.UnsubdivideError("No faces to remesh", [])
    non_quads = [f for f in target if len(f.verts) != 4]
    if non_quads:
        raise unsubdivide.UnsubdivideError(
            unsubdivide._format_non_quad_error(non_quads),
            non_quads,
        )

    face_set = set(target)
    edges_in_target: set = set()
    verts_in_target: set = set()
    quad_pairs: dict = {}
    for f in target:
        edges_in_target.update(f.edges)
        verts_in_target.update(f.verts)
        pairs = unsubdivide._quad_opposite_pairs(f)
        if pairs is None:
            raise unsubdivide.UnsubdivideError("Cannot remesh: degenerate quad face", [f])
        quad_pairs[f] = pairs

    protected_set = set(protected_edges) | set(skip_edges)
    pinned_edges: set = set()
    for e in edges_in_target:
        lf = e.link_faces
        if e in protected_set or len(lf) != 2 or not all(f in face_set for f in lf):
            pinned_edges.add(e)

    straight_adj: dict = {e: [] for e in edges_in_target}
    for v in verts_in_target:
        pairs = unsubdivide._regular_interior_pairs(v, face_set)
        if pairs is not None:
            for e1, e2 in pairs:
                straight_adj[e1].append(e2)
                straight_adj[e2].append(e1)
        else:
            allowed_interior = unsubdivide._regular_boundary_val3_interior_edge(v, face_set)
            for e in v.link_edges:
                if e in edges_in_target and e is not allowed_interior:
                    pinned_edges.add(e)

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

    fixed_kept: set[int] = {edge_to_loop[e] for e in pinned_edges}
    loop_adj: list[set[int]] = [set() for _ in range(len(loops))]
    for pairs in quad_pairs.values():
        for e1, e2 in pairs:
            l1, l2 = edge_to_loop[e1], edge_to_loop[e2]
            if l1 == l2:
                fixed_kept.add(l1)
            else:
                loop_adj[l1].add(l2)
                loop_adj[l2].add(l1)

    # Treat any branching dissolvable loop (degree >= 3 among dissolvable neighbors) as fixed KEPT
    # so every remaining component is a clean path or cycle.
    dissolvable = {lid for lid in range(len(loops)) if lid not in fixed_kept}
    for lid in list(dissolvable):
        if len(loop_adj[lid] & dissolvable) > 2:
            fixed_kept.add(lid)
            dissolvable.remove(lid)

    visited: set[int] = set()
    strips: list[Strip] = []
    for start in sorted(dissolvable):
        if start in visited:
            continue
        comp = []
        q = deque([start])
        visited.add(start)
        while q:
            cur = q.popleft()
            comp.append(cur)
            for nb in loop_adj[cur]:
                if nb in dissolvable and nb not in visited:
                    visited.add(nb)
                    q.append(nb)

        comp_set = set(comp)
        endpoints = [lid for lid in comp if len(loop_adj[lid] & comp_set) <= 1]
        if endpoints:
            # Open path between fixed_kept anchors.
            ordered_ids = [endpoints[0]]
            seen = {endpoints[0]}
            while True:
                nbs = [nb for nb in loop_adj[ordered_ids[-1]] if nb in comp_set and nb not in seen]
                if not nbs:
                    break
                ordered_ids.append(nbs[0])
                seen.add(nbs[0])
            anchors_a = loop_adj[ordered_ids[0]] & fixed_kept
            anchors_b = loop_adj[ordered_ids[-1]] & fixed_kept
            same_anchor = bool(anchors_a and anchors_a == anchors_b)
            ordered_loops = [loops[lid] for lid in ordered_ids]
            ribs = _build_ribs_for_path(ordered_loops, face_set, edge_to_loop, quad_pairs)
            strips.append(
                Strip(loops=ordered_loops, closed=False, same_anchor=same_anchor, ribs=ribs)
            )
        else:
            # Closed ring with no fixed anchor (e.g. seamless cylinder or torus).
            ordered_ids = [comp[0]]
            seen = {comp[0]}
            while len(ordered_ids) < len(comp):
                nbs = [nb for nb in loop_adj[ordered_ids[-1]] if nb in comp_set and nb not in seen]
                if not nbs:
                    break
                ordered_ids.append(nbs[0])
                seen.add(nbs[0])
            ordered_loops = [loops[lid] for lid in ordered_ids]
            strips.append(Strip(loops=ordered_loops, closed=True, same_anchor=False, ribs=[]))

    return strips


def plan_strip_remesh(
    strip: Strip,
    reduction: float,
    uniformity: float,
    get_co,
) -> StripRemeshPlan | None:
    """Build a slide-and-dissolve plan for one open strip (or None if nothing can be changed)."""
    n = len(strip.loops)
    if n == 0 or not strip.ribs:
        return None

    min_keep = 2 if strip.same_anchor else 0
    keep_count = max(min_keep, min(n, round(n * (1.0 - max(0.0, min(1.0, reduction))))))
    # Prevent collapsing a 2-seam cylinder half directly into duplicate edges between its two anchors.
    if keep_count == 0 and any(
        rib.verts[0] is rib.verts[-1]
        or any(e.other_vert(rib.verts[0]) is rib.verts[-1] for e in rib.verts[0].link_edges)
        for rib in strip.ribs
    ):
        keep_count = min(n, 1)

    # Compute average arc lengths (n+1 spans) and average turning angles (n interior loops).
    num_ribs = len(strip.ribs)
    span_lengths = [0.0] * (n + 1)
    loop_curvatures = [0.0] * n
    for rib in strip.ribs:
        coords = [get_co(v) for v in rib.verts]
        for i in range(n + 1):
            span_lengths[i] += _dist(coords[i], coords[i + 1])
        for i in range(n):
            loop_curvatures[i] += _turning_angle(coords[i], coords[i + 1], coords[i + 2])
    span_lengths = [s / num_ribs for s in span_lengths]
    loop_curvatures = [c / num_ribs for c in loop_curvatures]

    targets = compute_target_params(span_lengths, loop_curvatures, keep_count, uniformity)
    kept_1based = _pick_kept_indices(targets, n)
    kept_set = set(kept_1based)

    vert_slides = []
    uv_slides = []
    for rib in strip.ribs:
        for idx_1, t in zip(kept_1based, targets):
            span = min(n, max(0, int(math.floor(t))))
            alpha = max(0.0, min(1.0, t - span))
            kept_v = rib.verts[idx_1]
            vert_slides.append((kept_v, rib.verts[span], rib.verts[span + 1], alpha))
            for s_idx, sl in enumerate(rib.side_loops):
                # Both faces flanking `kept_v` along this side must receive the interpolated UV.
                target_loops = [
                    _loop_at_vert(rib.sides[s_idx][idx_1 - 1], kept_v),
                    _loop_at_vert(rib.sides[s_idx][idx_1], kept_v),
                ]
                target_loops = [l for l in target_loops if l is not None]
                uv_slides.append((target_loops, sl[span], sl[span + 1], alpha))

    kept_edges = [e for i, loop in enumerate(strip.loops, start=1) if i in kept_set for e in loop]
    dissolve_edges = [
        e for i, loop in enumerate(strip.loops, start=1) if i not in kept_set for e in loop
    ]
    dissolve_verts = list(
        {v for i, loop in enumerate(strip.loops, start=1) if i not in kept_set for e in loop for v in e.verts}
    )

    return StripRemeshPlan(
        kept_edges=kept_edges,
        dissolve_edges=dissolve_edges,
        dissolve_verts=dissolve_verts,
        vert_slides=vert_slides,
        uv_slides=uv_slides,
    )


def pick_closed_ring_anchor(strip: Strip, get_co) -> list:
    """For a closed unpinned ring strip, return the edges of the sharpest loop to pin as anchor."""
    # Approximate sharpness by average turning angle along each loop's edges' perpendicular neighbors,
    # or simply fall back to loop 0 if no curvature difference exists.
    if not strip.loops:
        return []
    loop_sets = [set(l) for l in strip.loops]
    n = len(strip.loops)
    best_idx = 0
    best_score = -1.0
    for i, loop in enumerate(strip.loops):
        prev_set = loop_sets[(i - 1) % n]
        next_set = loop_sets[(i + 1) % n]
        cur_set = loop_sets[i]
        verts = {v for e in loop for v in e.verts}
        total = 0.0
        count = 0
        for v in verts:
            perp = [e for e in v.link_edges if e not in cur_set]
            if len(perp) != 2:
                continue
            v_a = perp[0].other_vert(v)
            v_b = perp[1].other_vert(v)
            # Only measure across the ring (neighbors lie on prev/next loops).
            if any(e in prev_set or e in next_set for e in v_a.link_edges) and any(
                e in prev_set or e in next_set for e in v_b.link_edges
            ):
                total += _turning_angle(get_co(v_a), get_co(v), get_co(v_b))
                count += 1
        score = total / count if count else 0.0
        if score > best_score:
            best_score = score
            best_idx = i
    return list(strip.loops[best_idx])
