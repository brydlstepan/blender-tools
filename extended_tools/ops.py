"""Operators for the Extended Tools panel."""

from __future__ import annotations

import bmesh
import bpy
from bpy.props import FloatProperty, IntProperty
from bpy.types import Context, Operator

from . import adaptive_remesh, clear_parent, loops, unsubdivide
from ..version import VERSION


class BLENDERTOOLS_OT_clear_parent_keep_transform(Operator):
    """Clear parents on selected objects and keep their world transforms exactly"""

    bl_idname = "blender_tools.clear_parent_keep_transform"
    bl_label = "Clear Parent (Keep Transform)"
    bl_description = (
        "Clear parent for all selected objects while preserving exact world placement"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return (
            context.mode == "OBJECT"
            and bool(context.selected_objects)
            and any(obj.parent is not None for obj in context.selected_objects)
        )

    def execute(self, context: Context):
        selected = list(context.selected_objects)
        cleared, baked = clear_parent.clear_parents_keep_world(
            selected,
            view_layer=context.view_layer,
        )

        if cleared == 0:
            self.report({"WARNING"}, "No selected objects have a parent")
            return {"CANCELLED"}

        msg = f"Cleared parent on {cleared} object(s) [{VERSION}]"
        if baked:
            msg += f" · baked shear into {baked} datablock(s)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BLENDERTOOLS_OT_select_next_loops(Operator):
    """With two edge loops selected, select the following loops at the same spacing"""

    bl_idname = "blender_tools.select_next_loops"
    bl_label = "Select Next Loops"
    bl_description = (
        "Select two loops (with any number of loops skipped between them): "
        "the direction and skip are repeated to select every following loop"
    )
    bl_options = {"REGISTER", "UNDO"}

    count: IntProperty(
        name="Count",
        description="How many loops to add (0 = until the end of the mesh)",
        default=0,
        min=0,
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.mode == "EDIT_MESH"

    def execute(self, context: Context):
        me = context.edit_object.data
        bm = bmesh.from_edit_mesh(me)
        try:
            history = [e for e in bm.select_history if isinstance(e, bmesh.types.BMEdge)]
            edges = loops.next_loops([e for e in bm.edges if e.select], self.count, history)
        except ValueError as err:
            self.report({"WARNING"}, str(err))
            return {"CANCELLED"}
        if not edges:
            self.report({"INFO"}, "No further loops")
            return {"CANCELLED"}
        for edge in edges:
            edge.select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(me)
        return {"FINISHED"}


def _select_problem_faces(bm, faces) -> None:
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    for f in bm.faces:
        f.select = False
    for f in faces:
        f.select = True
        for e in f.edges:
            e.select = True
        for v in f.verts:
            v.select = True
    bm.select_flush_mode()


def _uv_borders(bm, faces) -> set:
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        return set()
    return unsubdivide.uv_border_edges(faces, lambda loop: loop[uv_layer].uv)


class BLENDERTOOLS_OT_fix_quad_topology(Operator):
    """Check quad topology, fix diagonal triangle pairs and slanted parallelogram chains, and select any remaining non-quads"""

    bl_idname = "blender_tools.fix_quad_topology"
    bl_label = "Fix Quad Topology"
    bl_description = (
        "Check quad topology and auto-fix diagonally split quad triangles and slanted parallelogram "
        "chains without crossing UV island borders; selects any remaining unfixable non-quads"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.mode == "EDIT_MESH" and context.edit_object is not None

    def execute(self, context: Context):
        me = context.edit_object.data
        bm = bmesh.from_edit_mesh(me)

        visible = [f for f in bm.faces if not f.hide]
        selected_only = any(f.select for f in visible)
        target = [f for f in visible if f.select] if selected_only else visible
        if not target:
            self.report({"WARNING"}, "No visible faces to check")
            return {"CANCELLED"}

        uv_borders = _uv_borders(bm, target)
        total_pairs = 0
        total_chains = 0

        for _ in range(8):
            visible = [f for f in bm.faces if not f.hide]
            cur_target = [f for f in visible if f.select] if selected_only else visible
            uv_borders = {e for e in uv_borders if e.is_valid}

            diagonals = unsubdivide.find_fixable_diagonals(
                cur_target,
                uv_borders,
                edge_length=lambda e: e.calc_length(),
            )
            if diagonals:
                bmesh.ops.dissolve_edges(
                    bm,
                    edges=diagonals,
                    use_verts=False,
                    use_face_split=False,
                )
                total_pairs += len(diagonals)
                visible = [f for f in bm.faces if not f.hide]
                cur_target = [f for f in visible if f.select] if selected_only else visible
                uv_borders = {e for e in uv_borders if e.is_valid}

            chains = unsubdivide.find_slanted_chains(
                cur_target,
                uv_borders,
                edge_length=lambda e: e.calc_length(),
                vert_dist=lambda u, v: (u.co - v.co).length,
                has_edge=lambda u, v: bm.edges.get((u, v)) is not None,
            )
            chains_done = 0
            for edges, new_pairs in chains:
                if any(not e.is_valid for e in edges):
                    continue
                bmesh.ops.dissolve_edges(
                    bm,
                    edges=edges,
                    use_verts=False,
                    use_face_split=False,
                )
                for u, v in new_pairs:
                    if u.is_valid and v.is_valid:
                        bmesh.ops.connect_vert_pair(bm, verts=[u, v])
                chains_done += 1
            total_chains += chains_done

            if not diagonals and not chains_done:
                break

        visible = [f for f in bm.faces if not f.hide]
        final_target = [f for f in visible if f.select] if selected_only else visible
        non_quads = [f for f in final_target if len(f.verts) != 4]
        changed = bool(total_pairs or total_chains)

        if non_quads:
            _select_problem_faces(bm, non_quads)
            bmesh.update_edit_mesh(me, loop_triangles=changed, destructive=changed)
            if changed:
                self.report(
                    {"WARNING"},
                    f"Fixed {total_pairs} pair(s), {total_chains} chain(s); "
                    f"{len(non_quads)} unfixable non-quad(s) selected",
                )
            else:
                self.report({"WARNING"}, f"Found {len(non_quads)} unfixable non-quad(s) (selected)")
            return {"FINISHED"}

        if changed:
            bm.select_flush_mode()
            bmesh.update_edit_mesh(me, loop_triangles=True, destructive=True)
            self.report(
                {"INFO"},
                f"Fixed {total_pairs} pair(s), {total_chains} chain(s) — quad topology is clean",
            )
        else:
            self.report({"INFO"}, "Quad topology is already clean (0 non-quads)")
        return {"FINISHED"}


class BLENDERTOOLS_OT_unsubdivide_safe(Operator):
    """Un-subdivide one level while preserving UV island borders; abort and select problems on conflict"""

    bl_idname = "blender_tools.unsubdivide_safe"
    bl_label = "UV-Safe Un-Subdivide"
    bl_description = (
        "Un-subdivide one level while preserving actual UV island borders; "
        "aborts without modifying the mesh and selects non-quads or blocked faces on conflict"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.mode == "EDIT_MESH" and context.edit_object is not None

    def execute(self, context: Context):
        me = context.edit_object.data
        bm = bmesh.from_edit_mesh(me)

        visible = [f for f in bm.faces if not f.hide]
        selected_only = any(f.select for f in visible)
        target = [f for f in visible if f.select] if selected_only else visible
        if not target:
            self.report({"WARNING"}, "No visible faces to un-subdivide")
            return {"CANCELLED"}

        try:
            plan = unsubdivide.plan_unsubdivide(target, _uv_borders(bm, target))
        except unsubdivide.UnsubdivideError as err:
            _select_problem_faces(bm, err.problem_faces)
            bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)
            self.report({"ERROR"}, str(err))
            return {"FINISHED"}

        before_target = len(target)
        before_total = len(bm.faces)

        if plan.face_centers:
            bmesh.ops.dissolve_verts(
                bm,
                verts=plan.face_centers,
                use_face_split=False,
                use_boundary_tear=False,
            )
        rem_edges = [e for e in plan.dissolve_edges if e.is_valid]
        if rem_edges:
            bmesh.ops.dissolve_edges(
                bm,
                edges=rem_edges,
                use_verts=False,
                use_face_split=False,
            )
        mids = [v for v in plan.edge_midpoints if v.is_valid]
        if mids:
            bmesh.ops.dissolve_verts(
                bm,
                verts=mids,
                use_face_split=False,
                use_boundary_tear=False,
            )
        after_target = before_target - (before_total - len(bm.faces))
        bm.select_flush_mode()
        bmesh.update_edit_mesh(me, loop_triangles=True, destructive=True)
        self.report({"INFO"}, f"Un-subdivided {before_target} -> {after_target} quads")
        return {"FINISHED"}


def _apply_strip_plan(bm, plan: adaptive_remesh.StripRemeshPlan) -> None:
    """Slide kept vertices & UVs along original ribs, then dissolve the dropped loops."""
    if plan.vert_slides:
        orig_co = {}
        for _, va, vb, _ in plan.vert_slides:
            if va not in orig_co:
                orig_co[va] = va.co.copy()
            if vb not in orig_co:
                orig_co[vb] = vb.co.copy()
        for kept_v, va, vb, alpha in plan.vert_slides:
            kept_v.co = orig_co[va].lerp(orig_co[vb], alpha)

    if plan.uv_slides:
        for uv_layer in bm.loops.layers.uv:
            orig_uv = {}
            for _, la, lb, _ in plan.uv_slides:
                if la not in orig_uv:
                    orig_uv[la] = la[uv_layer].uv.copy()
                if lb not in orig_uv:
                    orig_uv[lb] = lb[uv_layer].uv.copy()
            for target_loops, la, lb, alpha in plan.uv_slides:
                new_uv = orig_uv[la].lerp(orig_uv[lb], alpha)
                for loop in target_loops:
                    loop[uv_layer].uv = new_uv

    if plan.dissolve_edges:
        edges = [e for e in plan.dissolve_edges if e.is_valid]
        if edges:
            bmesh.ops.dissolve_edges(bm, edges=edges, use_verts=False, use_face_split=False)
    if plan.dissolve_verts:
        verts = [v for v in plan.dissolve_verts if v.is_valid]
        if verts:
            bmesh.ops.dissolve_verts(
                bm,
                verts=verts,
                use_face_split=False,
                use_boundary_tear=False,
            )


class BLENDERTOOLS_OT_adaptive_quad_remesh(Operator):
    """Adaptively reduce and redistribute quad edge loops by surface curvature while keeping UV borders"""

    bl_idname = "blender_tools.adaptive_quad_remesh"
    bl_label = "Adaptive Quad Reduce"
    bl_description = (
        "Reduce and slide quad edge loops along the surface so detailed areas keep denser loops "
        "and flat areas space out, while keeping 100% quads and actual UV island borders"
    )
    bl_options = {"REGISTER", "UNDO"}

    reduction: FloatProperty(
        name="Reduction",
        description="Percentage of dissolvable loops to remove per strip (0% = slide loops only)",
        default=50.0,
        min=0.0,
        max=95.0,
        subtype="PERCENTAGE",
    )
    uniformity: FloatProperty(
        name="Uniformity",
        description="1.0 = evenly spaced loops by length, 0.0 = denser on detailed/curved areas",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.mode == "EDIT_MESH" and context.edit_object is not None

    def execute(self, context: Context):
        me = context.edit_object.data
        bm = bmesh.from_edit_mesh(me)

        visible = [f for f in bm.faces if not f.hide]
        selected_only = any(f.select for f in visible)
        target = [f for f in visible if f.select] if selected_only else visible
        if not target:
            self.report({"WARNING"}, "No visible faces to remesh")
            return {"CANCELLED"}

        try:
            if not adaptive_remesh.extract_strips(target, _uv_borders(bm, target)):
                raise unsubdivide.UnsubdivideError("No dissolvable quad loop strips found", target)
        except unsubdivide.UnsubdivideError as err:
            _select_problem_faces(bm, err.problem_faces)
            bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)
            self.report({"ERROR"}, str(err))
            return {"FINISHED"}

        before_target = len(target)
        before_total = len(bm.faces)
        ratio = self.reduction / 100.0
        processed_edges: set = set()
        strips_done = 0

        while True:
            visible = [f for f in bm.faces if not f.hide]
            cur_target = [f for f in visible if f.select] if selected_only else visible
            processed_edges = {e for e in processed_edges if e.is_valid}
            cur_borders = _uv_borders(bm, cur_target)
            strips = adaptive_remesh.extract_strips(
                cur_target,
                protected_edges=cur_borders,
                skip_edges=processed_edges,
            )
            if not strips:
                break
            strip = strips[0]
            if strip.closed:
                # Pin the sharpest loop on a closed unpinned ring so it becomes a clean open strip.
                anchor = adaptive_remesh.pick_closed_ring_anchor(strip, lambda v: v.co)
                if not anchor:
                    processed_edges.update(e for loop in strip.loops for e in loop)
                    continue
                processed_edges.update(anchor)
                continue

            plan = adaptive_remesh.plan_strip_remesh(
                strip,
                reduction=ratio,
                uniformity=self.uniformity,
                get_co=lambda v: v.co,
            )
            if plan is None:
                processed_edges.update(e for loop in strip.loops for e in loop)
                continue

            _apply_strip_plan(bm, plan)
            processed_edges.update(e for e in plan.kept_edges if e.is_valid)
            strips_done += 1

        after_target = before_target - (before_total - len(bm.faces))
        bm.select_flush_mode()
        bmesh.update_edit_mesh(me, loop_triangles=True, destructive=True)
        self.report(
            {"INFO"},
            f"Remeshed {strips_done} strip(s): {before_target} -> {after_target} quads",
        )
        return {"FINISHED"}


classes = (
    BLENDERTOOLS_OT_clear_parent_keep_transform,
    BLENDERTOOLS_OT_select_next_loops,
    BLENDERTOOLS_OT_fix_quad_topology,
    BLENDERTOOLS_OT_unsubdivide_safe,
    BLENDERTOOLS_OT_adaptive_quad_remesh,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
