"""Edge-loop pattern selection. Pure logic on bmesh-like objects (no bpy), so tests can run without Blender.

Two selected loops set the direction (loop A -> loop B) and the step (how many loops lie between them);
`next_loops` returns the edges of every following loop at that same step.
"""

from __future__ import annotations


def components(edges) -> list[set]:
    """Group edges into connected pieces (edges sharing a vertex)."""
    pending = set(edges)
    groups = []
    while pending:
        stack = [pending.pop()]
        group = set(stack)
        while stack:
            edge = stack.pop()
            for vert in edge.verts:
                for other in vert.link_edges:
                    if other in pending:
                        pending.discard(other)
                        group.add(other)
                        stack.append(other)
        groups.append(group)
    return groups


def _opposite(face, edge):
    if len(face.verts) != 4:
        return None
    for other in face.edges:
        if other is not edge and not (set(other.verts) & set(edge.verts)):
            return other
    return None


def ring(edge, face) -> list:
    """Edges reached by crossing quads from `edge` through `face`, starting with `edge` itself."""
    out = [edge]
    seen = {edge}
    while face is not None:
        nxt = _opposite(face, edge)
        if nxt is None or nxt in seen:
            break
        out.append(nxt)
        seen.add(nxt)
        faces = [f for f in nxt.link_faces if f is not face]
        face = faces[0] if faces else None
        edge = nxt
    return out


def _straight(edge, vert):
    """The edge that continues `edge` straight through `vert` (4-valent vertex), else None."""
    if len(edge.link_faces) == 1:  # boundary edge: continue along the boundary (not round corners)
        if len(vert.link_edges) != 3:
            return None
        found = [e for e in vert.link_edges if e is not edge and len(e.link_faces) == 1]
        return found[0] if len(found) == 1 else None
    if len(vert.link_edges) != 4:
        return None
    faces = set(edge.link_faces)
    found = [e for e in vert.link_edges if e is not edge and not (faces & set(e.link_faces))]
    return found[0] if len(found) == 1 else None


def edge_loop(edge) -> set:
    loop = {edge}
    for start in edge.verts:
        current, vert = edge, start
        while True:
            nxt = _straight(current, vert)
            if nxt is None or nxt in loop:
                break
            loop.add(nxt)
            current, vert = nxt, nxt.other_vert(vert)
    return loop


def next_loops(selected, count: int = 0, history=()) -> set:
    """Edges of the loops that follow the two selected ones at the same spacing. `count` 0 = to the end.

    `history` is the edges in the order they were selected; the loop holding the last one is loop 2.
    """
    groups = components(selected)
    if len(groups) != 2:
        raise ValueError(f"Select exactly two loops (found {len(groups)})")
    first, second = groups
    last = history[-1] if history else None
    if last is not None and last in first:
        first, second = second, first
    start = next(iter(first))
    for face in start.link_faces:
        sequence = ring(start, face)
        hits = [i for i, e in enumerate(sequence) if e in second]
        if hits:
            break
    else:
        raise ValueError("The two loops are not on the same ring")

    step = hits[0]
    targets = sequence[2 * step :: step]
    if count > 0:
        targets = targets[:count]
    result = set()
    for edge in targets:
        result |= edge_loop(edge)
    return result
