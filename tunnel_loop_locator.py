"""
tunnel_loop_locator.py
======================

PURPOSE
-------
Find and TIGHTEN the topological cut loop for each handle in a watertight
mesh, then EXPORT the loop (vertex indices + coordinates) for you to cut
manually in Blender. This module deliberately does NOT cut or cap the mesh.

WHY NO CUTTING
--------------
A genus-1 handle has TWO valid cut loops (Dey et al. 2008: the 'handle loop'
and the 'tunnel loop'). BOTH reduce genus and BOTH pass an Euler-characteristic
check, but they are geometrically opposite:

  * cutting the BRIDGE-NECK loop  -> removes the membrane, holes cap flush
    with the surrounding surface  (the clean manual result)
  * cutting the TUNNEL-MOUTH loop -> slices around the opening, leaving a deep
    funnel with sliver triangles   (a valid but BEM-hostile result)

The cut-and-Euler check cannot tell these apart -- distinguishing them
provably needs the interior/exterior tessellation that HanTun computes.
So the robust, honest workflow is: let the code LOCATE and TIGHTEN the loop
(which it does reliably), then YOU make the cut in Blender where you can see
which loop is the bridge neck. Your manual cut is already clean; this just
removes the hunting.

WHAT THIS GIVES YOU
-------------------
  locate_cut_loops(pts, faces, genus) -> list of tight loops (vertex-index
  arrays), each verified to sever a handle, each tightened to a short ring.

  export_loops_for_blender(...) -> writes a .txt / .npy of loop vertex indices
  and world coordinates you can select in Blender (e.g. via a tiny bpy snippet).

Depends only on numpy, scipy, networkx (already in your requirements).
The heavy lifting (tree-cotree, tightening, genus check) is imported from
tunnel_loop_extractor.py so there is ONE source of truth for that logic.
"""
import numpy as np
import networkx as nx

from tunnel_loop_extractor import (
    mesh_genus, _build_edges, _primal_forest, _dual_forest, _generators,
    _loose_loop, _tighten_loop, _genus_after_cut, _loop_len, _severs,
    dual_crossing_loop,
)


# ---------------------------------------------------------------------------
# LOCATE + TIGHTEN  (the reliable part)
# ---------------------------------------------------------------------------
def locate_cut_loops(pts, faces, genus, *, max_loops=5, progress_cb=None,
                     with_loose=False):
    """
    Return a list of tight, severing cut loops (one per handle), each an
    ordered ndarray of vertex indices. Does NOT modify the mesh.

    Each loop is guaranteed (Euler-characteristic check) to sever a handle:
    cutting its 1-ring band drops genus by one, opens exactly two holes, and
    keeps the surface in one piece. The loop is tightened to a short ring so
    it is easy to locate and select.

    progress_cb(msg) is an optional callable for status updates (used by the
    viewer's worker thread); it must be safe to call from a background thread.

    with_loose=True: return a list of (tight, loose) tuples instead of bare
    tight loops. `loose` is the unshortened tree-cotree generator used as the
    progenitor — callers can use it as a fallback when the tight loop is too
    small for dual-loop generation (e.g. degenerate 3-4 vertex rings produced
    by heavily-cleaned meshes). Default False preserves the original signature.
    """
    def _emit(msg):
        if progress_cb is not None:
            try:
                progress_cb(msg)
            except Exception:
                pass

    pts = np.asarray(pts, dtype=np.float64)
    faces = np.asarray(faces)
    g0 = mesh_genus(pts, faces)
    if g0 <= 0:
        return []

    _emit("Building tree-cotree homology...")
    eid, elist, e2f, adj = _build_edges(pts, faces)
    parent, intree = _primal_forest(len(pts), adj)
    cotree = _dual_forest(len(faces), elist, e2f, intree)
    gens = _generators(elist, e2f, intree, cotree)
    if not gens:
        return []

    G = nx.Graph()
    for (u, v) in elist:
        G.add_edge(u, v, weight=float(np.linalg.norm(pts[u] - pts[v])))

    want = min(genus, max_loops)
    loops, centroids = [], []
    for g in gens:
        loose = _loose_loop(g, elist, parent)
        if not _severs(pts, faces, loose, g0):
            continue
        _emit(f"Tightening cut loop {len(loops) + 1}/{want}...")
        tight = _tighten_loop(pts, faces, loose, G, g0)
        if not _severs(pts, faces, tight, g0):
            tight = loose
        c = pts[tight].mean(axis=0)
        if any(np.linalg.norm(c - fc) < _loop_len(pts, tight) for fc in centroids):
            continue
        loops.append((tight, loose) if with_loose else tight)
        centroids.append(c)
        if len(loops) >= want:
            break
    return loops


# ---------------------------------------------------------------------------
# CLASSIFY + SELECT  (calibrated against the user's ground-truth cut)
# ---------------------------------------------------------------------------
def _as_pv(pts, faces):
    """Build a pyvista PolyData from (pts, faces) for enclosed-point tests."""
    import pyvista as pv
    faces = np.asarray(faces)
    cells = np.hstack([np.full((len(faces), 1), 3, dtype=np.int64),
                       faces.astype(np.int64)]).ravel()
    return pv.PolyData(np.asarray(pts, dtype=np.float64), cells)


def disk_in_solid(mesh_pv, pts, loop, grid=7):
    """Fraction of the loop's spanning disk that lies INSIDE the solid.

    Fan the loop to its centroid, sample each fan triangle on a barycentric
    grid, and test the samples against the closed mesh with
    select_enclosed_points. This is the calibrated discriminator between the two
    dual cut loops of a handle:

        disk_in_solid ~ 1.0  ->  BRIDGE-NECK loop  (spanning disk sits inside the
                                 solid bridge)               -> the loop to CUT
        disk_in_solid ~ 0.0  ->  TUNNEL-MOUTH/FUNNEL loop    -> the loop to AVOID

    Polarity pinned from the ground-truth cut (mesh-genus-1-cut-tunnel.ply):
    the user's chosen loop measured 1.0, the funnel loop 0.0. Threshold 0.5.
    """
    pts = np.asarray(pts, dtype=np.float64)
    P = pts[np.asarray(loop)]
    c = P.mean(0)
    m = len(P)
    samples = []
    for i in range(m):
        a, b = P[i], P[(i + 1) % m]
        for u in np.linspace(0.08, 0.92, grid):
            for v in np.linspace(0.08, 0.92, grid):
                if u + v < 1.0:
                    samples.append(c + u * (a - c) + v * (b - c))
    import pyvista as pv
    sel = pv.PolyData(np.asarray(samples)).select_enclosed_points(
        mesh_pv, tolerance=1e-6, check_surface=False)
    return float(np.asarray(sel["SelectedPoints"]).mean())


def select_cut_loop(pts, faces, genus, *, threshold=0.5, max_loops=5, progress_cb=None):
    """For each handle, return the dual loop pair labelled cut vs avoid.

    Pipeline:
      1. locate_cut_loops -> loop A (one tight severing loop per handle), plus
         the loose progenitor used to generate it.
      2. dual_crossing_loop(A) -> loop B (the OTHER member, crossing A once).
         Fallback: if A is too degenerate (e.g. a 3-4 vertex ring produced by
         heavily-cleaned meshes) and dual_crossing_loop returns None, retry
         dual_crossing_loop on the loose progenitor, which has enough vertices
         for the fan-split to succeed. B is the shortest crossing loop by
         construction so it needs no further tightening.
      3. disk_in_solid classifies each; the loop with disk_in_solid >= threshold
         is the bridge-neck loop to CUT, the other is the funnel loop to AVOID.

    Returns a list (one entry per handle) of dicts:
      {"cut": ndarray, "avoid": ndarray, "scores": {"cut": float, "avoid": float},
       "A": ndarray, "B": ndarray, "disk_A": float, "disk_B": float}
    If B cannot be generated even from the loose loop, falls back to
    {"cut": A, "avoid": None, ...} so the caller still gets the located loop.
    """
    def _emit(msg):
        if progress_cb is not None:
            try:
                progress_cb(msg)
            except Exception:
                pass

    pts = np.asarray(pts, dtype=np.float64)
    faces = np.asarray(faces)
    mesh_pv = _as_pv(pts, faces)
    located = locate_cut_loops(pts, faces, genus, max_loops=max_loops,
                               progress_cb=progress_cb, with_loose=True)
    results = []
    for hi, (A, loose) in enumerate(located):
        _emit(f"Computing dual loop {hi + 1}/{len(located)}...")
        B = dual_crossing_loop(pts, faces, A)
        if B is None and loose is not None and len(loose) > len(A):
            # Tight loop A too degenerate for fan-split; retry on the loose loop.
            _emit(f"Dual loop retry on loose progenitor ({len(loose)} verts)...")
            B = dual_crossing_loop(pts, faces, loose)
        disk_A = disk_in_solid(mesh_pv, pts, A)
        if B is None:
            results.append({"cut": A, "avoid": None, "A": A, "B": None,
                            "disk_A": disk_A, "disk_B": None,
                            "scores": {"cut": disk_A, "avoid": None}})
            continue
        disk_B = disk_in_solid(mesh_pv, pts, B)
        # the loop to cut is the one whose spanning disk is inside the solid
        if disk_B >= disk_A:
            cut, avoid, sc, sa = B, A, disk_B, disk_A
        else:
            cut, avoid, sc, sa = A, B, disk_A, disk_B
        results.append({"cut": cut, "avoid": avoid, "A": A, "B": B,
                        "disk_A": disk_A, "disk_B": disk_B,
                        "scores": {"cut": sc, "avoid": sa}})
    return results


# ---------------------------------------------------------------------------
# EXPERIMENTAL: score which loop of a handle pair is the 'bridge neck'
# ---------------------------------------------------------------------------
def score_loop_quality(pts, faces, loop, vertex_normals=None):
    """
    EXPERIMENTAL / UNCALIBRATED. Returns geometric descriptors that *may* help
    decide whether `loop` is the clean bridge-neck loop or the funnel-leaving
    tunnel-mouth loop. NOT yet validated against a known-good manual loop --
    treat the numbers as diagnostics, not a decision, until you calibrate the
    thresholds on your own data (cut both candidates, see which matches your
    manual result, record the descriptor values).

    Descriptors returned (dict):
      cap_planarity        : svd s3/s1 of loop verts (small = flat ring)
      cap_surface_angle_deg: angle between the loop's best-fit-plane normal and
                             the mean surface normal on the loop. Hypothesis:
                             a bridge-neck cap is roughly FLUSH (cap plane
                             contains the surface normal -> larger angle),
                             a tunnel-mouth cap spans the opening (cap plane
                             perpendicular to surface normal -> smaller angle).
      hole_depths_mm       : after cutting the 1-ring band, out-of-plane depth
                             of each resulting boundary hole (deep = funnel).
      max_hole_depth_mm    : worst of the above (bigger = more funnel-like).
    """
    pts = np.asarray(pts, dtype=np.float64)
    faces = np.asarray(faces)
    P = pts[loop]
    c = P.mean(0)
    _, s, vt = np.linalg.svd(P - c)
    cap_n = vt[2] / np.linalg.norm(vt[2])
    planarity = float(s[2] / s[0]) if s[0] > 0 else 0.0

    if vertex_normals is not None:
        surf_n = np.asarray(vertex_normals)[loop].mean(0)
        nn = np.linalg.norm(surf_n)
        surf_n = surf_n / nn if nn > 0 else surf_n
        cap_surface_angle = float(np.degrees(
            np.arccos(np.clip(abs(cap_n @ surf_n), 0, 1))))
    else:
        cap_surface_angle = float('nan')

    # depth of each resulting hole after band deletion
    loop_set = set(int(x) for x in loop)
    band = set(loop_set)
    for tri in faces:
        if loop_set & {int(tri[0]), int(tri[1]), int(tri[2])}:
            band.update(int(x) for x in tri)
    keep = [tri for tri in faces
            if not (band & {int(tri[0]), int(tri[1]), int(tri[2])})]
    from collections import defaultdict
    ec = defaultdict(int)
    for tri in keep:
        for u, v in ((int(tri[0]), int(tri[1])),
                     (int(tri[1]), int(tri[2])),
                     (int(tri[0]), int(tri[2]))):
            ec[(u, v) if u < v else (v, u)] += 1
    bedges = [e for e, n in ec.items() if n == 1]
    bg = nx.Graph()
    bg.add_edges_from(bedges)
    depths = []
    for comp in nx.connected_components(bg):
        cv = np.array(sorted(comp))
        Q = pts[cv]
        cc = Q.mean(0)
        _, _, qvt = np.linalg.svd(Q - cc)
        n = qvt[2]
        depths.append(float(np.abs((Q - cc) @ n).max()))

    return {
        "cap_planarity": planarity,
        "cap_surface_angle_deg": cap_surface_angle,
        "hole_depths_mm": depths,
        "max_hole_depth_mm": max(depths) if depths else 0.0,
    }


# ---------------------------------------------------------------------------
# EXPORT for manual cutting in Blender
# ---------------------------------------------------------------------------
def export_loops_for_blender(pts, loops, out_prefix):
    """
    Write loop vertex indices AND world coordinates to disk for manual selection
    in Blender, plus a ready-to-paste, COORDINATE-BASED selection snippet.

    Produces:
      {out_prefix}_loops.npy   : object array of vertex-index arrays
      {out_prefix}_loops.txt   : human-readable; one block per loop with the
                                 indices, the XYZ coordinates, and a Blender
                                 snippet that selects the loop by coordinate.

    WHY COORDINATES, NOT INDICES, ARE THE RELIABLE RELAY
    ----------------------------------------------------
    The indices are into the mesh as loaded by pyvista (triangulate().clean()),
    which may merge/reorder vertices relative to Blender's raw .ply import, so
    raw index selection can land on the wrong vertices. The coordinate snippet
    below is import-order-independent: it selects the mesh vertex nearest each
    exported world coordinate (within a small tolerance), so it is robust no
    matter how Blender re-indexes on import. Run it in Edit Mode with the mesh
    selected. (Coordinates are in the mesh's own units -- mm for these scans.)
    """
    pts = np.asarray(pts)
    np.save(f"{out_prefix}_loops.npy",
            np.array([np.asarray(l) for l in loops], dtype=object))
    with open(f"{out_prefix}_loops.txt", "w") as f:
        f.write(f"# {len(loops)} cut loop(s)\n")
        for li, loop in enumerate(loops):
            coords = [tuple(round(float(c), 4) for c in pts[int(v)]) for v in loop]
            f.write(f"\n# ---- loop {li}: {len(loop)} vertices "
                    f"(length {_loop_len(pts, loop):.2f} mm) ----\n")
            f.write("indices = [" + ", ".join(str(int(v)) for v in loop) + "]\n")
            for v, (x, y, z) in zip(loop, coords):
                f.write(f"#   v{int(v):7d}  ({x:9.3f}, {y:9.3f}, {z:9.3f})\n")
            # Coordinate-based Blender snippet (import-order independent)
            f.write("\n# --- paste into Blender Text Editor, Edit Mode, mesh selected ---\n")
            f.write("# import bpy, bmesh, mathutils\n")
            f.write(f"# coords = {coords}\n")
            f.write("# me = bpy.context.object.data\n")
            f.write("# bm = bmesh.from_edit_mesh(me); bm.verts.ensure_lookup_table()\n")
            f.write("# for v in bm.verts: v.select = False\n")
            f.write("# kd = mathutils.kdtree.KDTree(len(bm.verts))\n")
            f.write("# for i, v in enumerate(bm.verts): kd.insert(v.co, i)\n")
            f.write("# kd.balance()\n")
            f.write("# for c in coords:\n")
            f.write("#     _, idx, dist = kd.find(mathutils.Vector(c))\n")
            f.write("#     if dist < 0.5: bm.verts[idx].select = True\n")
            f.write("# bmesh.update_edit_mesh(me)\n")
    return f"{out_prefix}_loops.npy", f"{out_prefix}_loops.txt"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(
        description="Locate + tighten tunnel cut loops; export for manual cutting.")
    ap.add_argument("mesh", help="watertight mesh (.ply/.stl)")
    ap.add_argument("--genus", type=int, default=None,
                    help="known genus (default: compute via Euler char)")
    ap.add_argument("--out", default="cut", help="output prefix")
    ap.add_argument("--score", action="store_true",
                    help="also print experimental loop-quality descriptors")
    args = ap.parse_args()

    import pyvista as pv
    from tunnel_loop_extractor import load_mesh
    m = pv.read(args.mesh).triangulate().clean()
    pts = np.asarray(m.points, dtype=np.float64)
    faces = m.faces.reshape(-1, 4)[:, 1:].astype(np.int64)
    g = args.genus if args.genus is not None else mesh_genus(pts, faces)
    print(f"V={len(pts)} F={len(faces)} genus={mesh_genus(pts, faces)} "
          f"(using genus={g})")

    pairs = select_cut_loop(pts, faces, g)
    print(f"located {len(pairs)} handle(s); for each, the dual loop pair "
          f"(disk_in_solid >= 0.5 = CUT this, the bridge neck):")
    cut_loops = []
    for i, pr in enumerate(pairs):
        cut, avoid = pr["cut"], pr["avoid"]
        cut_loops.append(cut)
        cc = pts[cut].mean(0)
        print(f"  handle {i}:")
        print(f"    CUT   : {len(cut):3d} verts, {_loop_len(pts, cut):6.2f} mm, "
              f"centroid {cc.round(1)}, disk_in_solid={pr['scores']['cut']:.3f}, "
              f"cut->{_genus_after_cut(pts, faces, cut)}")
        if avoid is not None:
            ac = pts[avoid].mean(0)
            print(f"    AVOID : {len(avoid):3d} verts, {_loop_len(pts, avoid):6.2f} mm, "
                  f"centroid {ac.round(1)}, disk_in_solid={pr['scores']['avoid']:.3f}")
        else:
            print(f"    (dual loop B not found; cut loop is the located loop A)")

    # export the CUT loops (the bridge-neck loops to remove)
    npy, txt = export_loops_for_blender(pts, cut_loops, args.out)
    print(f"exported cut loop(s): {npy}, {txt}")
