"""
tunnel_loop_locator.py
======================

PURPOSE
-------
Find and TIGHTEN the topological cut loop for each handle in a watertight
mesh, then EXPORT the loop (vertex indices + coordinates) for you to cut
manually in Blender. This module deliberately does NOT cut or patchthe mesh.

HANDLE vs TUNNEL (settled 2026-07-04)
-------------------------------------
A genus-1 handle has TWO valid cut loops (Dey et al. 2008): the HANDLE loop
(bounds a disk INSIDE the solid; cutting it severs the material bridge) and
the TUNNEL loop (bounds a disk in the outside AIR; cutting it caps the
see-through channel flush). BOTH reduce genus and BOTH pass the
Euler-characteristic check.

Which is which IS reliably computable on the surface mesh alone — no
interior/exterior tessellation — via the Gauss linking-number test of
Dey-Fan-Wang 2013 (see `classify_handle_tunnel` below and
memory/tunnel-vs-handle-SOLVED-2026-07-04.md; validated 108/108 on the
zzmeshes ground truth + synthetic torus).

What is NOT computable is which loop the USER wants cut: that depends on
which side is the scan artifact (ground-truth users cut the HANDLE on one
mesh, the TUNNEL on five others). So the viewer pre-selects the TUNNEL loop
(the majority/typical "fill the pierced channel" fix) but the user confirms
or toggles the choice; the labels make that decision legible.

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
    """DEPRECATED (2026-07-04) — superseded by `classify_handle_tunnel`.

    Fraction of the loop's fan-disk samples inside the solid. This was the
    old cut/avoid hint; it conflates fan-disk GEOMETRY with the topological
    handle/tunnel question and breaks on loose or degenerate loops (see
    memory/tunnel-loop-lessons-learned-2026-06-17.md). Kept only for
    reference/diagnostics; no longer called by `select_cut_loop`.
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


# ---------------------------------------------------------------------------
# HANDLE/TUNNEL classification via Gauss linking numbers (settled 2026-07-04)
# ---------------------------------------------------------------------------
# Method (Dey-Fan-Wang 2013, Lemma 3.3, reduced to the dual-pair case; see
# research/genus_An efficient computation of handle and tunnel loops via Reeb
# graphs.md and memory/tunnel-vs-handle-SOLVED-2026-07-04.md):
# push a loop epsilon off the surface to both sides and take the Gauss linking
# number with its dual partner. "Pushing into the side where the loop bounds
# kills the linking":  lk_in = 0 -> HANDLE (bounds inside the solid),
# lk_out = 0 -> TUNNEL (bounds in the air). Always |lk_in - lk_out| = 1
# because the pair crosses at exactly one vertex — a built-in integrity check.
# Validated 108/108 (zzmeshes ground truth x seeds x epsilon x orientation)
# plus an analytic torus; experiment: plans/tunnel-loop/linking_classifier.py.

def _gauss_linking(P, Q):
    """Gauss linking number of closed polylines P (n,3), Q (m,3).

    Exact polygonal double sum of signed solid angles (Klenin & Langowski
    2000, method 1a). Near-integer for disjoint curves.
    """
    P = np.asarray(P, dtype=np.float64)
    Q = np.asarray(Q, dtype=np.float64)
    P2 = np.roll(P, -1, axis=0)
    Q2 = np.roll(Q, -1, axis=0)
    dQ = Q2 - Q
    total = 0.0

    def _unit(v):
        nn = np.linalg.norm(v, axis=1, keepdims=True)
        nn = np.where(nn < 1e-14, 1.0, nn)
        return v / nn

    for i in range(len(P)):
        p1, p2 = P[i], P2[i]
        r13 = Q - p1
        r14 = Q2 - p1
        r24 = Q2 - p2
        r23 = Q - p2
        n1 = _unit(np.cross(r13, r14))
        n2 = _unit(np.cross(r14, r24))
        n3 = _unit(np.cross(r24, r23))
        n4 = _unit(np.cross(r23, r13))
        s = (np.arcsin(np.clip(np.einsum("ij,ij->i", n1, n2), -1, 1))
             + np.arcsin(np.clip(np.einsum("ij,ij->i", n2, n3), -1, 1))
             + np.arcsin(np.clip(np.einsum("ij,ij->i", n3, n4), -1, 1))
             + np.arcsin(np.clip(np.einsum("ij,ij->i", n4, n1), -1, 1)))
        sign = np.sign(np.einsum("ij,ij->i", np.cross(dQ, (p2 - p1)[None, :]), r13))
        total += float((s * sign).sum())
    return total / (4.0 * np.pi)


def _vertex_normals(pts, faces):
    """Area-weighted vertex normals (orientation as given by face winding)."""
    fn = np.cross(pts[faces[:, 1]] - pts[faces[:, 0]],
                  pts[faces[:, 2]] - pts[faces[:, 0]])
    vn = np.zeros_like(pts)
    for k in range(3):
        np.add.at(vn, faces[:, k], fn)
    nn = np.linalg.norm(vn, axis=1, keepdims=True)
    nn = np.where(nn < 1e-14, 1.0, nn)
    return vn / nn


def _min_incident_edge(pts, faces):
    """Per-vertex minimum incident edge length (push-distance scale)."""
    e = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    L = np.linalg.norm(pts[e[:, 0]] - pts[e[:, 1]], axis=1)
    out = np.full(len(pts), np.inf)
    np.minimum.at(out, e[:, 0], L)
    np.minimum.at(out, e[:, 1], L)
    return out


def _points_inside(mesh_pv, pts_query):
    """Boolean inside-mask via ray parity — immune to flipped normals."""
    import pyvista as pv
    sel = pv.PolyData(np.asarray(pts_query)).select_enclosed_points(
        mesh_pv, tolerance=1e-6, check_surface=False)
    return np.asarray(sel["SelectedPoints"]).astype(bool)


def _outward_normals(mesh_pv, pts, faces, n_probe=200, seed=0):
    """(vertex normals oriented OUTWARD, probe agreement fraction).

    File-winding normals can point inward ("black in MeshLab" meshes), so the
    global sign is fixed empirically with a ray-parity probe.
    """
    vn = _vertex_normals(pts, faces)
    mine = _min_incident_edge(pts, faces)
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(pts), size=min(n_probe, len(pts)), replace=False)
    eps = 0.3 * mine[idx]
    inside = _points_inside(mesh_pv, pts[idx] + eps[:, None] * vn[idx])
    frac_in = float(inside.mean())
    if frac_in > 0.5:
        vn = -vn
    return vn, mine, max(frac_in, 1.0 - frac_in)


def _push_loop_in_out(mesh_pv, pts, vn, mine, loop, max_halvings=5):
    """Push a loop epsilon inside AND outside along outward normals.

    Every pushed point is parity-verified on its intended side; per-vertex
    epsilon starts at 0.3x the min incident edge and halves individually until
    parity passes (thin walls). Returns (curve_in, curve_out, ok).
    """
    loop = np.asarray(loop, dtype=np.int64)
    n = len(loop)
    eps_in = 0.3 * mine[loop]
    eps_out = eps_in.copy()
    for _ in range(max_halvings + 1):
        c_in = pts[loop] - eps_in[:, None] * vn[loop]
        c_out = pts[loop] + eps_out[:, None] * vn[loop]
        inside = _points_inside(mesh_pv, np.vstack([c_in, c_out]))
        bad_in = ~inside[:n]
        bad_out = inside[n:]
        if not bad_in.any() and not bad_out.any():
            return c_in, c_out, True
        eps_in[bad_in] *= 0.5
        eps_out[bad_out] *= 0.5
    return c_in, c_out, False


def _classify_one(mesh_pv, pts, vn, mine, loop, partner):
    """Linking-number label for `loop` given its dual `partner`.

    Returns (label, lk_in, lk_out) with label in {HANDLE, TUNNEL, AMBIGUOUS}.
    """
    c_in, c_out, push_ok = _push_loop_in_out(mesh_pv, pts, vn, mine, loop)
    Pq = pts[np.asarray(partner, dtype=np.int64)]
    lk_in = _gauss_linking(c_in, Pq)
    lk_out = _gauss_linking(c_out, Pq)
    ri, ro = int(round(lk_in)), int(round(lk_out))
    near_int = abs(lk_in - ri) < 0.1 and abs(lk_out - ro) < 0.1
    ok = push_ok and near_int and abs(ri - ro) == 1
    if ok and ri == 0 and ro != 0:
        label = "HANDLE"
    elif ok and ro == 0 and ri != 0:
        label = "TUNNEL"
    else:
        label = "AMBIGUOUS"
    return label, lk_in, lk_out


def classify_handle_tunnel(pts, faces, loop_a, loop_b, *, mesh_pv=None,
                           vn=None, mine=None):
    """Classify a dual cut-loop pair as (handle, tunnel) — surface-only.

    loop_a / loop_b: the two dual loops of one handle (vertex-index arrays,
    crossing at exactly one vertex, e.g. from dual_crossing_loop).

    Returns a dict:
      {"label_a", "label_b": "HANDLE"|"TUNNEL"|"AMBIGUOUS",
       "lk_a": (lk_in, lk_out), "lk_b": (lk_in, lk_out), "ok": bool}
    ok=True only when both labels are definite AND complementary — on any
    integrity-check failure both labels come back AMBIGUOUS/ok=False rather
    than a silently wrong answer.
    """
    pts = np.asarray(pts, dtype=np.float64)
    faces = np.asarray(faces)
    if mesh_pv is None:
        mesh_pv = _as_pv(pts, faces)
    if vn is None or mine is None:
        vn, mine, _ = _outward_normals(mesh_pv, pts, faces)
    la, ai, ao = _classify_one(mesh_pv, pts, vn, mine, loop_a, loop_b)
    lb, bi, bo = _classify_one(mesh_pv, pts, vn, mine, loop_b, loop_a)
    ok = {la, lb} == {"HANDLE", "TUNNEL"}
    if not ok:
        la = lb = "AMBIGUOUS"
    return {"label_a": la, "label_b": lb,
            "lk_a": (ai, ao), "lk_b": (bi, bo), "ok": ok}


def select_cut_loop(pts, faces, genus, *, max_loops=5, progress_cb=None):
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
      3. classify_handle_tunnel labels the pair via Gauss linking numbers
         (reliable, topological — see module docstring). The TUNNEL loop is
         pre-selected as "cut" (the typical pierced-channel fix: flush caps);
         the HANDLE loop is "avoid". The USER still confirms/toggles in the
         viewer — which loop to cut depends on which side is the artifact.

    Returns a list (one entry per handle) of dicts:
      {"cut": ndarray, "avoid": ndarray|None, "A": ndarray, "B": ndarray|None,
       "labels": {"cut": "TUNNEL"|"HANDLE"|"UNKNOWN", "avoid": ...|None},
       "linking": {"cut": (lk_in, lk_out)|None, "avoid": ...|None}}
    If B cannot be generated, falls back to {"cut": A, "avoid": None, ...}.
    If classification is AMBIGUOUS, cut=A / avoid=B with labels "UNKNOWN".
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
    vn = mine = None
    if located:
        _emit("Orienting surface normals (parity probe)...")
        vn, mine, _ = _outward_normals(mesh_pv, pts, faces)
    results = []
    for hi, (A, loose) in enumerate(located):
        _emit(f"Computing dual loop {hi + 1}/{len(located)}...")
        B = dual_crossing_loop(pts, faces, A)
        if B is None and loose is not None and len(loose) > len(A):
            # Tight loop A too degenerate for fan-split; retry on the loose loop.
            _emit(f"Dual loop retry on loose progenitor ({len(loose)} verts)...")
            B = dual_crossing_loop(pts, faces, loose)
        if B is None:
            results.append({"cut": A, "avoid": None, "A": A, "B": None,
                            "labels": {"cut": "UNKNOWN", "avoid": None},
                            "linking": {"cut": None, "avoid": None}})
            continue
        _emit(f"Classifying handle/tunnel {hi + 1}/{len(located)}...")
        cls = classify_handle_tunnel(pts, faces, A, B, mesh_pv=mesh_pv,
                                     vn=vn, mine=mine)
        if cls["ok"]:
            # Pre-select the TUNNEL loop: cutting it fills the see-through
            # channel with flush caps (the typical artifact fix; 5 of 6
            # ground-truth handles). The user can toggle to the HANDLE loop.
            if cls["label_a"] == "TUNNEL":
                cut, avoid = A, B
                lk_cut, lk_avoid = cls["lk_a"], cls["lk_b"]
            else:
                cut, avoid = B, A
                lk_cut, lk_avoid = cls["lk_b"], cls["lk_a"]
            labels = {"cut": "TUNNEL", "avoid": "HANDLE"}
            linking = {"cut": lk_cut, "avoid": lk_avoid}
        else:
            cut, avoid = A, B
            labels = {"cut": "UNKNOWN", "avoid": "UNKNOWN"}
            linking = {"cut": cls["lk_a"], "avoid": cls["lk_b"]}
        results.append({"cut": cut, "avoid": avoid, "A": A, "B": B,
                        "labels": labels, "linking": linking})
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
      patch_planarity        : svd s3/s1 of loop verts (small = flat ring)
      patch_surface_angle_deg: angle between the loop's best-fit-plane normal and
                             the mean surface normal on the loop. Hypothesis:
                             a bridge-neck patchis roughly FLUSH (patchplane
                             contains the surface normal -> larger angle),
                             a tunnel-mouth patchspans the opening (patchplane
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
    patch_n = vt[2] / np.linalg.norm(vt[2])
    planarity = float(s[2] / s[0]) if s[0] > 0 else 0.0

    if vertex_normals is not None:
        surf_n = np.asarray(vertex_normals)[loop].mean(0)
        nn = np.linalg.norm(surf_n)
        surf_n = surf_n / nn if nn > 0 else surf_n
        patch_surface_angle = float(np.degrees(
            np.arccos(np.clip(abs(patch_n @ surf_n), 0, 1))))
    else:
        patch_surface_angle = float('nan')

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
        "patch_planarity": planarity,
        "patch_surface_angle_deg": patch_surface_angle,
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
          f"(pre-selected cut = TUNNEL loop, flush channel fill):")
    cut_loops = []
    for i, pr in enumerate(pairs):
        cut, avoid = pr["cut"], pr["avoid"]
        cut_loops.append(cut)
        cc = pts[cut].mean(0)
        lk_c = pr["linking"]["cut"]
        lk_c = "" if lk_c is None else f", lk(in,out)=({lk_c[0]:+.2f},{lk_c[1]:+.2f})"
        print(f"  handle {i}:")
        print(f"    CUT   : {len(cut):3d} verts, {_loop_len(pts, cut):6.2f} mm, "
              f"centroid {cc.round(1)}, {pr['labels']['cut']}{lk_c}, "
              f"cut->{_genus_after_cut(pts, faces, cut)}")
        if avoid is not None:
            ac = pts[avoid].mean(0)
            lk_a = pr["linking"]["avoid"]
            lk_a = "" if lk_a is None else f", lk(in,out)=({lk_a[0]:+.2f},{lk_a[1]:+.2f})"
            print(f"    AVOID : {len(avoid):3d} verts, {_loop_len(pts, avoid):6.2f} mm, "
                  f"centroid {ac.round(1)}, {pr['labels']['avoid']}{lk_a}")
        else:
            print(f"    (dual loop B not found; cut loop is the located loop A)")

    # export the CUT loops (the bridge-neck loops to remove)
    npy, txt = export_loops_for_blender(pts, cut_loops, args.out)
    print(f"exported cut loop(s): {npy}, {txt}")
