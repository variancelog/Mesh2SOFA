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

# --- LOOP STYLING ---
COLOR_CUT_ACTIVE = "#F01818"
COLOR_CUT_INACTIVE = "#CC6161"
COLOR_IGNORE_ACTIVE = "#546EB0" 
COLOR_IGNORE_INACTIVE = "#7483B0"
COLOR_MESH = "#AFA79C"
COLOR_BLOCKED = "#6E6E6E"   # loop B that failed the locate-time sever check

# --- OTHER COLORS
COLOR_PALE_YELLOW = "#e6d79f"
COLOR_PALE_GREEN = "#9fe6bf"
COLOR_CANVAS_HINT = "#DDDDDD" 

# --- MESH SETTINGS ---
MESH_OPACITY = 1
MESH_DIFFUSE = .7
MESH_AMBIENT = 0.4
MESH_SPECULAR = .15

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
    Returns the actor names added, so the caller can remove them on redraw.
    """
    names = []
    if not len(targets):
        return names
    for j, (t, a) in enumerate(zip(targets, anchors)):
        try:
            plotter.add_mesh(pv.Line(t, a), color=color, line_width=2,
                             render_lines_as_tubes=True,
                             name=f"{name}_leader_{j}", reset_camera=False)
            names.append(f"{name}_leader_{j}")
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
    names.append(name)
    return names


def _write_ply_tmp(result, path):
    """Write (pts, faces) as a binary PLY to `path` (the temp file of the atomic
    save; it must end in .ply, see project_store.tmp_path_for), then re-read it and confirm the face count, so a truncated write can never
    be os.replace()d over the real mesh."""
    from vtkmodules.vtkIOPLY import vtkPLYWriter
    new_pts, new_faces = result
    faces_pv = np.hstack([np.full((len(new_faces), 1), 3, dtype=np.int64),
                          np.asarray(new_faces, dtype=np.int64)]).ravel()
    out = pv.PolyData(np.asarray(new_pts, dtype=np.float64), faces_pv)
    w = vtkPLYWriter()
    w.SetFileName(path)
    w.SetInputData(out)
    w.SetFileTypeToBinary()
    if not w.Write() or w.GetErrorCode():
        raise IOError(f"could not write {os.path.basename(path)}")
    back = pv.PLYReader(path).read()   # explicit reader: any extension
    if back.n_cells != len(new_faces):
        raise IOError(f"{os.path.basename(path)} read back with {back.n_cells} "
                      f"faces, expected {len(new_faces)}")


# ---------------------------------------------------------------------------
# Plain-language text and camera maths for the fixer panel (stage 2).
#
# Everything here is pure (no Qt / GL) so it can be unit-tested headlessly
# (tests/test_tunnel_ui.py). The two technical loop classes keep their names
# in code and tooltips only; the user sees "Fill the hole" / "Remove the
# bridge" (TUNNEL / HANDLE as decided by classify_handle_tunnel).
# ---------------------------------------------------------------------------
OPTION_TITLE = {"TUNNEL": "Fill the hole", "HANDLE": "Remove the bridge"}
OPTION_SUBTITLE = {"TUNNEL": "Plugs the see-through channel flush",
                   "HANDLE": "Cuts away the thin strand of material"}
OPTION_TOOLTIP = {
    "TUNNEL": ("Technical name: TUNNEL loop. The ring encircles air, so cutting "
               "along it and capping the two openings fills the see-through "
               "channel flush with the surface."),
    "HANDLE": ("Technical name: HANDLE loop. The ring encircles solid material, "
               "so cutting along it severs the thin strand (the 'bridge') and "
               "caps the two stumps."),
}
UNKNOWN_TOOLTIP = ("The two rings could not be told apart automatically "
                   "(classifier result UNKNOWN), so they are numbered instead.")
UNAVAILABLE_TEXT = "Not available for this defect"

QUESTION_TEXT = ("Which is the scanning mistake: the see-through hole, or the "
                 "strand of material next to it?")
RULE_OF_THUMB = ("Usually it's the hole, so 'Fill the hole' is pre-selected. "
                 "Choose 'Remove the bridge' if the strand looks like stray "
                 "material that isn't part of the ear.")
UNKNOWN_WARNING = ("Couldn't tell which is the hole and which is the bridge "
                   "- look closely before choosing.")
SINGLE_UNKNOWN_WARNING = ("Only one way to fix this defect is available, and we "
                          "couldn't tell whether it fills the hole or removes "
                          "the bridge. Look closely before applying.")
TIGHT_MODE_NOTE = ("These loops can't be cut inside the app: the safe automatic "
                   "check did not pass for them, so Apply is disabled and your "
                   "mesh will not be changed. Use 'Export Loops for Blender' "
                   "and make the cut there.")
NO_LOOPS_NOTE = ("A safe way to fix this automatically could not be found. "
                 "Your mesh has not been changed. Close this window and fix the "
                 "hole in Blender or another mesh tool, then re-align.")

CANVAS_HINT = ("Drag = rotate    Z = zoom to defect    H = whole head    "
               "X = see-through")
# Measured on this machine (zzmeshes genus_1/2/3: 5 s / 25 s / 37 s, plus a few
# seconds to load the mesh). Re-measure if the loop finder changes.
LOOP_FIND_HINT = "This usually takes under a minute (longer with several defects)."
CUT_HINT = "This usually takes about 15 seconds. Please don't close this window."

# Camera framing for "zoom to defect": distance = DEFECT_ZOOM_SPANS x the span
# (bounding-box diagonal) of the defect's loops, clamped to a fraction of the
# whole-mesh diagonal so a 3 mm ring is still seen in context and a head-wrapping
# loop never goes further out than the whole-head view.
DEFECT_ZOOM_SPANS = 3.0
DEFECT_ZOOM_MIN_FRAC = 0.10
DEFECT_ZOOM_MAX_FRAC = 0.80
SEE_THROUGH_OPACITY = 0.4


def option_text(label, index):
    """(title, subtitle) for a loop option. `label` is the classifier label
    ("TUNNEL" / "HANDLE" / "UNKNOWN"/None); `index` is the 0-based card slot,
    used for the neutral "Option 1" / "Option 2" fallback."""
    if label in OPTION_TITLE:
        return OPTION_TITLE[label], OPTION_SUBTITLE[label]
    return f"Option {index + 1}", ""


def defect_title(active, total):
    return f"Defect {active + 1} of {total}"


def remaining_defects_note(shown, genus):
    """Honest text when fewer defects are offered than the mesh has (genus).
    None when everything is shown (or genus is unknown)."""
    if genus is None or shown >= genus:
        return None
    return (f"Showing {shown} of {genus} defects. The rest will be found "
            "after these are fixed.")


def option_cards(labels, blocked_reasons, sel):
    """Two card descriptions for one defect.

    labels: classifier labels of the AVAILABLE loops (1 or 2 entries);
    blocked_reasons: technical reasons for loops that failed the locate-time
    check (they fill the second card, disabled); sel: index of the selected card.
    A lone available loop has no classifier label (the classifier needs both
    loops), so it is shown as UNKNOWN: "Option 1"."""
    cards = []
    for i in range(2):
        if i < len(labels):
            label = labels[i]
            title, sub = option_text(label, i)
            tip = OPTION_TOOLTIP.get(label, UNKNOWN_TOOLTIP)
            cards.append({"title": title, "subtitle": sub, "enabled": True,
                          "tooltip": tip, "selected": i == sel})
        else:
            tip = (blocked_reasons[i - len(labels)]
                   if i - len(labels) < len(blocked_reasons) else
                   "No second loop could be generated for this defect.")
            cards.append({"title": f"Option {i + 1}", "subtitle": UNAVAILABLE_TEXT,
                          "enabled": False, "tooltip": tip, "selected": False})
    return cards


def defect_notes(labels, shown, genus):
    """Warning lines for the panel: the UNKNOWN / single-option warning for the
    current defect (`labels` = classifier labels of its available loops), then
    the "k of g" note."""
    notes = []
    if len(labels) == 1:
        if labels[0] not in OPTION_TITLE:
            notes.append(SINGLE_UNKNOWN_WARNING)
    elif any(lb not in OPTION_TITLE for lb in labels):
        notes.append(UNKNOWN_WARNING)
    more = remaining_defects_note(shown, genus)
    if more:
        notes.append(more)
    return notes


def friendly_progress(msg):
    """Translate a locator progress message into plain words (no jargon). The
    locator reports "k/n" counts in some messages; they become "defect k of n"."""
    import re
    m = re.search(r"(\d+)\s*/\s*(\d+)", msg or "")
    k, n = (m.group(1), m.group(2)) if m else (None, None)
    low = (msg or "").lower()
    if "tree-cotree" in low or "building" in low:
        return "Finding the defect..."
    if "tightening" in low:
        return f"Finding defect {k} of {n}..." if k else "Finding the defect..."
    if "orienting" in low:
        return "Working out both ways to fix it..."
    if "dual loop retry" in low:
        return "Working out both ways to fix it..."
    if "dual loop" in low:
        return (f"Working out both ways to fix defect {k} of {n}..."
                if k else "Working out both ways to fix it...")
    if "classifying" in low:
        return (f"Checking which way is which (defect {k} of {n})..."
                if k else "Checking which way is which...")
    return "Working..."


def defect_camera_pose(loop_point_arrays, mesh_center, mesh_diag):
    """Camera pose that frames one defect.

    focal = centroid of the loop pair (mean of the loops' centroids);
    distance = DEFECT_ZOOM_SPANS x bbox diagonal of all the defect's loop points,
    clamped to [MIN_FRAC, MAX_FRAC] x the whole-mesh diagonal;
    direction = outward from the mesh centre through the focal point (looking
    at the defect from outside the head, whatever side it is on).
    Returns (position, focal, view_up, distance) as numpy arrays / float."""
    arrays = [np.asarray(a, dtype=float) for a in loop_point_arrays if len(a)]
    focal = np.mean([a.mean(axis=0) for a in arrays], axis=0)
    allp = np.vstack(arrays)
    span = float(np.linalg.norm(allp.max(axis=0) - allp.min(axis=0)))
    dist = float(np.clip(DEFECT_ZOOM_SPANS * span,
                         DEFECT_ZOOM_MIN_FRAC * mesh_diag,
                         DEFECT_ZOOM_MAX_FRAC * mesh_diag))
    outward = focal - np.asarray(mesh_center, dtype=float)
    n = np.linalg.norm(outward)
    outward = outward / n if n > 1e-9 else np.array([0.0, -1.0, 0.0])
    up = np.array([0.0, 0.0, 1.0])
    if abs(outward[2]) > 0.95:
        up = np.array([0.0, 1.0, 0.0])
    return focal + outward * dist, focal, up, dist


class TunnelSelector:
    """Interactive fix chooser for see-through holes (topological tunnels).

    Each defect has up to two candidate loops (the dual pair). The user picks
    one per defect: "Fill the hole" (TUNNEL loop: cutting it plugs the channel
    flush) or "Remove the bridge" (HANDLE loop: cutting it severs the thin
    strand), as labelled by select_cut_loop's linking-number classifier (reliable
    -- settled 2026-07-04). The TUNNEL loop is pre-selected, but which one is the
    RIGHT fix depends on which side is the scan artifact, so the user confirms or
    switches before applying. The selected loop is drawn red, the other blue; a
    loop B that failed the locate-time sever check is drawn grey and can never be
    selected. When the classifier could not label the pair the options are just
    "Option 1" / "Option 2".

    The window drives this through select_option / set_active / zoom_to_defect /
    whole_head / set_see_through; Space / Tab / Shift+Tab / C map to
    toggle_active_loop / next_handle / prev_handle / request_apply. Hooks:
      on_change()        -- the selection or active defect changed (panel refresh)
      status_cb(text, c) -- show a status line (defaults to a canvas text)
      job_runner(job, finish) -- run `job()` off the GUI thread and call
                         finish(error_or_None) back on it (defaults to running
                         the job inline, which is what the headless tests use).
    """

    def __init__(self, plotter, pts, pairs, mesh_path=None, faces=None,
                 on_success=None, confirm_cb=None, busy_cb=None,
                 genus=None, on_change=None, status_cb=None, job_runner=None):
        self.pl = plotter
        self.pts = np.asarray(pts)
        self.faces = None if faces is None else np.asarray(faces)
        self.mesh_path = mesh_path
        self.on_success = on_success  # callable: invoked after a successful fix
        # confirm_cb(n_defects, on_confirm): shows the confirm step and calls
        # on_confirm() only if the user confirms. None -> apply without asking
        # (headless callers only; the window always supplies one).
        self.confirm_cb = confirm_cb
        self.busy_cb = busy_cb      # busy_cb(bool): window enables/disables the panel
        self.genus = genus          # mesh genus, for the "k of g defects" note
        self.on_change = on_change
        self.status_cb = status_cb
        self.job_runner = job_runner
        self.busy = False           # True while a cut is running
        self.see_through = False
        self.center = self.pts.mean(0)
        self.diag = float(np.linalg.norm(self.pts.max(0) - self.pts.min(0)))
        self.active = 0             # active defect index
        # each: {"loops": [idx arrays], "labels": [str], "sel": int,
        #        "blocked": [(idx array, reason)]}
        self.handles = []
        for pr in pairs:
            pr_labels = pr.get("labels") or {}
            loops = [np.asarray(pr["cut"])]
            labels = [pr_labels.get("cut") or "UNKNOWN"]
            if pr.get("avoid") is not None:
                loops.append(np.asarray(pr["avoid"]))
                labels.append(pr_labels.get("avoid") or "UNKNOWN")
            blocked = []
            if pr.get("B_unavailable") is not None:
                blocked.append((np.asarray(pr["B_unavailable"]),
                                pr.get("B_reason") or "does not cut cleanly"))
            self.handles.append({"loops": loops, "labels": labels, "sel": 0,
                                 "blocked": blocked})
        self.export_txt = None
        self._label_names = []
        self.redraw()
        self.export()

    # ------------------------------------------------------------------
    # Text for the panel (pure; see option_cards / defect_notes)
    # ------------------------------------------------------------------
    def unavailable_messages(self):
        """Technical notes (with the reason) for second loops that are not
        offered; the panel shows plain text, this is for the console log."""
        return [f"Defect {hi + 1}: the second loop is unavailable ({reason})."
                for hi, h in enumerate(self.handles)
                for (_lp, reason) in h["blocked"]]

    def defect_title(self):
        return defect_title(self.active, len(self.handles))

    def cards(self, hi=None):
        h = self.handles[self.active if hi is None else hi]
        return option_cards(h["labels"], [r for (_lp, r) in h["blocked"]], h["sel"])

    def notes(self):
        h = self.handles[self.active]
        return defect_notes(h["labels"], len(self.handles), self.genus)

    def is_classified(self, hi=None):
        h = self.handles[self.active if hi is None else hi]
        return len(h["labels"]) == 2 and all(lb in OPTION_TITLE for lb in h["labels"])

    def choice_summary(self):
        """One line per defect naming the chosen option (for the confirm step)."""
        out = []
        for hi, h in enumerate(self.handles):
            title, _ = option_text(h["labels"][h["sel"]], h["sel"])
            out.append(f"Defect {hi + 1}: {title}")
        return out

    def _iter_loops(self):
        for hi, h in enumerate(self.handles):
            for li, lp in enumerate(h["loops"]):
                yield hi, li, lp

    def _changed(self):
        if callable(self.on_change):
            try:
                self.on_change()
            except Exception as e:
                print(f"[tunnel] on_change failed: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Navigation / selection (keys and panel buttons share these)
    # ------------------------------------------------------------------
    def set_active(self, idx, zoom=True):
        """Make defect `idx` the active one; re-frames the camera on it."""
        if not 0 <= idx < len(self.handles) or idx == self.active:
            return
        self.active = idx
        self.redraw()
        if zoom:
            self.zoom_to_defect()
        self._changed()

    def next_handle(self):
        """Next defect (Tab / Next button). No-op when there is only one."""
        if len(self.handles) > 1:
            self.set_active((self.active + 1) % len(self.handles))

    def prev_handle(self):
        """Previous defect (Shift+Tab / Prev button). No-op when only one."""
        if len(self.handles) > 1:
            self.set_active((self.active - 1) % len(self.handles))

    def select_option(self, li):
        """Choose option `li` (0 or 1) for the active defect. A disabled
        (unavailable) card can never be chosen."""
        h = self.handles[self.active]
        if 0 <= li < len(h["loops"]) and h["sel"] != li:
            h["sel"] = li
            self.redraw()
            self.export()
            self._changed()

    def toggle_active_loop(self):
        """Switch to the other option of the active defect (Space key)."""
        h = self.handles[self.active]
        if len(h["loops"]) > 1:
            self.select_option((h["sel"] + 1) % len(h["loops"]))

    def focus_points(self):
        return [list(map(float, self.pts[h["loops"][h["sel"]]].mean(0)))
                for h in self.handles]

    # ------------------------------------------------------------------
    # Camera (A4)
    # ------------------------------------------------------------------
    def zoom_to_defect(self):
        """Frame the active defect: focal point = pair centroid, a few loop
        diameters away (see defect_camera_pose)."""
        h = self.handles[self.active]
        arrays = ([self.pts[lp] for lp in h["loops"]]
                  + [self.pts[lp] for (lp, _r) in h["blocked"]])
        try:
            pos, focal, up, _dist = defect_camera_pose(arrays, self.center, self.diag)
            self.pl.camera_position = [tuple(pos), tuple(focal), tuple(up)]
            self.pl.reset_camera_clipping_range()
            self.pl.render()
        except Exception as e:
            print(f"[tunnel] zoom to defect skipped: {e}", file=sys.stderr)

    def whole_head(self):
        """Back to the whole head, seen from the side the active defect is on."""
        try:
            self.pl.reset_camera()
            focus = self.pts[self.handles[self.active]["loops"][
                self.handles[self.active]["sel"]]].mean(0)
            _orient_camera_to(self.pl, self.center, focus)
            self.pl.render()
        except Exception as e:
            print(f"[tunnel] whole-head view skipped: {e}", file=sys.stderr)

    def set_see_through(self, on):
        """Make the head surface see-through (opacity ~0.4) so loops inside ear
        folds show; False restores the normal opacity."""
        self.see_through = bool(on)
        opacity = SEE_THROUGH_OPACITY if self.see_through else MESH_OPACITY
        try:
            for name in ("surface", "components"):
                actor = self.pl.actors.get(name)
                if actor is not None:
                    actor.GetProperty().SetOpacity(opacity)
            try:   # correct blending of the translucent surface and the ring tubes
                if self.see_through:
                    self.pl.enable_depth_peeling()
                else:
                    self.pl.disable_depth_peeling()
            except Exception:
                pass
            self.pl.render()
        except Exception as e:
            print(f"[tunnel] see-through toggle skipped: {e}", file=sys.stderr)
        self._changed()

    def toggle_see_through(self):
        self.set_see_through(not self.see_through)

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

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def _clear_labels(self):
        for name in self._label_names:
            try:
                self.pl.remove_actor(name, render=False)
            except Exception:
                pass
        self._label_names = []

    def _label(self, targets, anchors, labels, color, name):
        self._label_names += _add_labels_with_leaders(
            self.pl, targets, anchors, labels, color, name)

    def redraw(self):
        self._clear_labels()
        for hi, h in enumerate(self.handles):
            sel = h["sel"]
            is_active = (hi == self.active)

            for li, lp in enumerate(h["loops"]):
                is_sel = (li == sel)
                # Active defect: wider rings so it stands out from the others.
                if is_active:
                    width = 20 if is_sel else 10
                else:
                    width = 10 if is_sel else 5
                # Selected ring red, the other blue; dimmer tints when inactive.
                color = COLOR_CUT_ACTIVE if is_sel else COLOR_IGNORE_ACTIVE
                if not is_active:
                    color = COLOR_CUT_INACTIVE if is_sel else COLOR_IGNORE_INACTIVE
                _draw_loop(self.pl, self.pts[lp], color,
                           f'sel_loop_{hi}_{li}', width=width)

            if not is_active:
                # Other defects: one short tag on the selected ring.
                cc = self.pts[h["loops"][sel]].mean(0)
                self._label([cc], [_label_anchor(cc, self.center, self.diag, +1)],
                            [f"Defect {hi + 1}"], COLOR_CUT_INACTIVE,
                            f'lbl_{hi}_sel')
            else:
                # Active defect: each ring carries its option name; the chosen
                # one is marked.
                for li, lp in enumerate(h["loops"]):
                    title, _ = option_text(h["labels"][li], li)
                    is_sel = (li == sel)
                    cc = self.pts[lp].mean(0)
                    self._label([cc],
                                [_label_anchor(cc, self.center, self.diag,
                                               +1 if is_sel else -1)],
                                [title + ("\n[selected]" if is_sel else "")],
                                COLOR_CUT_ACTIVE if is_sel else COLOR_IGNORE_ACTIVE,
                                f'lbl_{hi}_{li}')

            # Loop B that failed the sever check: grey, label only, never selectable.
            for bi, (blp, _reason) in enumerate(h["blocked"]):
                _draw_loop(self.pl, self.pts[blp], COLOR_BLOCKED,
                           f'blocked_loop_{hi}_{bi}', width=8)
                if is_active:
                    bc = self.pts[blp].mean(0)
                    self._label([bc],
                                [_label_anchor(bc, self.center, self.diag, -1)],
                                [UNAVAILABLE_TEXT], COLOR_BLOCKED,
                                f'blocked_lbl_{hi}_{bi}')

        # The single on-canvas hint line (everything else lives in the panel).
        try:
            self.pl.add_text(CANVAS_HINT, position="lower_right", font_size=9,
                             color=COLOR_CANVAS_HINT, name="kb_hint")
        except Exception:
            pass

        try:
            self.pl.render()
        except Exception:
            pass

    def export(self):
        """Write the currently-selected loops for a manual Blender cut. Returns
        the path of the .txt written (also kept in self.export_txt), or None."""
        if not self.mesh_path:
            return None
        try:
            from tunnel_loop_locator import export_loops_for_blender
            cut_loops = [h["loops"][h["sel"]] for h in self.handles]
            prefix = os.path.splitext(self.mesh_path)[0] + "_cut"
            _, self.export_txt = export_loops_for_blender(self.pts, cut_loops, prefix)
            return self.export_txt
        except Exception as e:
            print(f"[tunnel] loop export failed: {e}", file=sys.stderr)
            return None

    def _status(self, text, color):
        if callable(self.status_cb):
            try:
                self.status_cb(text, color)
            except Exception as e:
                print(f"[tunnel] status_cb failed: {e}", file=sys.stderr)
            return
        try:
            self.pl.add_text(text, position="upper_right", font_size=10,
                             color=color, name="patchstatus")
            self.pl.render()
        except Exception:
            pass

    def _set_busy(self, busy):
        self.busy = busy
        if callable(self.busy_cb):
            try:
                self.busy_cb(busy)
            except Exception:
                pass

    def request_apply(self):
        """C key / Apply button. Opens the confirm step (Cancel changes nothing);
        ignored while a cut is already running so a queued second press cannot
        re-cut stale arrays."""
        if self.busy:
            return
        if self.faces is None or not self.mesh_path:
            self._status("The fix is unavailable (no mesh data).", COLOR_CUT_INACTIVE)
            return
        if callable(self.confirm_cb):
            self.confirm_cb(len(self.handles), self.apply_cut_and_patch)
        else:
            self.apply_cut_and_patch()

    def apply_cut_and_patch(self):
        """Cut and patch the chosen loops. Call request_apply() for the
        user-facing path (it adds the confirm step); this is the confirmed action.

        One chosen loop -> cut_and_patch (per-loop sever check + post-checks);
        several -> cut_and_patch_loops (union pre-check + post-checks). Backup
        (only if none exists), cut + checks, temp-file write and os.replace are
        done by project_store.apply_with_backup: on ANY failure the original
        aligned_head.ply is untouched and the temp file is removed. The job runs
        through job_runner (a worker thread in the window) so the panel stays
        alive; _finish_apply then runs on the GUI thread. On success a sentinel
        cutpatch_report.json is written so the GUI re-inspects, then on_success()
        closes the viewer. The exported loops (manual Blender cut) remain the
        fallback; a failure names the check that failed."""
        if self.busy:
            return
        if self.faces is None or not self.mesh_path:
            self._status("The fix is unavailable (no mesh data).", COLOR_CUT_INACTIVE)
            return
        self._set_busy(True)
        cut_loops = [h["loops"][h["sel"]] for h in self.handles]

        def job():
            # Worker-safe: numpy/scipy/pymeshfix + a file write, no rendering.
            from tunnel_loop_extractor import cut_and_patch, cut_and_patch_loops
            from project_store import apply_with_backup

            def compute():
                if len(cut_loops) == 1:
                    return cut_and_patch(self.pts, self.faces, cut_loops[0])
                return cut_and_patch_loops(self.pts, self.faces, cut_loops)

            apply_with_backup(self.mesh_path, compute, _write_ply_tmp)

        self._status("Fixing the mesh... " + CUT_HINT, COLOR_PALE_YELLOW)
        if callable(self.job_runner):
            self.job_runner(job, self._finish_apply)
            return
        error = None
        try:
            job()
        except Exception as e:
            error = e
        self._finish_apply(error)

    def _finish_apply(self, error):
        """GUI-thread tail of apply_cut_and_patch (error is None on success)."""
        if error is not None:
            self._set_busy(False)
            self._status(
                f"The fix was NOT applied; your mesh is unchanged.\n{error}\n"
                "Use 'Export Loops for Blender' to make this cut manually instead.",
                "#e6a09f")
            print(f"[tunnel] fix failed: {error}", file=sys.stderr)
            return

        # The mesh is now replaced; the rest is best-effort notification.
        from project_store import backup_path_for
        try:
            # Sentinel so the GUI knows the fix ran (not just that the viewer
            # was closed); picked up by _after_tunnel_viewer(), which runs
            # repair_aligned automatically.
            import json as _json
            sentinel = os.path.join(os.path.dirname(self.mesh_path),
                                    "cutpatch_report.json")
            with open(sentinel, "w") as _f:
                _json.dump({"applied": True,
                            "mesh": os.path.basename(self.mesh_path)}, _f)
        except Exception as e:
            print(f"[tunnel] could not write cutpatch_report.json: {e}", file=sys.stderr)

        self._status("Fixed. Closing and re-checking the mesh...", COLOR_PALE_GREEN)
        print("[tunnel] Cut+Patch applied; mesh saved watertight; backup: "
              f"{os.path.basename(backup_path_for(self.mesh_path))}", flush=True)
        # Leave busy=True: the viewer is closing, nothing may re-cut.
        if callable(self.on_success):
            try:
                self.on_success()
            except Exception as e:
                print(f"[tunnel] on_success callback failed: {e}", file=sys.stderr)


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


def render_tunnel_data(plotter, data, mesh_path=None, on_success=None,
                       confirm_cb=None, busy_cb=None, genus=None, on_change=None,
                       status_cb=None, job_runner=None):
    """Main-thread: draw the computed tunnel data onto the plotter. Returns the
    same dict shape add_tunnel_overlay used (rendered, tight, export_txt,
    n_loops, focus, selector) or False if nothing to draw.

    on_success: optional callable forwarded to TunnelSelector — called when
    Cut+Patch succeeds so the hosting window can close itself and trigger GUI
    re-validation. genus / on_change / status_cb / job_runner are forwarded to
    TunnelSelector (panel hooks and the off-thread cut runner).
    """
    mode = data.get("mode")

    if mode == "pairs":
        pts, faces, pairs = data["pts"], data["faces"], data["pairs"]
        selector = TunnelSelector(plotter, pts, pairs, mesh_path=mesh_path,
                                  faces=faces, on_success=on_success,
                                  confirm_cb=confirm_cb, busy_cb=busy_cb,
                                  genus=genus, on_change=on_change,
                                  status_cb=status_cb, job_runner=job_runner)
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
        _add_labels(plotter, pins, [f"Defect {i + 1}" for i in range(len(pins))],
                    'magenta', 'tunnel_labels')
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
    `mesh` is a pyvista mesh or a bare centre point.
    """
    try:
        center = np.array(getattr(mesh, "center", mesh), dtype=float)
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
    pl.add_mesh(mesh_disp, color="#C8B69F", opacity=MESH_OPACITY, show_edges=False,
                ambient=MESH_AMBIENT, diffuse=MESH_DIFFUSE, specular=MESH_SPECULAR, name="surface")

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
                        opacity=MESH_OPACITY, show_scalar_bar=False, name="components")
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
                pl.add_mesh(si_mesh, color="yellow", opacity=MESH_OPACITY, name="si_faces")
                legend_lines.append(f"Yellow faces: {n_si} self-intersection(s)")
        else:
            legend_lines.append("[OK] No self-intersecting faces")
    except Exception as e:
        legend_lines.append(f"Self-intersection check: {e}")

    return legend_lines


def view_mesh_problems(mesh_path):
    """
    Open a PySide6 window showing all detected mesh problems:
      - Red lines    : boundary / non-manifold edges
      - Colour bands : disconnected components
      - Yellow faces : self-intersecting faces (requires pymeshlab)
      - Red/blue rings : the two ways to fix each see-through hole (genus > 0):
        "Fill the hole" / "Remove the bridge"

    The window appears immediately with the base mesh + the fast overlays, and a
    progress bar runs while the (slow) cut-loop computation happens in a worker
    thread. Press R (or H) for the whole head; close the window to exit.
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
    """Define the Qt window + workers lazily so importing this module does not
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

    class _CutWorker(QtCore.QThread):
        """Runs the cut + checks + atomic save off the GUI thread. The job is
        VTK-rendering-free (see TunnelSelector.apply_cut_and_patch). The result
        is read from `.error` after QThread.finished, which fires only once run()
        has returned, so the thread is never destroyed while still running."""

        def __init__(self, job):
            super().__init__()
            self._job = job
            self.error = None

        def run(self):
            try:
                self._job()
            except BaseException as e:   # noqa: BLE001 - reported on the GUI thread
                self.error = e

    class _OptionCard(QtWidgets.QFrame):
        """A clickable, radio-style option card. Never takes keyboard focus."""
        clicked = QtCore.Signal()

        def __init__(self):
            super().__init__()
            self.setObjectName("OptionCard")
            self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(12, 8, 12, 8)
            lay.setSpacing(2)
            self._title = QtWidgets.QLabel("")
            self._sub = QtWidgets.QLabel("")
            self._sub.setWordWrap(True)
            for w in (self._title, self._sub):
                w.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                lay.addWidget(w)
            self._interactive = False

        def set_state(self, title, subtitle, selected, enabled, tooltip, accent):
            self._interactive = enabled
            self._title.setText(("●  " if selected else "○  ") + title)
            self._sub.setText(subtitle)
            self._sub.setVisible(bool(subtitle))
            self.setToolTip(tooltip or "")
            self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor if enabled
                           else QtCore.Qt.CursorShape.ArrowCursor)
            if not enabled:
                bg, border, tcol, scol = "#262626", "#3a3a3a", "#777", "#666"
            elif selected:
                bg, border, tcol, scol = "#3a3030" if accent == "red" else "#2c3140", \
                    (COLOR_CUT_ACTIVE if accent == "red" else COLOR_IGNORE_ACTIVE), \
                    "#ffffff", "#ddd"
            else:
                bg, border, tcol, scol = "#2f2f2f", "#555", "#ddd", "#aaa"
            self.setStyleSheet(
                f"QFrame#OptionCard {{ background: {bg}; border: 2px solid {border};"
                " border-radius: 8px; }")
            self._title.setStyleSheet(
                f"font-size: 13px; font-weight: bold; color: {tcol}; background: transparent;")
            self._sub.setStyleSheet(
                f"font-size: 11px; color: {scol}; background: transparent;")

        def mouseReleaseEvent(self, ev):
            if (self._interactive and ev.button() == QtCore.Qt.MouseButton.LeftButton
                    and self.rect().contains(ev.position().toPoint())):
                self.clicked.emit()
            super().mouseReleaseEvent(ev)

    class _ProblemViewerWindow(QtWidgets.QMainWindow):
        def __init__(self, mesh_path):
            super().__init__()
            self.mesh_path = mesh_path
            self.setWindowTitle(f"Fix See-through Holes - {os.path.basename(mesh_path)}")
            self.resize(1300, 800)

            self.setStyleSheet(f"""
                QMainWindow {{ background-color: {BG_COLOR}; }}
                QWidget {{ background-color: {BG_COLOR}; color: {TEXT_COLOR};
                           font-family: 'Segoe UI', sans-serif; }}
                QFrame#ControlPanel {{ background-color: {PANEL_COLOR}; border-radius: 10px; }}
                QPushButton {{ background-color: {ACCENT_COLOR}; border-radius: 5px;
                               padding: 8px; font-weight: bold; color: {TEXT_COLOR}; }}
                QPushButton:checked {{ background-color: #2a6a9e; border: 2px solid #8cc4f0; }}
                QPushButton:disabled {{ background-color: #444; color: #888; }}
                QProgressBar {{ border: none; background: #444; border-radius: 4px; }}
                QProgressBar::chunk {{ background: {ACCENT_COLOR}; border-radius: 4px; }}
                QToolTip {{ background-color: #111; color: #eee; border: 1px solid #666; }}
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

            # Keyboard state: selector is None until _on_done fires.
            self._selector = None
            self._genus = 0
            self._confirm_open = False
            self._cut_running = False
            self._cut_worker = None
            self._tight_export = None
            # Space/Tab/Shift+Tab/C/Esc and Z/H/X are intercepted by an
            # APPLICATION-level filter (scoped to this window being active)
            # rather than a filter on the 3D widget: keys then work wherever
            # focus is and are consumed before any focused widget or Qt
            # focus-traversal sees them.
            QtWidgets.QApplication.instance().installEventFilter(self)

            # Central progress overlay (child of plotter_container, positioned by resizeEvent)
            self._overlay = QtWidgets.QFrame(self.plotter_container)
            self._overlay.setStyleSheet(
                "background: rgba(30,30,30,225); border-radius: 10px;")
            ov_lay = QtWidgets.QVBoxLayout(self._overlay)
            self._overlay_label = QtWidgets.QLabel("Finding the defect...")
            self._overlay_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: white; background: transparent;")
            self._overlay_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._overlay_label.setWordWrap(True)
            self._overlay_hint = QtWidgets.QLabel(LOOP_FIND_HINT)
            self._overlay_hint.setStyleSheet(
                "font-size: 11px; color: #bbb; background: transparent;")
            self._overlay_hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._overlay_hint.setWordWrap(True)
            ov_bar = QtWidgets.QProgressBar()
            ov_bar.setRange(0, 0)
            ov_bar.setFixedHeight(8)
            ov_lay.addWidget(self._overlay_label)
            ov_lay.addWidget(self._overlay_hint)
            ov_lay.addWidget(ov_bar)
            self._overlay.hide()

            # Confirm step for the fix (child of plotter_container, like the overlay)
            self._confirm = QtWidgets.QFrame(self.plotter_container)
            self._confirm.setStyleSheet(
                "background: rgba(30,30,30,235); border-radius: 10px;")
            cf_lay = QtWidgets.QVBoxLayout(self._confirm)
            cf_lay.setContentsMargins(16, 14, 16, 14)
            self._confirm_label = QtWidgets.QLabel("")
            self._confirm_label.setWordWrap(True)
            self._confirm_label.setStyleSheet(
                "font-size: 13px; color: white; background: transparent;")
            cf_lay.addWidget(self._confirm_label)
            cf_row = QtWidgets.QHBoxLayout()
            self._btn_confirm_yes = QtWidgets.QPushButton("Apply fix")
            self._btn_confirm_no = QtWidgets.QPushButton("Cancel")
            for _b in (self._btn_confirm_yes, self._btn_confirm_no):
                _b.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
                cf_row.addWidget(_b)
            cf_lay.addLayout(cf_row)
            self._btn_confirm_yes.clicked.connect(self._confirm_yes)
            self._btn_confirm_no.clicked.connect(self._confirm_no)
            self._confirm_cb_pending = None
            self._confirm_h = 150
            self._confirm.hide()

            # Right: control panel
            panel = QtWidgets.QFrame()
            panel.setObjectName("ControlPanel")
            panel.setFixedWidth(340)
            panel_layout = QtWidgets.QVBoxLayout(panel)
            panel_layout.setContentsMargins(12, 14, 12, 14)
            panel_layout.setSpacing(8)
            main_layout.addWidget(panel, stretch=1)

            lbl_title = QtWidgets.QLabel(os.path.basename(mesh_path))
            lbl_title.setStyleSheet("font-size: 12px; color: #999;")
            lbl_title.setWordWrap(True)
            panel_layout.addWidget(lbl_title)

            # --- the decision (hidden until defects are found) ---
            self._decision = QtWidgets.QWidget()
            self._decision.setStyleSheet("background: transparent;")
            dl = QtWidgets.QVBoxLayout(self._decision)
            dl.setContentsMargins(0, 0, 0, 0)
            dl.setSpacing(8)

            self._lbl_question = QtWidgets.QLabel(QUESTION_TEXT)
            self._lbl_question.setWordWrap(True)
            self._lbl_question.setStyleSheet(
                "font-size: 14px; font-weight: bold; background: transparent;")
            dl.addWidget(self._lbl_question)

            self._lbl_rule = QtWidgets.QLabel(RULE_OF_THUMB)
            self._lbl_rule.setWordWrap(True)
            self._lbl_rule.setStyleSheet(
                "color: #aaa; font-size: 11px; background: transparent;")
            dl.addWidget(self._lbl_rule)

            nav = QtWidgets.QHBoxLayout()
            self._btn_prev = QtWidgets.QPushButton("< Prev")
            self._lbl_defect = QtWidgets.QLabel("Defect 1 of 1")
            self._lbl_defect.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._lbl_defect.setStyleSheet(
                "font-size: 13px; font-weight: bold; background: transparent;")
            self._btn_next = QtWidgets.QPushButton("Next >")
            self._btn_prev.setToolTip("Previous defect (Shift+Tab)")
            self._btn_next.setToolTip("Next defect (Tab)")
            self._btn_prev.clicked.connect(lambda: self._selector.prev_handle())
            self._btn_next.clicked.connect(lambda: self._selector.next_handle())
            nav.addWidget(self._btn_prev)
            nav.addWidget(self._lbl_defect, stretch=1)
            nav.addWidget(self._btn_next)
            dl.addLayout(nav)

            self._cards = []
            for _i in range(2):
                card = _OptionCard()
                card.clicked.connect(lambda i=_i: self._card_clicked(i))
                dl.addWidget(card)
                self._cards.append(card)

            self._lbl_note = QtWidgets.QLabel("")
            self._lbl_note.setWordWrap(True)
            self._lbl_note.setStyleSheet(
                "color: #e6c36f; font-size: 11px; background: transparent;")
            dl.addWidget(self._lbl_note)
            panel_layout.addWidget(self._decision)
            self._decision.hide()

            self._panel_status = QtWidgets.QLabel("Loading mesh...")
            self._panel_status.setWordWrap(True)
            self._panel_status.setStyleSheet(
                "color: #ccc; font-size: 11px; background: transparent;")
            panel_layout.addWidget(self._panel_status)

            panel_layout.addStretch()

            self._btn_cut = QtWidgets.QPushButton("Apply fix  [C]")
            self._btn_cut.setEnabled(False)
            self._btn_cut.clicked.connect(self._request_cut)
            panel_layout.addWidget(self._btn_cut)

            cam_row = QtWidgets.QHBoxLayout()
            self._btn_zoom = QtWidgets.QPushButton("Zoom to defect [Z]")
            self._btn_zoom.setEnabled(False)
            self._btn_zoom.clicked.connect(self._zoom_to_defect)
            self._btn_whole = QtWidgets.QPushButton("Whole head [H]")
            self._btn_whole.setEnabled(False)
            self._btn_whole.clicked.connect(self._whole_head)
            cam_row.addWidget(self._btn_zoom)
            cam_row.addWidget(self._btn_whole)
            panel_layout.addLayout(cam_row)

            self._btn_see = QtWidgets.QPushButton("See-through [X]")
            self._btn_see.setCheckable(True)
            self._btn_see.setEnabled(False)
            self._btn_see.setToolTip("Make the head semi-transparent so rings "
                                     "hidden in ear folds show through.")
            self._btn_see.clicked.connect(self._toggle_see_through)
            panel_layout.addWidget(self._btn_see)

            self._btn_export = QtWidgets.QPushButton("Export Loops for Blender")
            self._btn_export.setEnabled(False)
            self._btn_export.clicked.connect(self._do_export)
            panel_layout.addWidget(self._btn_export)

            self._btn_close = QtWidgets.QPushButton("Close without fixing")
            self._btn_close.setEnabled(False)
            self._btn_close.clicked.connect(self.close)
            panel_layout.addWidget(self._btn_close)

            # Status bar: slim label only (overlay is the prominent indicator)
            self.status_label = QtWidgets.QLabel("Loading mesh...")
            self.statusBar().addWidget(self.status_label, 1)

            # Panel widgets must never take keyboard focus: a focused button
            # turns Space (option toggle) into a click on that button, and Apply
            # has no other guard. Clicks still work; keys go to the filter.
            for _b in (self._btn_cut, self._btn_zoom, self._btn_whole, self._btn_see,
                       self._btn_export, self._btn_close, self._btn_prev,
                       self._btn_next, *self._cards):
                _b.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

            self.plotter.set_background(BG_COLOR)

            try:
                self.mesh = pv.read(mesh_path)
            except Exception as e:
                self.status_label.setText(f"Error loading mesh: {e}")
                self._panel_status.setText(f"Error: {e}")
                return

            self.legend_lines = _add_static_overlays(self.plotter, self.mesh, mesh_path)
            self._refresh_legend()
            self.plotter.add_key_event('r', lambda: self.plotter.reset_camera())
            self.plotter.reset_camera()
            self._btn_whole.setEnabled(True)

            # genus check, then launch the worker for the slow loop compute
            genus = 0
            try:
                import mesh_inspector
                genus = mesh_inspector.inspect_mesh(mesh_path)["counts"].get("genus", 0)
            except Exception as e:
                self.legend_lines.append(f"Hole check: {e}")
                self._refresh_legend()
            self._genus = genus

            if genus <= 0:
                self.status_label.setText("No see-through holes.")
                self._panel_status.setText("No see-through holes detected.")
                self._btn_close.setEnabled(True)
                return

            first = "Finding the defects..." if genus > 1 else "Finding the defect..."
            self.status_label.setText(first)
            self._panel_status.setText(first)
            self._overlay_label.setText(first)
            pts, faces = _mesh_to_arrays(self.mesh)
            self._worker = _TunnelWorker(pts, faces, genus)
            self._worker.progress.connect(self._on_progress)
            self._worker.done.connect(self._on_done)
            self._worker.start()
            self._overlay.show()
            self._overlay.raise_()

        def eventFilter(self, obj, event):
            """Application-level key filter, active only while this window is the
            active window: Space = other option, Tab/Shift+Tab = next/previous
            defect, C = apply (opens the confirm step), Esc = cancel the confirm
            step, Z / H / X = zoom to defect / whole head / see-through (no
            modifiers; none clash with VTK or pyvista defaults: those use
            q e f p r w s 3 u b v C and the arrow / +/- keys). Auto-repeat is
            swallowed so holding a key cannot fire twice, and nothing but Esc does
            anything while a cut runs or the confirm shows."""
            if (event.type() == QtCore.QEvent.Type.KeyPress
                    and self._selector is not None
                    and QtWidgets.QApplication.activeWindow() is self):
                key = event.key()
                Qt = QtCore.Qt
                camera_keys = (Qt.Key.Key_Z, Qt.Key.Key_H, Qt.Key.Key_X)
                handled = (Qt.Key.Key_Space, Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
                           Qt.Key.Key_C, Qt.Key.Key_Escape) + camera_keys
                if key in camera_keys and event.modifiers() != Qt.KeyboardModifier.NoModifier:
                    return super().eventFilter(obj, event)
                if key in handled:
                    if event.isAutoRepeat():
                        return True
                    sel = self._selector
                    if self._confirm_open:
                        if key == Qt.Key.Key_Escape:
                            self._confirm_no()
                        return True
                    if sel.busy:
                        return True
                    if key == Qt.Key.Key_Space:
                        sel.toggle_active_loop()
                    elif key == Qt.Key.Key_Tab:
                        sel.next_handle()
                    elif key == Qt.Key.Key_Backtab:
                        sel.prev_handle()
                    elif key == Qt.Key.Key_C:
                        self._request_cut()
                    elif key == Qt.Key.Key_Z:
                        self._zoom_to_defect()
                    elif key == Qt.Key.Key_H:
                        self._whole_head()
                    elif key == Qt.Key.Key_X:
                        sel.toggle_see_through()
                    else:
                        return False   # Esc with nothing to cancel
                    return True
            return super().eventFilter(obj, event)

        # --- panel <-> selector -------------------------------------------
        def _card_clicked(self, i):
            sel = self._selector
            if sel is None or sel.busy or self._confirm_open:
                return
            sel.select_option(i)

        def _zoom_to_defect(self, *_):
            if self._selector is not None and not self._selector.busy:
                self._selector.zoom_to_defect()

        def _whole_head(self, *_):
            if self._selector is not None:
                if not self._selector.busy:
                    self._selector.whole_head()
            else:
                self.plotter.reset_camera()

        def _toggle_see_through(self, checked=False):
            if self._selector is not None and not self._selector.busy:
                self._selector.set_see_through(bool(checked))

        def _refresh_panel(self):
            """Re-sync every panel widget from the selector (called on any change
            of selection / active defect / busy / confirm state)."""
            sel = self._selector
            if sel is None:
                return
            locked = sel.busy or self._confirm_open
            n = len(sel.handles)
            self._lbl_defect.setText(sel.defect_title())
            for i, spec in enumerate(sel.cards()):
                self._cards[i].set_state(
                    spec["title"], spec["subtitle"], spec["selected"],
                    spec["enabled"] and not locked, spec["tooltip"],
                    "red" if spec["selected"] else "blue")
            self._btn_prev.setEnabled(n > 1 and not locked)
            self._btn_next.setEnabled(n > 1 and not locked)
            self._lbl_rule.setVisible(sel.is_classified())
            notes = sel.notes()
            self._lbl_note.setText("\n\n".join(notes))
            self._lbl_note.setVisible(bool(notes))
            self._btn_cut.setEnabled(not locked)
            self._btn_zoom.setEnabled(not sel.busy)
            self._btn_whole.setEnabled(not sel.busy)
            self._btn_see.setEnabled(not sel.busy)
            self._btn_see.blockSignals(True)
            self._btn_see.setChecked(sel.see_through)
            self._btn_see.blockSignals(False)
            self._btn_export.setEnabled(not sel.busy)
            self._btn_close.setEnabled(not sel.busy)

        def _selector_status(self, text, color):
            self._panel_status.setText(text)
            self._panel_status.setStyleSheet(
                f"color: {color}; font-size: 11px; background: transparent;")

        # --- confirm step -------------------------------------------------
        def _request_cut(self, *_):
            if self._selector is None or self._selector.busy or self._confirm_open:
                return
            self._selector.request_apply()

        def _show_confirm(self, n_defects, on_confirm):
            from project_store import backup_path_for
            backup_name = os.path.basename(backup_path_for(self.mesh_path))
            if os.path.exists(backup_path_for(self.mesh_path)):
                tail = (f"A backup from before your first fix "
                        f"({backup_name}) already exists and is kept.")
            else:
                tail = (f"The current file is saved as {backup_name} first.")
            choices = self._selector.choice_summary() if self._selector else []
            self._confirm_label.setText(
                f"Apply the fix for {n_defects} defect(s)?\n"
                + "\n".join(choices) + "\n"
                f"{os.path.basename(self.mesh_path)} will be replaced. {tail}")
            self._confirm_h = 150 + 18 * len(choices)
            self._confirm_cb_pending = on_confirm
            self._confirm_open = True
            self._refresh_panel()
            self._place_overlays()
            self._confirm.show()
            self._confirm.raise_()

        def _hide_confirm(self):
            self._confirm_open = False
            self._confirm_cb_pending = None
            self._confirm.hide()
            self._refresh_panel()

        def _confirm_yes(self):
            cb = self._confirm_cb_pending
            self._hide_confirm()
            if callable(cb):
                cb()

        def _confirm_no(self):
            self._hide_confirm()
            self._selector_status("Cancelled - nothing was changed.", "#ccc")

        # --- the cut, off the GUI thread ----------------------------------
        def _set_cut_busy(self, busy):
            """Selector busy hook: lock the panel and show the wait overlay while
            a cut runs; restore on failure (success leaves it locked: closing)."""
            self._btn_confirm_yes.setEnabled(not busy)
            if busy:
                self._overlay_label.setText("Fixing the mesh...")
                self._overlay_hint.setText(CUT_HINT)
                self._place_overlays()
                self._overlay.show()
                self._overlay.raise_()
            else:
                self._overlay.hide()
            self._refresh_panel()

        def _run_cut_job(self, job, finish):
            self._cut_finish = finish
            self._cut_running = True
            self._cut_worker = _CutWorker(job)
            self._cut_worker.finished.connect(self._on_cut_finished)
            self._cut_worker.start()

        def _on_cut_finished(self):
            worker = self._cut_worker
            self._cut_running = False
            finish, self._cut_finish = self._cut_finish, None
            if worker is not None and worker.error is None:
                self._overlay_label.setText("Fixed. Closing and re-checking the mesh...")
                self._overlay_hint.setText("")
            if worker is not None and callable(finish):
                finish(worker.error)

        def _do_export(self):
            path = (self._selector.export() if self._selector is not None
                    else self._tight_export)
            if path:
                self._selector_status(f"Loops exported to:\n{path}", "#ccc")
                self.status_label.setText(f"Exported loops: {path}")
            else:
                self._selector_status("Loop export failed - see the console.", "#e6a09f")

        def closeEvent(self, ev):
            if self._cut_running:
                # Closing mid-write could cost the user the mesh; the window
                # closes by itself as soon as the fix finishes.
                ev.ignore()
                self._selector_status(
                    "The mesh is being saved - please wait. This window closes "
                    "by itself when it is done.", COLOR_PALE_YELLOW)
                return
            try:
                QtWidgets.QApplication.instance().removeEventFilter(self)
            except Exception:
                pass
            super().closeEvent(ev)

        def _place_overlays(self):
            r = self.plotter_container.rect()
            w, h = 440, 110
            self._overlay.setGeometry(
                (r.width() - w) // 2, (r.height() - h) // 2, w, h)
            cw, ch = min(520, max(300, r.width() - 40)), self._confirm_h
            self._confirm.setGeometry(
                (r.width() - cw) // 2, (r.height() - ch) // 2, cw, ch)
            self._overlay.raise_()
            if self._confirm_open:
                self._confirm.raise_()

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            if hasattr(self, '_confirm'):
                self._place_overlays()

        def _refresh_legend(self):
            self.plotter.add_text(_build_legend(self.legend_lines),
                                  position="upper_left", font_size=10,
                                  color="white", name="legend")

        def _on_progress(self, msg):
            text = friendly_progress(msg)
            self.status_label.setText(text)
            self._overlay_label.setText(text)
            self._panel_status.setText(text)

        def _on_done(self, data):
            self._overlay.hide()
            self._btn_close.setEnabled(True)
            self._panel_status.setStyleSheet(
                "color: #ccc; font-size: 11px; background: transparent;")

            found = render_tunnel_data(self.plotter, data, mesh_path=self.mesh_path,
                                       on_success=self.close,
                                       confirm_cb=self._show_confirm,
                                       busy_cb=self._set_cut_busy,
                                       genus=self._genus,
                                       on_change=self._refresh_panel,
                                       status_cb=self._selector_status,
                                       job_runner=self._run_cut_job)
            self._selector = found.get("selector") if isinstance(found, dict) else None
            if found and self._selector is not None:
                sel = self._selector
                for note in sel.unavailable_messages():
                    print(f"[tunnel] {note}", file=sys.stderr)
                self._decision.show()
                self._btn_export.setEnabled(True)
                self._refresh_panel()
                n = found["n_loops"]
                self._panel_status.setText(
                    f"Found {n} defect{'s' if n != 1 else ''}. Look at the "
                    "highlighted rings, then choose an option.")
                self.status_label.setText(
                    f"Found {n} defect{'s' if n != 1 else ''}.")
                sel.zoom_to_defect()
                self.plotter.setFocus()
            elif found:
                # Tight (magenta) fallback: the safe in-app check did not pass.
                self._tight_export = found.get("export_txt")
                msg = TIGHT_MODE_NOTE
                if self._tight_export:
                    msg += f"\n\nAlready written: {self._tight_export}"
                self._panel_status.setText(msg)
                self._btn_export.setEnabled(bool(self._tight_export))
                self.status_label.setText("Cutting is disabled for these loops.")
                focus_pts = found.get("focus")
                if focus_pts:
                    _orient_camera_to(self.plotter, self.mesh,
                                      np.array(focus_pts[0], dtype=float))
            else:
                self._panel_status.setText(NO_LOOPS_NOTE)
                self.status_label.setText("No safe fix found.")
            self._refresh_legend()

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
