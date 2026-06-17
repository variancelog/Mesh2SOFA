"""
tunnel_loop_extractor.py
========================

Extract the TIGHT, genus-reducing cut loop (the "neck ring" you select by hand
in Blender, Image 1) directly from the tree-cotree structure you already build
in mesh_problem_viewer.py -- no HanTun / C++ dependency.

WHAT THIS SOLVES
----------------
Your existing tree-cotree detector returns a *correct but geometrically loose*
homology generator: it threads the handle but wanders across the surface (the
loose magenta loop). Cutting along it does not cleanly localize the repair.

Empirically (see the prototype tests), neither the primal generator loop NOR
the dual (cotree) loop is tight -- both are arbitrary representatives of the
homology class. Tree-cotree encodes TOPOLOGY, not GEOMETRY. To get the tight
neck ring you must add an explicit geometric step:

    Find the SHORTEST loop in the mesh whose removal actually reduces genus.

This module does exactly that, and -- crucially -- VERIFIES each candidate by
cutting and checking the Euler characteristic, so it can never return a
contractible loop or the wrong (non-severing) member of the handle/tunnel pair.

PIPELINE
--------
    1. Build tree-cotree (reuse your code) -> homology generators (loose loops).
    2. Use the loose loop only to pick BASEPOINTS near the handle.
    3. From each basepoint, Dijkstra shortest-path tree; every non-tree edge
       yields a candidate loop  x->a + (a,b) + b->x.  Collect shortest k.
    4. Sort candidates by length; walk shortest-first.
    5. For each, CUT the 1-ring band and recompute genus via Euler char.
       Return the first loop that reduces genus -> the tight neck ring.

The returned loop is the one to delete-and-cap (Blender alt+f / pymeshfix).

Dependencies: numpy, scipy, networkx  (all already in your requirements).
"""
import numpy as np
import networkx as nx
from collections import defaultdict, deque


# ---------------------------------------------------------------------------
# Mesh loading (pyvista-based, no trimesh dependency)
# ---------------------------------------------------------------------------
def load_mesh(path):
    """
    Load a surface mesh as (points, faces) ndarrays using pyvista, which is
    already a project dependency. Returns (pts float64 (N,3), faces int64 (M,3)).

    Uses triangulate().clean() so face indices are contiguous and triangular --
    the same normalization mesh_problem_viewer.py applies, so vertex indices are
    consistent between the viewer overlay and the exported Blender loop.
    """
    import pyvista as pv
    m = pv.read(str(path)).triangulate().clean()
    pts = np.asarray(m.points, dtype=np.float64)
    faces = m.faces.reshape(-1, 4)[:, 1:].astype(np.int64)
    return pts, faces


# ---------------------------------------------------------------------------
# Genus via Euler characteristic
# ---------------------------------------------------------------------------
def _genus_closed(n_v, edge_set, n_f):
    chi = n_v - len(edge_set) + n_f
    return (2 - chi) // 2


def mesh_genus(pts, faces):
    """
    Total genus of a closed orientable triangle mesh, summed over components.

    For c closed components:  chi = 2c - 2*g_total  =>  g_total = (2c - chi)/2.
    (The single-component special case reduces to the familiar (2 - chi)/2.)
    Uses only referenced vertices so stray unreferenced points don't skew chi.
    """
    es = set()
    cg = nx.Graph()
    used_v = set()
    for tri in faces:
        a, b, c = int(tri[0]), int(tri[1]), int(tri[2])
        used_v.update((a, b, c))
        for u, v in ((a, b), (b, c), (a, c)):
            es.add((u, v) if u < v else (v, u))
            cg.add_edge(u, v)
    if not used_v:
        return 0
    V, E, F = len(used_v), len(es), len(faces)
    chi = V - E + F
    n_comp = nx.number_connected_components(cg)
    return (2 * n_comp - chi) // 2


def _genus_after_cut(pts, faces, loop_verts):
    """
    Delete the 1-ring band of faces touching loop_verts, then compute the genus
    of the resulting (open) surface using the boundary-aware Euler formula:

        chi = V - E + F = 2c - 2g - b
        =>  g = (2c - b - chi) / 2

    where c = #components, b = #boundary loops.  If the loop severs the handle,
    g drops by one and b == 2 (two new holes -- the Image-2 outcome).

    Returns (genus_after, n_boundary_loops, n_components) or None.
    """
    loop_set = set(int(x) for x in loop_verts)
    keep = [tri for tri in faces
            if not (loop_set & {int(tri[0]), int(tri[1]), int(tri[2])})]
    if not keep:
        return None
    keep = np.asarray(keep)

    edge_count = defaultdict(int)
    cg = nx.Graph()
    used_v = set()
    for tri in keep:
        a, b, c = int(tri[0]), int(tri[1]), int(tri[2])
        used_v.update((a, b, c))
        for u, v in ((a, b), (b, c), (a, c)):
            edge_count[(u, v) if u < v else (v, u)] += 1
            cg.add_edge(u, v)

    boundary_edges = [e for e, n in edge_count.items() if n == 1]
    bg = nx.Graph()
    bg.add_edges_from(boundary_edges)
    n_bloops = nx.number_connected_components(bg) if bg.number_of_nodes() else 0
    n_comp = nx.number_connected_components(cg)

    V, E, F = len(used_v), len(edge_count), len(keep)
    chi = V - E + F
    g = (2 * n_comp - n_bloops - chi) // 2
    return g, n_bloops, n_comp


# ---------------------------------------------------------------------------
# Tree-cotree -> loose generators (compact version of your existing code)
# ---------------------------------------------------------------------------
def _build_edges(pts, faces):
    eid, elist = {}, []
    e2f = defaultdict(list)
    adj = defaultdict(list)

    def ekey(a, b):
        return (a, b) if a < b else (b, a)

    for fi, tri in enumerate(faces):
        a, b, c = int(tri[0]), int(tri[1]), int(tri[2])
        for u, v in ((a, b), (b, c), (a, c)):
            k = ekey(u, v)
            if k not in eid:
                eid[k] = len(elist)
                elist.append(k)
            e2f[eid[k]].append(fi)
    for ei, (u, v) in enumerate(elist):
        adj[u].append((v, ei))
        adj[v].append((u, ei))
    return eid, elist, e2f, adj


def _primal_forest(n_v, adj):
    parent = [-1] * n_v
    intree = set()
    seen = [False] * n_v
    for s in range(n_v):
        if seen[s]:
            continue
        seen[s] = True
        dq = deque([s])
        while dq:
            x = dq.popleft()
            for (y, ei) in adj[x]:
                if not seen[y]:
                    seen[y] = True
                    parent[y] = x
                    intree.add(ei)
                    dq.append(y)
    return parent, intree


def _dual_forest(n_f, elist, e2f, intree):
    dadj = defaultdict(list)
    for ei, (u, v) in enumerate(elist):
        if ei in intree:
            continue
        fs = e2f[ei]
        if len(fs) == 2:
            dadj[fs[0]].append((fs[1], ei))
            dadj[fs[1]].append((fs[0], ei))
    cotree = set()
    seen = [False] * n_f
    for s in range(n_f):
        if seen[s]:
            continue
        seen[s] = True
        dq = deque([s])
        while dq:
            x = dq.popleft()
            for (y, ei) in dadj[x]:
                if not seen[y]:
                    seen[y] = True
                    cotree.add(ei)
                    dq.append(y)
    return cotree


def _generators(elist, e2f, intree, cotree):
    return [ei for ei in range(len(elist))
            if ei not in intree and ei not in cotree and len(e2f[ei]) == 2]


def _loose_loop(gen_ei, elist, parent):
    """Generator edge + primal-tree path between its endpoints (loose loop)."""
    u, v = elist[gen_ei]
    au = {}
    x, d = u, 0
    while x != -1:
        au[x] = d
        x = parent[x]
        d += 1
    pv = []
    x = v
    while x not in au:
        pv.append(x)
        x = parent[x]
    lca = x
    pu = []
    x = u
    while x != lca:
        pu.append(x)
        x = parent[x]
    pu.append(lca)
    return np.array(pu + pv[::-1], dtype=np.int64)


# ---------------------------------------------------------------------------
# Shortest-loop search from basepoints + verification
# ---------------------------------------------------------------------------
def _loop_len(pts, lp):
    a = np.asarray(lp)
    cl = np.vstack([pts[a], pts[a][:1]])
    return float(np.linalg.norm(np.diff(cl, axis=0), axis=1).sum())


def _severs(pts, faces, lp, g0):
    """A loop severs a handle iff cutting it drops genus, opens >=2 holes,
    and keeps the surface in ONE piece (separating loops make 2 components)."""
    res = _genus_after_cut(pts, faces, np.asarray(lp))
    return res is not None and res[0] < g0 and res[1] >= 2 and res[2] == 1


def _tighten_loop(pts, faces, loop, G, g0, rounds=6):
    """
    Shorten a KNOWN-severing loop while preserving its severing property,
    verified at every step by the cut-check. Replaces short windows of the
    loop with graph-shortest-paths; accepts a replacement only if it is
    shorter AND still severs. Monotone: can only shorten, never leaves the
    severing class. On real ear-scan data this pulls a ~50 mm loose loop down
    to a ~6 mm tight neck ring in a few seconds.
    """
    loop = [int(v) for v in loop]
    if len(loop) > 1 and loop[0] == loop[-1]:
        loop = loop[:-1]
    best_len = _loop_len(pts, loop)
    for _ in range(rounds):
        improved = False
        n = len(loop)
        for w in (max(2, n // 3), max(2, n // 5), 6, 4, 3):
            if w >= n:
                continue
            i = 0
            while i < n:
                j = (i + w) % n
                u, v = loop[i], loop[j]
                if u != v:
                    try:
                        sp = nx.shortest_path(G, u, v, weight='weight')
                    except Exception:
                        sp = None
                    if sp and len(sp) >= 2:
                        cand = (loop[:i] + sp + loop[j + 1:]) if i < j \
                            else (sp + loop[j + 1:i])
                        cleaned = [cand[0]]
                        for x in cand[1:]:
                            if x != cleaned[-1]:
                                cleaned.append(x)
                        if len(cleaned) >= 3 and cleaned[0] == cleaned[-1]:
                            cleaned = cleaned[:-1]
                        if len(cleaned) >= 3 and len(set(cleaned)) == len(cleaned):
                            cl = _loop_len(pts, cleaned)
                            if cl < best_len - 1e-6 and _severs(pts, faces, cleaned, g0):
                                loop, best_len, n = cleaned, cl, len(cleaned)
                                improved = True
                i += 1
        if not improved:
            break
    return np.array(loop, dtype=np.int64)


# ---------------------------------------------------------------------------
# Dual loop B: the shortest loop crossing loop A at exactly one vertex
# ---------------------------------------------------------------------------
# A genus-1 handle has TWO dual cut loops that cross exactly once (Dey et al.
# 2008). extract_tight_cut_loops returns ONE of them (loop A). The other member,
# loop B, is the shortest loop crossing A at a single vertex. We find it by
# "cutting" the surface along A -- removing all of A's vertices turns the
# genus-1 surface into a connected annulus (A is non-separating), so the only
# path between A's two local sides runs AROUND the handle. For each loop vertex
# a_i we split its remaining neighbours into the two fan sides and take the
# shortest L-side -> R-side path through the annulus, closed through a_i.
#
# Validated on the real ear scan: the generated B is vertex-identical to the
# user's manual ground-truth cut (see plans/tunnel-loop/test_dual_loop.py).
def _vertex_faces(faces):
    vf = defaultdict(list)
    for fi, tri in enumerate(faces):
        for v in (int(tri[0]), int(tri[1]), int(tri[2])):
            vf[v].append(fi)
    return vf


def _fan_sides(a_i, prev, nxt, faces, vf):
    """Split a_i's neighbours into the loop's two sides at a_i.

    Build the neighbour-link graph (u,w adjacent iff (a_i,u,w) is a face), which
    forms a cycle around an interior manifold vertex. Removing the two loop
    neighbours prev/nxt splits that cycle into two arcs -- the two sides.
    Returns (sideL, sideR) lists, or None if it does not split cleanly in two
    (e.g. a sharp loop turn where prev and nxt are fan-adjacent -> one side
    empty; such a_i is simply skipped by the caller).
    """
    link = nx.Graph()
    for fi in vf[a_i]:
        tri = faces[fi]
        others = [int(v) for v in (tri[0], tri[1], tri[2]) if int(v) != a_i]
        if len(others) == 2:
            link.add_edge(others[0], others[1])
    if prev not in link or nxt not in link:
        return None
    H = link.copy()
    H.remove_nodes_from([prev, nxt])
    comps = [list(c) for c in nx.connected_components(H)]
    if len(comps) != 2:
        return None
    return comps[0], comps[1]


def dual_crossing_loop(pts, faces, loop_A):
    """Return the shortest loop crossing loop_A at exactly one vertex (loop B).

    loop_A is an ordered vertex-index array (e.g. from extract_tight_cut_loops).
    Returns an ordered ndarray of vertex indices, or None if no crossing loop is
    found. The returned loop touches loop_A at exactly one vertex and wraps the
    handle, so cutting it severs the handle just like A does -- but it is the
    OTHER (dual) member of the pair. Use the disk-in-solid classifier to decide
    which of A / B is the bridge-neck loop to cut.
    """
    pts = np.asarray(pts, dtype=np.float64)
    faces = np.asarray(faces)
    A = [int(v) for v in loop_A]
    if len(A) > 1 and A[0] == A[-1]:
        A = A[:-1]
    if len(A) < 3:
        return None
    Aset = set(A)
    vf = _vertex_faces(faces)

    _, elist, _, _ = _build_edges(pts, faces)
    G = nx.Graph()
    for (u, v) in elist:
        if u in Aset or v in Aset:
            continue
        G.add_edge(u, v, weight=float(np.linalg.norm(pts[u] - pts[v])))

    def w(u, v):
        return float(np.linalg.norm(pts[u] - pts[v]))

    best = None  # (length, ordered_loop)
    k = len(A)
    for i in range(k):
        a_i = A[i]
        prev, nxt = A[(i - 1) % k], A[(i + 1) % k]
        sides = _fan_sides(a_i, prev, nxt, faces, vf)
        if sides is None:
            continue
        L = [x for x in sides[0] if x in G]
        R = set(y for y in sides[1] if y in G)
        if not L or not R:
            continue
        for x in L:
            try:
                dist, paths = nx.single_source_dijkstra(G, x)
            except Exception:
                continue
            for y in R:
                if y not in dist:
                    continue
                length = w(a_i, x) + dist[y] + w(y, a_i)
                if best is None or length < best[0] - 1e-9:
                    best = (length, [a_i] + list(paths[y]))
    if best is None:
        return None
    return np.array(best[1], dtype=np.int64)


def extract_tight_cut_loops(pts, faces, genus, *, max_pins=5):
    """
    Main entry point.

    Returns a list of tight, severing cut loops -- one per handle -- each an
    ordered ndarray of vertex indices forming the neck ring to cut.

    METHOD (validated on real 34k-vertex ear scans):
      1. tree-cotree -> homology generators (loose loops that thread handles).
      2. Keep only the loose loops that actually SEVER (genus drops, 2 holes,
         1 component) -- this discards the wrong member of each handle pair.
      3. TIGHTEN each severing loose loop to its short neck ring, verified at
         every step so it never leaves the severing class.
      4. Deduplicate by location; return up to `genus` loops.

    Every returned loop is GUARANTEED by the Euler-characteristic check to
    reduce genus by one when its 1-ring band is deleted and the two resulting
    holes are capped.

    NOTE: an earlier candidate-search strategy (shortest loops from basepoints)
    was found to FAIL on real fine-tessellation meshes -- it never reached a
    severing loop among thousands of short contractible ones. Tightening a
    known-severing loop is the approach that works.
    """
    pts = np.asarray(pts, dtype=np.float64)
    faces = np.asarray(faces)
    g0 = mesh_genus(pts, faces)
    if g0 <= 0:
        return []

    eid, elist, e2f, adj = _build_edges(pts, faces)
    parent, intree = _primal_forest(len(pts), adj)
    cotree = _dual_forest(len(faces), elist, e2f, intree)
    gens = _generators(elist, e2f, intree, cotree)
    if not gens:
        return []

    G = nx.Graph()
    for (u, v) in elist:
        G.add_edge(u, v, weight=float(np.linalg.norm(pts[u] - pts[v])))

    found, centroids = [], []
    for g in gens:
        loose = _loose_loop(g, elist, parent)
        if not _severs(pts, faces, loose, g0):
            continue  # wrong member of the handle/tunnel pair (separates, etc.)
        tight = _tighten_loop(pts, faces, loose, G, g0)
        if not _severs(pts, faces, tight, g0):
            tight = loose  # fall back to the loose loop if tightening drifted
        c = pts[tight].mean(axis=0)
        if any(np.linalg.norm(c - fc) < _loop_len(pts, tight) for fc in centroids):
            continue  # same handle already captured
        found.append(tight)
        centroids.append(c)
        if len(found) >= min(genus, max_pins):
            break

    return found


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(
        description="Extract tight, verified genus-reducing cut loops from a mesh.")
    ap.add_argument("mesh", help="watertight mesh (.ply/.stl/.obj)")
    ap.add_argument("--genus", type=int, default=None,
                    help="known genus (default: compute via Euler char)")
    args = ap.parse_args()

    pts, faces = load_mesh(args.mesh)
    g = args.genus if args.genus is not None else mesh_genus(pts, faces)
    print(f"V={len(pts)} F={len(faces)} genus={mesh_genus(pts, faces)} (using genus={g})")

    loops = extract_tight_cut_loops(pts, faces, g)
    print(f"Extracted {len(loops)} tight cut loop(s)")
    for i, lp in enumerate(loops):
        L = _loop_len(pts, lp)
        c = pts[lp].mean(0)
        g_after, nb, nc = _genus_after_cut(pts, faces, lp)
        print(f"  loop {i}: {len(lp)} verts, len={L:.3f} mm, centroid {c.round(2)}, "
              f"cut->(genus,holes,comps)=({g_after},{nb},{nc})")
