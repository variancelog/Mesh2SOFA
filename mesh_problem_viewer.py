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
        return (pairs, pts, faces) if pairs else ([], None, None)
    except Exception as e:
        print(f"[tunnel] dual-pair path unavailable ({e}); using single loop",
              file=sys.stderr)
        return [], None, None


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

    def __init__(self, plotter, pts, pairs, mesh_path=None, faces=None,
                 on_success=None):
        self.pl = plotter
        self.pts = np.asarray(pts)
        self.faces = None if faces is None else np.asarray(faces)
        self.mesh_path = mesh_path
        self.on_success = on_success  # callable: invoked after a successful cut & cap
        self.center = self.pts.mean(0)
        self.diag = float(np.linalg.norm(self.pts.max(0) - self.pts.min(0)))
        self.active = 0             # keyboard-active handle index (Task 4)
        self.handles = []           # each: {"loops": [idx arrays], "sel": int}
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

    # ------------------------------------------------------------------
    # Keyboard navigation (Task 4)
    # ------------------------------------------------------------------

    def next_handle(self):
        """Cycle to the next handle (Tab key). No-op when there is only one."""
        if len(self.handles) > 1:
            self.active = (self.active + 1) % len(self.handles)
            self.redraw()

    def prev_handle(self):
        """Cycle to the previous handle (Shift+Tab). No-op when only one handle."""
        if len(self.handles) > 1:
            self.active = (self.active - 1) % len(self.handles)
            self.redraw()

    def toggle_active_loop(self):
        """Toggle the cut loop selection within the active handle (Space key)."""
        h = self.handles[self.active]
        if len(h["loops"]) > 1:
            h["sel"] = (h["sel"] + 1) % len(h["loops"])
            self.redraw()
            self.export()

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
        n_handles = len(self.handles)
        for hi, h in enumerate(self.handles):
            sel = h["sel"]
            cut_lp = h["loops"][sel]
            is_active = (hi == self.active)

            for li, lp in enumerate(h["loops"]):
                is_cut = (li == sel)
                # Active handle: wider rings so it stands out from inactive ones.
                if is_active:
                    width = 9 if is_cut else 5
                else:
                    # Inactive handles: draw dimmer/thinner so the active one
                    # is visually dominant.
                    width = 5 if is_cut else 3

                color = 'lime' if is_cut else 'red'
                if not is_active:
                    # Tint inactive handles slightly different to cue "not focused".
                    color = '#66cc66' if is_cut else '#cc6666'

                _draw_loop(self.pl, self.pts[lp], color,
                           f'sel_loop_{hi}_{li}', width=width)

            # Cut-loop label for this handle.
            cc = self.pts[cut_lp].mean(0)
            cut_label = 'Cut here' if not is_active else (
                f'Cut here  [active {hi + 1}/{n_handles}]'
                if n_handles > 1 else 'Cut here  [Space=toggle, C=apply]'
            )
            _add_labels_with_leaders(
                self.pl, [cc], [_label_anchor(cc, self.center, self.diag, +1)],
                [cut_label], 'lime' if is_active else '#66cc66',
                f'sel_cut_lbl_{hi}')

            # Avoid-loop labels.
            at, aa, al = [], [], []
            for li, lp in enumerate(h["loops"]):
                if li == sel:
                    continue
                ac = self.pts[lp].mean(0)
                at.append(ac)
                aa.append(_label_anchor(ac, self.center, self.diag, -1))
                al.append('Avoid')
            _add_labels_with_leaders(self.pl, at, aa, al,
                                     'red' if is_active else '#cc6666',
                                     f'sel_avoid_lbl_{hi}')

        # On-canvas keyboard hint (update every redraw so it stays current).
        if n_handles > 1:
            hint = (f"Active handle: {self.active + 1}/{n_handles}  "
                    f"[Tab=next  Space=toggle  C=apply cut & cap]")
        else:
            hint = "Space=toggle loop  C=apply cut & cap  drag=rotate"
        try:
            self.pl.add_text(hint, position="lower_right", font_size=9,
                             color="#9fe6a0", name="kb_hint")
        except Exception:
            pass

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

    def _status(self, text, color):
        try:
            self.pl.add_text(text, position="upper_right", font_size=10,
                             color=color, name="capstatus")
            self.pl.render()
        except Exception:
            pass

    def apply_cut_and_cap(self):
        """Delete the chosen cut loops' bands and cap the holes in-app, producing
        a watertight genus-0 mesh. Saves over mesh_path, writes a sentinel
        cutcap_report.json so the GUI can trigger auto re-inspect + repair, then
        calls self.on_success() to close the viewer. Falls back to the exported
        loops (manual Blender) on any failure, leaving the original file untouched."""
        if self.faces is None or not self.mesh_path:
            self._status("Cut & cap unavailable (no mesh data)", "#e6a09f")
            return
        try:
            from tunnel_loop_extractor import cut_and_cap_loops
            cut_loops = [h["loops"][h["sel"]] for h in self.handles]
            self._status("Applying cut & cap...", "#e6d79f")
            new_pts, new_faces = cut_and_cap_loops(self.pts, self.faces, cut_loops)

            faces_pv = np.hstack(
                [np.full((len(new_faces), 1), 3, dtype=np.int64),
                 new_faces.astype(np.int64)]).ravel()
            out = pv.PolyData(new_pts, faces_pv)
            out.save(self.mesh_path)

            # Write a sentinel file so the GUI knows cut & cap ran (not just
            # that the viewer was closed). The GUI picks this up via
            # _after_tunnel_viewer() and runs repair_aligned automatically.
            import json as _json
            sentinel = os.path.join(os.path.dirname(self.mesh_path),
                                    "cutcap_report.json")
            with open(sentinel, "w") as _f:
                _json.dump({"applied": True,
                            "mesh": os.path.basename(self.mesh_path)}, _f)

            self._status("Tunnel removed — closing and re-validating…", "#9fe6a0")
            print("[tunnel] cut & cap applied; mesh saved watertight (genus 0).",
                  flush=True)

            # Auto-close so the GUI can run the post-cap re-inspect + repair.
            if callable(self.on_success):
                try:
                    self.on_success()
                except Exception as e:
                    print(f"[tunnel] on_success callback failed: {e}", file=sys.stderr)
        except Exception as e:
            self._status(
                f"Auto cut & cap failed: {e}\n"
                "Use the exported loop for a manual Blender cut instead.",
                "#e6a09f")
            print(f"[tunnel] cut & cap failed: {e}", file=sys.stderr)


def _mesh_to_arrays(pv_mesh):
    """Triangulate+clean a pyvista mesh to (pts, faces) ndarrays. Do this on the
    MAIN thread (it uses VTK data filters) and hand the arrays to the worker so
    the worker stays VTK-free."""
    mesh = pv_mesh.triangulate().clean()
    pts = np.asarray(mesh.points, dtype=np.float64)
    faces = mesh.faces.reshape(-1, 4)[:, 1:].astype(np.int64)
    return pts, faces


def compute_tunnel_data(pts, faces, genus, *, max_pins=5, progress_cb=None):
    """Worker-safe (no VTK rendering): compute the cut-loop data to overlay.

    Returns a dict describing what to draw:
      {"mode":"pairs", pairs, pts, faces}  -- dual loop pairs (click-to-select)
      {"mode":"tight", idx_loops, pts, faces} -- single tight loops (export only)
      {"mode":"none"}
    Used by the viewer's background thread; pts/faces must already be the cleaned
    arrays (see _mesh_to_arrays). Heavy work (tree-cotree, tighten, dual loop) is
    pure numpy/scipy/networkx and is safe off the GUI thread.
    """
    try:
        from tunnel_loop_locator import select_cut_loop
        pairs = select_cut_loop(pts, faces, genus, max_loops=max_pins,
                                progress_cb=progress_cb)
        if pairs:
            return {"mode": "pairs", "pairs": pairs, "pts": pts, "faces": faces}
    except Exception as e:
        print(f"[tunnel] dual-pair path failed ({e})", file=sys.stderr)

    try:
        from tunnel_loop_extractor import extract_tight_cut_loops
        idx_loops = extract_tight_cut_loops(pts, faces, genus, max_pins=max_pins)
        if idx_loops:
            return {"mode": "tight", "idx_loops": idx_loops, "pts": pts, "faces": faces}
    except Exception as e:
        print(f"[tunnel] tight-loop path failed ({e})", file=sys.stderr)

    return {"mode": "none"}


def render_tunnel_data(plotter, data, mesh_path=None, on_success=None):
    """Main-thread: draw the computed tunnel data onto the plotter. Returns the
    same dict shape add_tunnel_overlay used (rendered, tight, export_txt,
    n_loops, focus, selector) or False if nothing to draw.

    on_success: optional callable forwarded to TunnelSelector — called when
    cut & cap succeeds so the hosting window can close itself and trigger GUI
    re-validation.
    """
    mode = data.get("mode")

    if mode == "pairs":
        pts, faces, pairs = data["pts"], data["faces"], data["pairs"]
        selector = TunnelSelector(plotter, pts, pairs, mesh_path=mesh_path,
                                  faces=faces, on_success=on_success)
        return {"rendered": True, "tight": True,
                "export_txt": selector.export_txt, "n_loops": len(pairs),
                "focus": selector.focus_points(), "selector": selector}

    if mode == "tight":
        pts, idx_loops = data["pts"], data["idx_loops"]
        export_txt = None
        if mesh_path:
            try:
                from tunnel_loop_locator import export_loops_for_blender
                prefix = os.path.splitext(mesh_path)[0] + "_cut"
                _, export_txt = export_loops_for_blender(pts, idx_loops, prefix)
            except Exception as e:
                print(f"[tunnel] loop export failed: {e}", file=sys.stderr)
        loops = [pts[lp] for lp in idx_loops]
        pins = [pts[lp].mean(0) for lp in idx_loops]
        for i, loop_pts in enumerate(loops):
            _draw_loop(plotter, loop_pts, 'magenta', f'tunnel_loop_{i}')
        _add_labels(plotter, pins, ['Cut here'] * len(pins), 'magenta', 'tunnel_labels')
        plotter.render()
        return {"rendered": True, "tight": True, "export_txt": export_txt,
                "n_loops": len(loops),
                "focus": [list(map(float, p)) for p in pins]}

    return False


def add_tunnel_overlay(plotter, pv_mesh, genus, max_pins=5, mesh_path=None):
    """
    Overlay the topological cut loops on the plotter (synchronous; main thread).
    Kept for callers that want a one-shot overlay; the embedded viewer instead
    uses compute_tunnel_data (in a worker) + render_tunnel_data (main thread).
    Returns {rendered, tight, export_txt, n_loops, focus, selector} or False.
    """
    pts, faces = _mesh_to_arrays(pv_mesh)
    data = compute_tunnel_data(pts, faces, genus, max_pins=max_pins)
    return render_tunnel_data(plotter, data, mesh_path=mesh_path)


# ---------------------------------------------------------------------------
# Standalone viewer — PySide6 + pyvistaqt.QtInteractor so the window appears
# immediately with the base mesh + a progress bar while the (slow) cut-loop
# computation runs in a background QThread. Uses the same QtInteractor pattern
# proven in align_head.py / _vtk_viewer.py. The OLD native-pv.Plotter wgl crash
# was specific to that earlier embedding attempt; the worker here never touches
# VTK/GL off the main thread (it only runs numpy/scipy/networkx).
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


def _add_static_overlays(pl, mesh, mesh_path):
    """Render the FAST problem overlays (base mesh, boundary/non-manifold edges,
    components, self-intersections) on the plotter and return legend lines.
    These are quick, so they go up immediately while the tunnel loops compute."""
    # Base mesh — semi-transparent surface. Recompute outward-pointing normals so
    # a mesh with inverted/inconsistent normals (solid black in MeshLab) still
    # shades correctly; ambient light so it can never go fully dark.
    try:
        mesh_disp = mesh.compute_normals(auto_orient_normals=True,
                                         consistent_normals=True, inplace=False)
    except Exception:
        mesh_disp = mesh
    pl.add_mesh(mesh_disp, color="#a0c4ff", opacity=0.65, show_edges=False,
                ambient=0.35, diffuse=0.6, specular=0.05, name="surface")

    legend_lines = []
    try:
        problem_edges = mesh.extract_feature_edges(
            boundary_edges=True, non_manifold_edges=True,
            feature_edges=False, manifold_edges=False)
        if problem_edges.n_points > 0:
            pl.add_mesh(problem_edges, color="red", line_width=3,
                        name="problem_edges", render_lines_as_tubes=True)
            legend_lines.append("Red lines: boundary/non-manifold edges")
        else:
            legend_lines.append("[OK] No boundary or non-manifold edges")
    except Exception as e:
        legend_lines.append(f"Edge check error: {e}")

    try:
        labeled = mesh.connectivity(largest=False)
        n_regions = (int(labeled.get_array("RegionId").max()) + 1
                     if labeled.n_points > 0 else 1)
        if n_regions > 1:
            pl.add_mesh(labeled, scalars="RegionId", cmap="tab10",
                        opacity=0.7, show_scalar_bar=False, name="components")
            legend_lines.append(f"Colours: {n_regions} disconnected components")
        else:
            legend_lines.append("[OK] Single connected component")
    except Exception as e:
        legend_lines.append(f"Component check error: {e}")

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
                pl.add_mesh(si_mesh, color="yellow", opacity=0.85, name="si_faces")
                legend_lines.append(f"Yellow faces: {n_si} self-intersection(s)")
        else:
            legend_lines.append("[OK] No self-intersecting faces")
    except Exception as e:
        legend_lines.append(f"Self-intersection check: {e}")

    return legend_lines


def _enable_loop_keys(pl, selector):
    """Wire keyboard controls for loop selection and cut & cap.

    Left-click is intentionally NOT wired for selection — it reverts to pure
    camera rotation, eliminating the fight between drag-rotate and pick-select.

    Keyboard layout (routed through the Qt eventFilter on the window):
      Tab / Shift+Tab — cycle handles           (genus > 1 only)
      Space           — toggle cut loop in the active pair
      C               — Apply Cut & Cap

    The Tab/Space keys are intercepted at the Qt level (see _ProblemViewerWindow
    eventFilter) so they never reach pyvista's VTK key handler or Qt's focus
    traversal. C stays here on the pyvista key event for compatibility.
    """
    pl.add_key_event('c', lambda: selector.apply_cut_and_cap())


def view_mesh_problems(mesh_path):
    """
    Open a PySide6 window showing all detected mesh problems:
      - Red lines    : boundary / non-manifold edges
      - Colour bands : disconnected components
      - Yellow faces : self-intersecting faces (requires pymeshlab)
      - Green/red rings : topological tunnel cut loops (genus > 0)

    The window appears immediately with the base mesh + the fast overlays, and a
    progress bar runs while the (slow) cut-loop computation happens in a worker
    thread. Press R to reset the camera; close the window to exit.
    """
    if not os.path.exists(mesh_path):
        print(f"Error: file not found: {mesh_path}", file=sys.stderr)
        sys.exit(1)

    os.environ.setdefault("QT_API", "pyside6")
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window_cls = _build_qt_classes()
    win = window_cls(mesh_path)
    win.show()
    app.exec()


BG_COLOR     = "#1d1d1d"
PANEL_COLOR  = "#2b2b2b"
TEXT_COLOR   = "#ffffff"
ACCENT_COLOR = "#3B8ED0"
SUCCESS_COLOR = "#2cc985"
ERROR_COLOR  = "#c0392b"


def _build_qt_classes():
    """Define the Qt window + worker lazily so importing this module does not
    require PySide6/pyvistaqt (keeps the CLI helpers and tests import-light)."""
    from PySide6 import QtCore, QtWidgets
    from pyvistaqt import QtInteractor

    class _TunnelWorker(QtCore.QThread):
        progress = QtCore.Signal(str)
        done = QtCore.Signal(object)

        def __init__(self, pts, faces, genus):
            super().__init__()
            self._pts, self._faces, self._genus = pts, faces, genus

        def run(self):
            try:
                data = compute_tunnel_data(self._pts, self._faces, self._genus,
                                           progress_cb=self.progress.emit)
            except Exception as e:
                print(f"[tunnel] worker error: {e}", file=sys.stderr)
                data = {"mode": "none"}
            self.done.emit(data)

    class _ProblemViewerWindow(QtWidgets.QMainWindow):
        def __init__(self, mesh_path):
            super().__init__()
            self.mesh_path = mesh_path
            self.setWindowTitle(f"Mesh Problems - {os.path.basename(mesh_path)}")
            self.resize(1300, 800)

            self.setStyleSheet(f"""
                QMainWindow {{ background-color: {BG_COLOR}; }}
                QWidget {{ background-color: {BG_COLOR}; color: {TEXT_COLOR};
                           font-family: 'Segoe UI', sans-serif; }}
                QFrame#ControlPanel {{ background-color: {PANEL_COLOR}; border-radius: 10px; }}
                QPushButton {{ background-color: {ACCENT_COLOR}; border-radius: 5px;
                               padding: 8px; font-weight: bold; color: {TEXT_COLOR}; }}
                QPushButton:disabled {{ background-color: #444; color: #888; }}
                QProgressBar {{ border: none; background: #444; border-radius: 4px; }}
                QProgressBar::chunk {{ background: {ACCENT_COLOR}; border-radius: 4px; }}
            """)

            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            main_layout = QtWidgets.QHBoxLayout(central)
            main_layout.setContentsMargins(0, 0, 0, 0)
            main_layout.setSpacing(0)

            # Left: plotter container
            self.plotter_container = QtWidgets.QWidget()
            pc_layout = QtWidgets.QVBoxLayout(self.plotter_container)
            pc_layout.setContentsMargins(0, 0, 0, 0)
            self.plotter = QtInteractor(self.plotter_container)
            pc_layout.addWidget(self.plotter)
            main_layout.addWidget(self.plotter_container, stretch=3)

            # Keyboard state — selector is None until _on_done fires.
            self._selector = None
            # Intercept Tab/Space at the Qt level before VTK or focus-traversal
            # can consume them.  installEventFilter on the interactor widget.
            self.plotter.installEventFilter(self)

            # Central progress overlay (child of plotter_container, positioned by resizeEvent)
            self._overlay = QtWidgets.QFrame(self.plotter_container)
            self._overlay.setStyleSheet(
                "background: rgba(30,30,30,210); border-radius: 10px;")
            ov_lay = QtWidgets.QVBoxLayout(self._overlay)
            self._overlay_label = QtWidgets.QLabel("Computing cut loops...")
            self._overlay_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: white; background: transparent;")
            self._overlay_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._overlay_label.setWordWrap(True)
            ov_bar = QtWidgets.QProgressBar()
            ov_bar.setRange(0, 0)
            ov_bar.setFixedHeight(8)
            ov_lay.addWidget(self._overlay_label)
            ov_lay.addWidget(ov_bar)
            self._overlay.hide()

            # Right: control panel
            panel = QtWidgets.QFrame()
            panel.setObjectName("ControlPanel")
            panel.setFixedWidth(300)
            panel_layout = QtWidgets.QVBoxLayout(panel)
            panel_layout.setContentsMargins(12, 16, 12, 16)
            panel_layout.setSpacing(10)
            main_layout.addWidget(panel, stretch=1)

            lbl_title = QtWidgets.QLabel(f"Mesh Problems\n{os.path.basename(mesh_path)}")
            lbl_title.setStyleSheet("font-size: 13px; font-weight: bold;")
            lbl_title.setWordWrap(True)
            panel_layout.addWidget(lbl_title)

            lbl_instr = QtWidgets.QLabel(
                "Space — toggle cut loop in active pair\n"
                "Tab / Shift+Tab — cycle handles (genus > 1)\n"
                "C — apply Cut & Cap\n"
                "Drag — rotate camera (no click conflict)")
            lbl_instr.setWordWrap(True)
            lbl_instr.setStyleSheet(f"color: #aaa; font-size: 11px; background: transparent;")
            panel_layout.addWidget(lbl_instr)

            self._panel_status = QtWidgets.QLabel("Loading mesh...")
            self._panel_status.setWordWrap(True)
            self._panel_status.setStyleSheet(
                f"color: #ccc; font-size: 11px; background: transparent;")
            panel_layout.addWidget(self._panel_status)

            panel_layout.addStretch()

            self._btn_cut = QtWidgets.QPushButton("Apply Cut && Cap  [C]")
            self._btn_cut.setEnabled(False)
            panel_layout.addWidget(self._btn_cut)

            self._btn_reset = QtWidgets.QPushButton("Reset Camera  [R]")
            self._btn_reset.setEnabled(False)
            self._btn_reset.clicked.connect(lambda: self.plotter.reset_camera())
            panel_layout.addWidget(self._btn_reset)

            self._btn_export = QtWidgets.QPushButton("Export Loops for Blender")
            self._btn_export.setEnabled(False)
            panel_layout.addWidget(self._btn_export)

            self._btn_close = QtWidgets.QPushButton("Save && Close")
            self._btn_close.setEnabled(False)
            self._btn_close.clicked.connect(self.close)
            panel_layout.addWidget(self._btn_close)

            # Status bar: slim label only (overlay is the prominent indicator)
            self.status_label = QtWidgets.QLabel("Loading mesh...")
            self.statusBar().addWidget(self.status_label, 1)

            self.plotter.set_background(BG_COLOR)

            try:
                self.mesh = pv.read(mesh_path)
            except Exception as e:
                self.status_label.setText(f"Error loading mesh: {e}")
                self._panel_status.setText(f"Error: {e}")
                return

            self.legend_lines = _add_static_overlays(self.plotter, self.mesh, mesh_path)
            self._refresh_legend()
            self.plotter.add_text("Press R to reset camera", position="lower_left",
                                  font_size=9, color="#aaaaaa", name="resethint")
            self.plotter.add_key_event('r', lambda: self.plotter.reset_camera())
            self.plotter.reset_camera()

            # genus check, then launch the worker for the slow loop compute
            genus = 0
            try:
                import mesh_inspector
                genus = mesh_inspector.inspect_mesh(mesh_path)["counts"].get("genus", 0)
            except Exception as e:
                self.legend_lines.append(f"Tunnel check: {e}")
                self._refresh_legend()

            if genus <= 0:
                self.status_label.setText("No tunnels (genus 0).")
                self._panel_status.setText("No tunnels detected.")
                self._btn_reset.setEnabled(True)
                self._btn_close.setEnabled(True)
                return

            self.status_label.setText(f"genus={genus}: computing cut loops...")
            self._panel_status.setText(f"genus={genus}: computing cut loops...")
            pts, faces = _mesh_to_arrays(self.mesh)
            self._worker = _TunnelWorker(pts, faces, genus)
            self._worker.progress.connect(self._on_progress)
            self._worker.done.connect(self._on_done)
            self._worker.start()
            self._overlay.show()
            self._overlay.raise_()

        def eventFilter(self, obj, event):
            """Intercept Tab/Shift+Tab/Space key presses on the plotter widget
            for keyboard loop navigation, before Qt focus-traversal or VTK can
            consume them."""
            if (self._selector is not None
                    and event.type() == QtCore.QEvent.Type.KeyPress):
                key = event.key()
                Qt = QtCore.Qt
                if key == Qt.Key.Key_Space:
                    self._selector.toggle_active_loop()
                    return True
                if key == Qt.Key.Key_Tab:
                    self._selector.next_handle()
                    return True
                # Shift+Tab arrives as Key_Backtab in Qt.
                if key == Qt.Key.Key_Backtab:
                    self._selector.prev_handle()
                    return True
            return super().eventFilter(obj, event)

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            if hasattr(self, '_overlay'):
                r = self.plotter_container.rect()
                w, h = 400, 80
                self._overlay.setGeometry(
                    (r.width() - w) // 2, (r.height() - h) // 2, w, h)
                self._overlay.raise_()

        def _refresh_legend(self):
            self.plotter.add_text(_build_legend(self.legend_lines),
                                  position="upper_left", font_size=10,
                                  color="white", name="legend")

        def _on_progress(self, msg):
            self.status_label.setText(msg)
            self._overlay_label.setText(msg)
            self._panel_status.setText(msg)

        def _on_done(self, data):
            self._overlay.hide()
            self._btn_reset.setEnabled(True)
            self._btn_close.setEnabled(True)

            found = render_tunnel_data(self.plotter, data, mesh_path=self.mesh_path,
                                       on_success=self.close)
            self._selector = found.get("selector") if isinstance(found, dict) else None
            if found and self._selector is not None:
                sel = self._selector
                _enable_loop_keys(self.plotter, sel)
                self._btn_cut.setEnabled(True)
                self._btn_cut.clicked.connect(sel.apply_cut_and_cap)
                self._btn_export.setEnabled(True)
                self._btn_export.clicked.connect(sel.export)
                msg = (f"{found['n_loops']} tunnel/handle(s) — dual pair shown. "
                       "GREEN = cut, RED = avoid. "
                       "Space=toggle, Tab=next handle, C=apply cut & cap.")
                if found.get("export_txt"):
                    msg += f" Exported: {os.path.basename(found['export_txt'])}"
                self.legend_lines.append(msg)
                self.status_label.setText(
                    "Space=toggle cut loop  Tab=next handle  C=apply cut & cap")
                self._panel_status.setText(
                    f"{found['n_loops']} tunnel(s) found.\n"
                    "Space=toggle  Tab=next handle  C=apply")
            elif found:
                self.legend_lines.append(
                    f"{found['n_loops']} tunnel loop(s) shown (magenta). "
                    "Export written for a manual Blender cut.")
                self.status_label.setText("Tunnel loop(s) located.")
                self._panel_status.setText(f"{found['n_loops']} tunnel loop(s) shown.")
            else:
                self.legend_lines.append(
                    "Tunnel detected but loop extraction failed - check in Blender.")
                self.status_label.setText("Loop extraction failed.")
                self._panel_status.setText("Loop extraction failed.")
            self._refresh_legend()

            focus_pts = found.get("focus") if isinstance(found, dict) else None
            if focus_pts:
                _orient_camera_to(self.plotter, self.mesh,
                                  np.array(focus_pts[0], dtype=float))

    return _ProblemViewerWindow


def main():
    parser = argparse.ArgumentParser(
        description="Visualise mesh problems (holes, non-manifold edges, components, tunnels)."
    )
    parser.add_argument("mesh", help="Path to mesh file (.ply, .stl, etc.)")
    args = parser.parse_args()
    view_mesh_problems(args.mesh)


if __name__ == "__main__":
    main()
