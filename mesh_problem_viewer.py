import sys
import os
import argparse
import numpy as np
import pyvista as pv
from collections import defaultdict, deque


# ---------------------------------------------------------------------------
# Topological tunnel / handle (genus) detector.
#
# Uses the tree-cotree method to find homology-loop generators that thread
# each topological handle.  This is the only method that gives zero false
# positives on ear-scan geometry (the curvature / geodesic-gap approaches
# both drown in ear-fold noise).
# ---------------------------------------------------------------------------

def _topological_tunnel_loops(pv_mesh, genus, *, max_pins=5):
    """
    Find topological tunnel/handle loops via the tree-cotree homology method.

    For a closed orientable surface:
        chi = V - E + F = 2 - 2g
        b1  = 2g  (first Betti number = number of homology generators)

    Algorithm:
    1. Build undirected edge index and edge→face incidence.
    2. Primal spanning forest (BFS on vertices).  Edges used = primal tree.
    3. Dual  spanning forest (BFS on faces, only crossing non-tree interior
       edges).  Edges used = cotree.
    4. Remaining interior edges (neither tree nor cotree) are the b1
       homology generators — one generator loop per edge.
    5. Each generator loop = generator edge + primal-tree path u→v.
    6. Cluster generator midpoints into `genus` groups (2 generators per
       handle → 1 pin per handle); keep the shortest loop in each cluster.

    Returns (pins, loops):
        pins  — list of np.ndarray(3,) pin positions
        loops — list of np.ndarray(K, 3) closed-polyline vertex arrays
    Returns ([], []) when nothing found or on any error.
    """
    try:
        mesh = pv_mesh.triangulate().clean()
        pts  = np.asarray(mesh.points, dtype=np.float64)
        N    = mesh.n_points
        if N < 4:
            return [], []

        faces = mesh.faces.reshape(-1, 4)[:, 1:]
        M     = len(faces)

        # ------------------------------------------------------------------
        # 1. Build undirected edge index and edge → face incidence
        # ------------------------------------------------------------------
        eid   = {}                  # (u,v) u<v → edge id
        elist = []                  # edge id → (u, v)
        e2f   = defaultdict(list)   # edge id → [face ids]
        adj   = defaultdict(list)   # vertex → [(neighbour, edge_id)]

        def ekey(a, b):
            return (a, b) if a < b else (b, a)

        for fi, tri in enumerate(faces):
            a, b, c = int(tri[0]), int(tri[1]), int(tri[2])
            for u, v in [(a, b), (b, c), (a, c)]:
                k = ekey(u, v)
                if k not in eid:
                    eid[k] = len(elist)
                    elist.append(k)
                e2f[eid[k]].append(fi)

        for ei, (u, v) in enumerate(elist):
            adj[u].append((v, ei))
            adj[v].append((u, ei))

        E = len(elist)

        # ------------------------------------------------------------------
        # 2. Primal spanning forest (BFS over vertices)
        # ------------------------------------------------------------------
        parent   = [-1] * N   # parent vertex (-1 = root)
        intree_e = set()
        seen_v   = [False] * N

        for start in range(N):
            if seen_v[start]:
                continue
            seen_v[start] = True
            dq = deque([start])
            while dq:
                x = dq.popleft()
                for (y, ei) in adj[x]:
                    if not seen_v[y]:
                        seen_v[y]  = True
                        parent[y]  = x
                        intree_e.add(ei)
                        dq.append(y)

        # ------------------------------------------------------------------
        # 3. Dual spanning forest (BFS over faces, non-tree interior edges)
        # ------------------------------------------------------------------
        dadj = defaultdict(list)   # face → [(face, edge_id)]
        for ei, (u, v) in enumerate(elist):
            if ei in intree_e:
                continue
            fs = e2f[ei]
            if len(fs) == 2:
                dadj[fs[0]].append((fs[1], ei))
                dadj[fs[1]].append((fs[0], ei))

        cotree_e = set()
        seen_f   = [False] * M
        for start in range(M):
            if seen_f[start]:
                continue
            seen_f[start] = True
            dq = deque([start])
            while dq:
                x = dq.popleft()
                for (y, ei) in dadj[x]:
                    if not seen_f[y]:
                        seen_f[y] = True
                        cotree_e.add(ei)
                        dq.append(y)

        # ------------------------------------------------------------------
        # 4. Generators = interior edges in neither forest
        # ------------------------------------------------------------------
        gens = [
            ei for ei in range(E)
            if ei not in intree_e
            and ei not in cotree_e
            and len(e2f[ei]) == 2
        ]
        if not gens:
            return [], []

        # ------------------------------------------------------------------
        # 5. Extract loop for each generator via primal-tree path u → v
        # ------------------------------------------------------------------
        def tree_path(u, v):
            """Ordered vertex list from u to v through the primal tree (LCA)."""
            # Collect ancestors of u with depth
            au = {}
            x  = u
            d  = 0
            while x != -1:
                au[x] = d
                x = parent[x]
                d += 1
            # Walk v upward to LCA
            pv_side = []
            x = v
            while x not in au:
                pv_side.append(x)
                x = parent[x]
            lca = x
            # u-side: u → lca
            pu_side = []
            x = u
            while x != lca:
                pu_side.append(x)
                x = parent[x]
            pu_side.append(lca)
            return pu_side + pv_side[::-1]

        gen_data = []   # (loop_pts ndarray, midpoint ndarray, loop_length float)
        for ei in gens:
            u, v = elist[ei]
            try:
                path = tree_path(u, v)
                if len(path) < 2:
                    continue
                loop_pts = pts[path]
                closed   = np.vstack([loop_pts, loop_pts[0:1]])
                length   = float(np.linalg.norm(np.diff(closed, axis=0), axis=1).sum())
                midpt    = (pts[u] + pts[v]) * 0.5
                gen_data.append((loop_pts, midpt, length))
            except Exception:
                continue

        if not gen_data:
            return [], []

        # ------------------------------------------------------------------
        # 6. Cluster midpoints into `genus` groups; keep shortest loop each
        # ------------------------------------------------------------------
        n_clusters = min(max(genus, 1), max_pins, len(gen_data))
        midpoints  = np.array([gd[1] for gd in gen_data])

        if len(gen_data) <= n_clusters:
            labels = np.arange(len(gen_data))
        else:
            from scipy.cluster.hierarchy import linkage, fcluster
            Z      = linkage(midpoints, method='ward')
            labels = fcluster(Z, t=n_clusters, criterion='maxclust') - 1  # 0-indexed

        pins  = []
        loops = []
        for cl in range(n_clusters):
            members = [i for i, lb in enumerate(labels) if lb == cl]
            if not members:
                continue
            pins.append(midpoints[members].mean(axis=0))
            best = min(members, key=lambda i: gen_data[i][2])   # shortest loop
            loops.append(gen_data[best][0])

        return pins, loops

    except Exception:
        return [], []


def _tight_cut_loops(pv_mesh, genus, max_pins=5):
    """
    Preferred loop finder: tree-cotree generator -> TIGHTEN to the short neck
    ring -> VERIFY by cut-and-Euler-check (tunnel_loop_extractor). Returns
    (pins, loop_point_arrays, loop_index_arrays, pts) where indices/pts are for
    the cleaned mesh so the export coordinates are exact.

    Falls back to ([], [], [], None) on any error so the caller can use the
    loose-loop method instead. Tightening costs ~10-15 s on a 35k-vertex scan;
    that is acceptable for a one-shot inspection window.
    """
    try:
        import numpy as _np
        from tunnel_loop_extractor import extract_tight_cut_loops
        mesh = pv_mesh.triangulate().clean()
        pts = _np.asarray(mesh.points, dtype=_np.float64)
        faces = mesh.faces.reshape(-1, 4)[:, 1:].astype(_np.int64)
        idx_loops = extract_tight_cut_loops(pts, faces, genus, max_pins=max_pins)
        if not idx_loops:
            return [], [], [], None
        pins = [pts[lp].mean(axis=0) for lp in idx_loops]
        pt_loops = [pts[lp] for lp in idx_loops]
        return pins, pt_loops, idx_loops, pts
    except Exception as e:
        print(f"[tunnel] tight-loop path unavailable ({e}); using loose loop",
              file=sys.stderr)
        return [], [], [], None


def _dual_cut_pairs(pv_mesh, genus, max_pins=5):
    """
    Best loop finder: for each handle return the dual loop PAIR with the
    classifier's cut/avoid labels (tunnel_loop_locator.select_cut_loop).
    Returns (pairs, pts) where pairs is a list of dicts with 'cut'/'avoid' index
    arrays (see select_cut_loop) and pts are the cleaned-mesh points so export
    coordinates are exact. Returns ([], None) on any error.
    """
    try:
        import numpy as _np
        from tunnel_loop_locator import select_cut_loop
        mesh = pv_mesh.triangulate().clean()
        pts = _np.asarray(mesh.points, dtype=_np.float64)
        faces = mesh.faces.reshape(-1, 4)[:, 1:].astype(_np.int64)
        pairs = select_cut_loop(pts, faces, genus, max_loops=max_pins)
        return (pairs, pts) if pairs else ([], None)
    except Exception as e:
        print(f"[tunnel] dual-pair path unavailable ({e}); using single loop",
              file=sys.stderr)
        return [], None


def _draw_loop(plotter, loop_pts, color, name, width=6):
    line = pv.lines_from_points(loop_pts, close=True)
    plotter.add_mesh(line, color=color, line_width=width,
                     render_lines_as_tubes=True, name=name, reset_camera=False)


def _add_labels(plotter, centroids, labels, color, name):
    if not len(centroids):
        return
    try:
        plotter.add_point_labels(
            np.array(centroids), labels, always_visible=True,
            point_size=18, font_size=14, text_color=color,
            shape='rounded_rect', shape_color='black',
            name=name, reset_camera=False)
    except TypeError:
        try:
            plotter.add_point_labels(
                np.array(centroids), labels, always_visible=True,
                point_size=18, font_size=14, name=name, reset_camera=False)
        except Exception:
            pass


def _label_anchor(centroid, mesh_center, diag, side, up=(0.0, 0.0, 1.0)):
    """Offset a label away from its loop so the text never covers it.
    Push outward from the mesh centre (off the surface) plus a sideways nudge
    (side = +1 / -1) so two nearby loops' labels separate instead of overlapping.
    """
    c = np.asarray(centroid, float)
    outward = c - np.asarray(mesh_center, float)
    n = np.linalg.norm(outward)
    outward = outward / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
    tangent = np.cross(outward, np.asarray(up, float))
    tn = np.linalg.norm(tangent)
    tangent = tangent / tn if tn > 1e-9 else np.array([1.0, 0.0, 0.0])
    off = 0.11 * diag
    return c + outward * off + side * tangent * off * 0.9


def _add_labels_with_leaders(plotter, targets, anchors, labels, color, name):
    """Place each label at an offset anchor with a thin leader line back to the
    loop (target), so labels stay clear of the rings while still pointing at them.
    """
    if not len(targets):
        return
    for j, (t, a) in enumerate(zip(targets, anchors)):
        try:
            plotter.add_mesh(pv.Line(t, a), color=color, line_width=2,
                             render_lines_as_tubes=True,
                             name=f"{name}_leader_{j}", reset_camera=False)
        except Exception:
            pass
    try:
        plotter.add_point_labels(
            np.array(anchors), labels, always_visible=True,
            point_size=8, font_size=14, text_color=color,
            shape='rounded_rect', shape_color='black',
            name=name, reset_camera=False)
    except TypeError:
        try:
            plotter.add_point_labels(
                np.array(anchors), labels, always_visible=True,
                point_size=8, font_size=14, name=name, reset_camera=False)
        except Exception:
            pass


class TunnelSelector:
    """Interactive cut-loop chooser.

    Each handle has up to two candidate cut loops (the dual pair). One is shown
    as GREEN 'Cut here', the other RED 'Avoid'. Clicking near a loop makes it the
    chosen cut loop for its handle; the chosen loops are re-exported on every
    change. The initial (suggested) choice comes from select_cut_loop, but since
    that auto-classifier is NOT reliable across meshes, the user is expected to
    confirm / override by clicking -- the suggestion is a starting point only.
    """

    def __init__(self, plotter, pts, pairs, mesh_path=None):
        self.pl = plotter
        self.pts = np.asarray(pts)
        self.mesh_path = mesh_path
        self.center = self.pts.mean(0)
        self.diag = float(np.linalg.norm(self.pts.max(0) - self.pts.min(0)))
        self.handles = []          # each: {"loops": [idx arrays], "sel": int}
        for pr in pairs:
            loops = [np.asarray(pr["cut"])]
            if pr.get("avoid") is not None:
                loops.append(np.asarray(pr["avoid"]))
            self.handles.append({"loops": loops, "sel": 0})
        self.export_txt = None
        self.redraw()
        self.export()

    def _iter_loops(self):
        for hi, h in enumerate(self.handles):
            for li, lp in enumerate(h["loops"]):
                yield hi, li, lp

    def focus_points(self):
        return [list(map(float, self.pts[h["loops"][h["sel"]]].mean(0)))
                for h in self.handles]

    def on_pick(self, *args):
        # extract the picked xyz from whatever pyvista passed
        point = None
        for a in args:
            try:
                arr = np.asarray(a, dtype=float).ravel()
            except Exception:
                continue
            if arr.size >= 3:
                point = arr[:3]
                break
        if point is None:
            return
        best = None
        for hi, li, lp in self._iter_loops():
            d = float(np.linalg.norm(self.pts[lp] - point, axis=1).min())
            if best is None or d < best[0]:
                best = (d, hi, li)
        if best is None:
            return
        _, hi, li = best
        if self.handles[hi]["sel"] != li:
            self.handles[hi]["sel"] = li
            self.redraw()
            self.export()

    def redraw(self):
        for hi, h in enumerate(self.handles):
            sel = h["sel"]
            cut_lp = h["loops"][sel]
            for li, lp in enumerate(h["loops"]):
                is_cut = (li == sel)
                _draw_loop(self.pl, self.pts[lp],
                           'lime' if is_cut else 'red',
                           f'sel_loop_{hi}_{li}', width=7 if is_cut else 4)
            cc = self.pts[cut_lp].mean(0)
            _add_labels_with_leaders(
                self.pl, [cc], [_label_anchor(cc, self.center, self.diag, +1)],
                ['Cut here'], 'lime', f'sel_cut_lbl_{hi}')
            at, aa, al = [], [], []
            for li, lp in enumerate(h["loops"]):
                if li == sel:
                    continue
                ac = self.pts[lp].mean(0)
                at.append(ac)
                aa.append(_label_anchor(ac, self.center, self.diag, -1))
                al.append('Avoid')
            _add_labels_with_leaders(self.pl, at, aa, al, 'red', f'sel_avoid_lbl_{hi}')
        try:
            self.pl.render()
        except Exception:
            pass

    def export(self):
        if not self.mesh_path:
            return
        try:
            from tunnel_loop_locator import export_loops_for_blender
            cut_loops = [h["loops"][h["sel"]] for h in self.handles]
            prefix = os.path.splitext(self.mesh_path)[0] + "_cut"
            _, self.export_txt = export_loops_for_blender(self.pts, cut_loops, prefix)
        except Exception as e:
            print(f"[tunnel] loop export failed: {e}", file=sys.stderr)


def add_tunnel_overlay(plotter, pv_mesh, genus, max_pins=5, mesh_path=None):
    """
    Overlay the topological cut loops on the plotter. Call when genus > 0.
    Returns a dict {rendered, tight, export_txt, n_loops, focus, selector} or False.

    Preferred path: render the DUAL loop pair per handle and return a
    TunnelSelector so the caller can enable click-to-choose. GREEN 'Cut here' is
    the suggested loop to remove, RED 'Avoid' the other member; the user clicks
    to confirm/override (the auto-suggestion is not reliable across meshes).
    Falls back to the single tight loop, then the loose tree-cotree loop, if the
    newer modules are unavailable.
    """
    export_txt = None

    # --- preferred: dual pair, interactive cut/avoid selection --------------
    pairs, pts = _dual_cut_pairs(pv_mesh, genus, max_pins=max_pins)
    if pairs and pts is not None:
        selector = TunnelSelector(plotter, pts, pairs, mesh_path=mesh_path)
        return {"rendered": True, "tight": True,
                "export_txt": selector.export_txt, "n_loops": len(pairs),
                "focus": selector.focus_points(), "selector": selector}

    # --- fallback 1: single tight loop --------------------------------------
    is_tight = True
    pins, loops, idx_loops, pts = _tight_cut_loops(pv_mesh, genus, max_pins=max_pins)

    if pins and loops and mesh_path:
        try:
            from tunnel_loop_locator import export_loops_for_blender
            prefix = os.path.splitext(mesh_path)[0] + "_cut"
            _, export_txt = export_loops_for_blender(pts, idx_loops, prefix)
        except Exception as e:
            print(f"[tunnel] loop export failed: {e}", file=sys.stderr)

    # --- fallback 2: loose tree-cotree loop ---------------------------------
    if not pins or not loops:
        is_tight = False
        pins, loops = _topological_tunnel_loops(pv_mesh, genus, max_pins=max_pins)

    if not pins or not loops:
        return False

    for i, loop_pts in enumerate(loops):
        _draw_loop(plotter, loop_pts, 'magenta', f'tunnel_loop_{i}')
    _add_labels(plotter, list(pins),
                ['Cut here' if is_tight else 'Tunnel?'] * len(pins),
                'magenta', 'tunnel_labels')

    plotter.render()
    return {"rendered": True, "tight": is_tight, "export_txt": export_txt,
            "n_loops": len(loops)}


# ---------------------------------------------------------------------------
# Standalone viewer — native pv.Plotter (no Qt embedding) to avoid the
# wglMakeCurrent / shared-GL-context crashes seen with pyvistaqt.QtInteractor.
# ---------------------------------------------------------------------------

def _build_legend(lines):
    """Join legend lines for on-canvas add_text."""
    return "\n".join(lines)


def _orient_camera_to(pl, mesh, issue_point):
    """Rotate the camera to look at the mesh from the side where issue_point is,
    keeping the framing distance chosen by reset_camera(). Without this the
    default view often opens on the opposite side of the head from the tunnel.
    """
    try:
        center = np.array(mesh.center, dtype=float)
        cam = pl.camera
        focal = np.array(cam.focal_point, dtype=float)
        dist = float(np.linalg.norm(np.array(cam.position, dtype=float) - focal))
        if dist <= 0:
            return
        outward = issue_point - center
        n = np.linalg.norm(outward)
        if n < 1e-9:
            return
        outward /= n
        # pick an up vector not parallel to the view direction
        up = (0.0, 0.0, 1.0)
        if abs(outward[2]) > 0.95:
            up = (0.0, 1.0, 0.0)
        new_pos = focal + outward * dist
        pl.camera_position = [tuple(new_pos), tuple(focal), up]
        pl.reset_camera()  # re-fit so the whole mesh stays framed from this side
    except Exception as e:
        print(f"[tunnel] camera orient skipped: {e}", file=sys.stderr)


def view_mesh_problems(mesh_path):
    """
    Open a native PyVista window showing all detected mesh problems:
      • Red lines    — boundary / non-manifold edges
      • Colour bands — disconnected components
      • Yellow faces — self-intersecting faces (requires pymeshlab)
      • Magenta loop + 'Tunnel?' pin — topological tunnels (genus > 0)

    Press R to reset the camera.  Close the window to exit.
    """
    if not os.path.exists(mesh_path):
        print(f"Error: file not found: {mesh_path}", file=sys.stderr)
        sys.exit(1)

    try:
        mesh = pv.read(mesh_path)
    except Exception as e:
        print(f"Error loading mesh: {e}", file=sys.stderr)
        sys.exit(1)

    pl = pv.Plotter(title=f"Mesh Problems — {os.path.basename(mesh_path)}")
    pl.set_background("#1d1d1d")

    # Base mesh — semi-transparent surface.
    # Recompute outward-pointing normals so a mesh with inverted/inconsistent
    # normals (the kind that renders solid black in MeshLab) still shades
    # correctly here; add ambient light so it can never go fully dark.
    try:
        mesh_disp = mesh.compute_normals(auto_orient_normals=True,
                                         consistent_normals=True, inplace=False)
    except Exception:
        mesh_disp = mesh
    pl.add_mesh(mesh_disp, color="#a0c4ff", opacity=0.65, show_edges=False,
                ambient=0.35, diffuse=0.6, specular=0.05, name="surface")

    legend_lines = []

    # ---- Boundary + non-manifold edges (red) --------------------------------
    try:
        problem_edges = mesh.extract_feature_edges(
            boundary_edges=True,
            non_manifold_edges=True,
            feature_edges=False,
            manifold_edges=False,
        )
        if problem_edges.n_points > 0:
            pl.add_mesh(problem_edges, color="red", line_width=3,
                        name="problem_edges", render_lines_as_tubes=True)
            legend_lines.append("Red lines: boundary/non-manifold edges")
        else:
            legend_lines.append("✓ No boundary or non-manifold edges")
    except Exception as e:
        legend_lines.append(f"Edge check error: {e}")

    # ---- Connected components (colour bands) --------------------------------
    try:
        labeled = mesh.connectivity(largest=False)
        n_regions = (int(labeled.get_array("RegionId").max()) + 1
                     if labeled.n_points > 0 else 1)
        if n_regions > 1:
            pl.add_mesh(labeled, scalars="RegionId", cmap="tab10",
                        opacity=0.7, show_scalar_bar=False, name="components")
            legend_lines.append(f"Colours: {n_regions} disconnected components")
        else:
            legend_lines.append("✓ Single connected component")
    except Exception as e:
        legend_lines.append(f"Component check error: {e}")

    # ---- Self-intersecting faces (yellow, requires pymeshlab) ---------------
    try:
        import pymeshlab
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(mesh_path)
        for fname in ('compute_selection_by_self_intersections_per_face',
                      'select_self_intersecting_faces'):
            try:
                ms.apply_filter(fname)
                break
            except Exception:
                continue
        n_si = ms.current_mesh().selected_face_number()
        if n_si > 0:
            face_ids = [i for i in range(mesh.n_cells)
                        if ms.current_mesh().face_is_selected(i)]
            si_mesh = mesh.extract_cells(face_ids) if face_ids else None
            if si_mesh and si_mesh.n_points > 0:
                pl.add_mesh(si_mesh, color="yellow", opacity=0.85,
                            name="si_faces")
                legend_lines.append(f"Yellow faces: {n_si} self-intersection(s)")
        else:
            legend_lines.append("✓ No self-intersecting faces")
    except Exception as e:
        legend_lines.append(f"Self-intersection check: {e}")

    # ---- Topological tunnels / handles (magenta loop, tree-cotree) ----------
    try:
        import mesh_inspector
        report = mesh_inspector.inspect_mesh(mesh_path)
        genus  = report["counts"].get("genus", 0)
        if genus > 0:
            print(f"[tunnel] genus={genus}; computing tight cut loop "
                  f"(may take ~10-15 s)...", flush=True)
            found = add_tunnel_overlay(pl, mesh, genus, mesh_path=mesh_path)
            if found and isinstance(found, dict) and found.get("tight"):
                msg = (f"{found['n_loops']} tunnel/handle(s) — each has TWO candidate "
                       f"cut loops (a dual pair).\n"
                       f"  GREEN 'Cut here' = chosen loop to delete+cap; RED 'Avoid' "
                       f"= the other.\n"
                       f"  CLICK a ring to choose which one to cut (auto-pick is a "
                       f"suggestion only).")
                if found.get("export_txt"):
                    msg += f"\n  Chosen cut loop exported: {os.path.basename(found['export_txt'])}"
                legend_lines.append(msg)
            elif found:
                legend_lines.append(
                    f"Magenta loop(s): {genus} topological tunnel/handle(s)\n"
                    f"  (loose loop threads the hole — follow it to the opening)"
                )
            else:
                legend_lines.append(
                    f"genus={genus} (tunnel detected) — loop extraction failed\n"
                    f"  Check mesh manually in Blender"
                )
    except Exception as e:
        legend_lines.append(f"Tunnel check: {e}")

    # ---- On-canvas legend ---------------------------------------------------
    legend_text = _build_legend(legend_lines)
    pl.add_text(legend_text, position="upper_left", font_size=10,
                color="white", name="legend")
    pl.add_text("Press R to reset camera", position="lower_left",
                font_size=9, color="#aaaaaa")

    pl.add_key_event('r', lambda: pl.reset_camera())

    # Enable click-to-choose for the cut loop, if a selector was created.
    _found = locals().get("found")
    selector = _found.get("selector") if isinstance(_found, dict) else None
    if selector is not None:
        def _on_pick(*a):
            selector.on_pick(*a)
        for kwargs in (
            dict(callback=_on_pick, left_clicking=True, show_message=False,
                 show_point=False, use_picker=True),
            dict(callback=_on_pick, left_clicking=True),
            dict(callback=_on_pick),
        ):
            try:
                pl.enable_point_picking(**kwargs)
                break
            except TypeError:
                continue
            except Exception as e:
                print(f"[tunnel] click-to-select unavailable: {e}", file=sys.stderr)
                break
        pl.add_text("Click a ring to choose the CUT loop (green)",
                    position="lower_right", font_size=9, color="#9fe6a0",
                    name="pick_hint")

    pl.reset_camera()
    # Face the side where the tunnel was detected (otherwise the default camera
    # often opens on the opposite side of the head from the issue).
    focus_pts = _found.get("focus") if isinstance(_found, dict) else None
    if focus_pts:
        _orient_camera_to(pl, mesh, np.array(focus_pts[0], dtype=float))

    pl.show()


def main():
    parser = argparse.ArgumentParser(
        description="Visualise mesh problems (holes, non-manifold edges, components, tunnels)."
    )
    parser.add_argument("mesh", help="Path to mesh file (.ply, .stl, etc.)")
    args = parser.parse_args()
    view_mesh_problems(args.mesh)


if __name__ == "__main__":
    main()
