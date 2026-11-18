import sys
import os
import re
import argparse
import threading
import time
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


def _label_anchor(centroid, mesh_center, diag, side, up=(0.0, 0.0, 1.0), off=None):
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
    off = 0.11 * diag if off is None else off
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
            plotter.add_mesh(pv.Line(t, a), color=COLOR_LEADER, line_width=1,
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
    # NOTE: vtkPLYWriter stores the points as float32, so coordinates are rounded
    # (~1e-7 relative). That is harmless: faces are index triples, so the topology
    # (genus, watertightness, components) the cut was verified for cannot change.
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
    if back.n_points != len(new_pts):
        raise IOError(f"{os.path.basename(path)} read back with {back.n_points} "
                      f"points, expected {len(new_pts)}")


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
               "X = see-through    P = before / after")
# Measured on this machine (zzmeshes genus_1/2/3: 5 s / 25 s / 37 s, plus a few
# seconds to load the mesh). Re-measure if the loop finder changes.
LOOP_FIND_HINT = "This usually takes under a minute (longer with several defects)."
CUT_HINT = "This usually takes about 15 seconds. Please don't close this window."

# Stage 3: previews, the after-view and Keep / Undo.
# B1 switch: if the background previews ever turn out too slow or make the window
# unresponsive, set this to False -- the viewer then works exactly like stage 2
# plus the A6 after-view (Apply computes the cut itself, nothing is previewed).
PREVIEWS_ENABLED = True
# Longest a single preview cut may take in the child process before it is killed and
# the cut is done in-process instead. Real cuts take 3-15 s (measured on 75k-170k face
# heads and the torture meshes), so 10 minutes is only a hung-child guard.
PREVIEW_CUT_TIMEOUT_S = 600

PREVIEW_PENDING_TEXT = "Preparing preview…"
PREVIEW_READY_TEXT = "Preview ready"
PREVIEW_UNAVAILABLE_PREFIX = "Not available — "
KEEP_LABEL = "Keep & Close"
UNDO_LABEL = "Undo"
KEEP_TOOLTIP = ("Saves this result: aligned_head.ply will be replaced by the fixed "
                "mesh. A backup of the current file is kept as "
                "aligned_head.pre_cutpatch.ply, and you can restore it from the "
                "main window.")
UNDO_TOOLTIP = ("Throw this result away and go back to choosing. Nothing was "
                "written to disk, so your mesh is unchanged.")
DISCARD_PROMPT = ("Discard this fix?\nThe fixed mesh has not been saved. Your "
                  "mesh file is unchanged and will stay as it is.")
AFTER_NOTE = ("This is the result of the fix. Look around (drag to rotate, P "
              "switches between before and after), then keep it or undo it.")
AFTER_LEFT_NOTE = ("The fix worked, but the mesh still has other defect(s). "
                   "Keeping saves this progress; the app then re-checks the mesh "
                   "and you can fix the rest.")


def fixed_banner(n_left):
    """Banner for the after-view: "Fixed — N defect(s) left"."""
    n = int(n_left)
    return f"Fixed — {n} {'defect' if n == 1 else 'defects'} left"


def plain_failure(msg):
    """Plain-words version of a cut_and_patch failure message (the technical
    text stays in the tooltip). Never contains the technical class names."""
    low = (msg or "").lower()
    if "does not sever" in low or "do not sever" in low:
        return "cutting here would not separate the surface cleanly"
    if "removed all faces" in low:
        return "cutting here would remove the whole surface"
    if "not watertight" in low:
        return "the patched surface would have open edges"
    if "components" in low:
        return "the cut would split off a separate piece"
    if "capped genus" in low:
        return "the patch would bring the hole back"
    if "face count dropped" in low:
        return "too much of the surface would be lost"
    text = re.sub(r"^\w+:\s*", "", (msg or "").strip())
    return text or "the safety check failed"


def close_decision(cut_running, has_unkept, discard_ok):
    """What the window does when asked to close (pure, so it is unit-tested):
      "wait"  -- a cut or save is running: refuse, the window closes by itself after;
      "ask"   -- an unkept fix exists: show the in-window "Discard this fix?" frame;
      "close" -- go ahead. Closing ALWAYS ends the process promptly: the preview
                 child is terminated, and view_mesh_problems() hard-exits (rc 0) if a
                 worker thread that cannot be interrupted (startup checks, loop
                 finder) is still running after the event loop ends."""
    if cut_running:
        return "wait"
    if has_unkept and not discard_ok:
        return "ask"
    return "close"


def preview_status_text(state, reason=None):
    """Card status line for a preview state ("pending"/"running"/"ready"/"failed")."""
    if state == "ready":
        return PREVIEW_READY_TEXT
    if state == "failed":
        return PREVIEW_UNAVAILABLE_PREFIX + plain_failure(reason)
    return PREVIEW_PENDING_TEXT


# Camera framing for "zoom to defect": distance = DEFECT_ZOOM_SPANS x the span
# (bounding-box diagonal) of the defect's loops, clamped to a fraction of the
# whole-mesh diagonal so a 3 mm ring is still seen in context and a head-wrapping
# loop never goes further out than the whole-head view.
DEFECT_ZOOM_SPANS = 3.0
DEFECT_ZOOM_MIN_FRAC = 0.10
DEFECT_ZOOM_MAX_FRAC = 0.80
SEE_THROUGH_OPACITY = 0.4
# Stage 3b: choosing a view direction from which the defect is actually visible.
# Candidates = outward + / - the best-fit-plane normal of each loop, plus cones of
# directions around each; each is scored by rays from the camera to sample points of
# the defect's loops (blocked fraction); the least blocked wins, ties go to outward.
VIEW_CONE_DEGREES = (35.0, 60.0)       # cone half-angles around each base direction
VIEW_CONE_AZIMUTHS = 6                 # directions per cone ring
VIEW_SAMPLES_PER_LOOP = 16             # ray targets per loop
VIEW_TOL_SPANS = 0.04                  # rays stop this many spans short of the ring point
VIEW_OUTWARD_BIAS = 0.1                # score penalty (x angle/pi) for leaving outward
HIDDEN_FRAC = 0.5                      # best view still blocked more than this => see-through
HIDDEN_NOTE = ("Turned on see-through: this defect is hidden behind other parts "
               "of the ear.")
GREEN_LEGEND = "Green = new surface added by the fix"
COLOR_MESH_BASE = "#C8B69F"
COLOR_NEW_SURFACE = "#86C9A0"          # soft green: faces that are not in the original
CHANGED_ARRAY = "m2s_changed"
COLOR_LEADER = "#8C8C8C"               # thin neutral label leader lines


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


def option_cards(labels, blocked_reasons, sel, previews=None):
    """Two card descriptions for one defect.

    labels: classifier labels of the AVAILABLE loops (1 or 2 entries);
    blocked_reasons: technical reasons for loops that failed the locate-time
    check (they fill the second card, disabled); sel: index of the selected card.
    A lone available loop has no classifier label (the classifier needs both
    loops), so it is shown as UNKNOWN: "Option 1".
    previews (stage 3): per available loop a (state, reason) pair from the
    preview cache, or None when previews are off. Each card then carries a
    "status" line; a failed preview greys the card out and it can't be chosen
    (its technical reason goes in the tooltip)."""
    cards = []
    for i in range(2):
        if i < len(labels):
            label = labels[i]
            title, sub = option_text(label, i)
            tip = OPTION_TOOLTIP.get(label, UNKNOWN_TOOLTIP)
            card = {"title": title, "subtitle": sub, "enabled": True,
                    "tooltip": tip, "selected": i == sel, "status": None}
            if previews is not None and i < len(previews) and previews[i] is not None:
                state, reason = previews[i]
                card["status"] = preview_status_text(state, reason)
                card["preview_state"] = state
                if state == "failed":
                    card["enabled"] = False
                    card["selected"] = False
                    card["tooltip"] = (tip + "\n\nPreview check failed: "
                                       + str(reason))
            cards.append(card)
        else:
            tip = (blocked_reasons[i - len(labels)]
                   if i - len(labels) < len(blocked_reasons) else
                   "No second loop could be generated for this defect.")
            cards.append({"title": f"Option {i + 1}", "subtitle": UNAVAILABLE_TEXT,
                          "enabled": False, "tooltip": tip, "selected": False,
                          "status": None})
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


def defect_camera_pose(loop_point_arrays, mesh_center, mesh_diag, direction=None):
    """Camera pose that frames one defect.

    focal = centroid of the loop pair (mean of the loops' centroids);
    distance = DEFECT_ZOOM_SPANS x bbox diagonal of all the defect's loop points,
    clamped to [MIN_FRAC, MAX_FRAC] x the whole-mesh diagonal;
    direction = `direction` if given (a unit vector from the focal point towards
    the camera, see choose_defect_view), else outward from the mesh centre
    through the focal point.
    Returns (position, focal, view_up, distance) as numpy arrays / float."""
    arrays = [np.asarray(a, dtype=float) for a in loop_point_arrays if len(a)]
    focal = np.mean([a.mean(axis=0) for a in arrays], axis=0)
    allp = np.vstack(arrays)
    span = float(np.linalg.norm(allp.max(axis=0) - allp.min(axis=0)))
    dist = float(np.clip(DEFECT_ZOOM_SPANS * span,
                         DEFECT_ZOOM_MIN_FRAC * mesh_diag,
                         DEFECT_ZOOM_MAX_FRAC * mesh_diag))
    if direction is None:
        direction = focal - np.asarray(mesh_center, dtype=float)
    direction = np.asarray(direction, dtype=float)
    n = np.linalg.norm(direction)
    direction = direction / n if n > 1e-9 else np.array([0.0, -1.0, 0.0])
    up = np.array([0.0, 0.0, 1.0])
    if abs(direction[2]) > 0.95:
        up = np.array([0.0, 1.0, 0.0])
    return focal + direction * dist, focal, up, dist


class SurfaceRayProbe:
    """Segment-vs-surface occlusion test on the mesh (vtkStaticCellLocator: no
    rendering, no GL, so it is safe off the GUI thread; build ~15 ms and ~25 us per
    ray on a 75k-face head). Build once per mesh; read-only afterwards."""

    def __init__(self, pts, faces):
        from vtkmodules.vtkCommonCore import mutable
        from vtkmodules.vtkCommonDataModel import vtkStaticCellLocator
        self._mutable = mutable
        faces = np.asarray(faces, dtype=np.int64)
        cells = np.hstack([np.full((len(faces), 1), 3, dtype=np.int64), faces]).ravel()
        self._poly = pv.PolyData(np.asarray(pts, dtype=np.float64), cells)
        self._loc = vtkStaticCellLocator()
        self._loc.SetDataSet(self._poly)
        self._loc.BuildLocator()

    def blocked(self, a, b):
        """True if the segment a -> b crosses the surface."""
        mutable = self._mutable
        return bool(self._loc.IntersectWithLine(
            tuple(map(float, a)), tuple(map(float, b)), 1e-9,
            mutable(0.0), [0.0, 0.0, 0.0], [0.0, 0.0, 0.0],
            mutable(0), mutable(0)))


def _plane_normal(p):
    """Unit normal of the best-fit plane through points p (smallest-variance axis)."""
    p = np.asarray(p, dtype=float)
    if len(p) < 3:
        return None
    _u, _s, vt = np.linalg.svd(p - p.mean(axis=0), full_matrices=False)
    n = vt[-1]
    nn = np.linalg.norm(n)
    return n / nn if nn > 1e-12 else None


def _cone_directions(base, half_angle_deg, n_az):
    base = np.asarray(base, dtype=float)
    helper = np.array([0.0, 0.0, 1.0]) if abs(base[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(base, helper)
    u /= np.linalg.norm(u)
    v = np.cross(base, u)
    a = np.radians(half_angle_deg)
    return [np.cos(a) * base + np.sin(a) * (np.cos(t) * u + np.sin(t) * v)
            for t in np.linspace(0.0, 2 * np.pi, n_az, endpoint=False)]


def view_candidates(loop_point_arrays, outward):
    """Unit view directions (focal point -> camera) to try: outward first, then
    +/- each loop's best-fit-plane normal, then cones around all of those.
    Near-duplicates are dropped; the order is stable."""
    outward = np.asarray(outward, dtype=float)
    outward = outward / np.linalg.norm(outward)
    bases = [outward]
    for arr in loop_point_arrays:
        n = _plane_normal(arr)
        if n is not None:
            bases += [n, -n]
    cands = list(bases)
    for b in bases:
        for deg in VIEW_CONE_DEGREES:
            cands += _cone_directions(b, deg, VIEW_CONE_AZIMUTHS)
    out = []
    for d in cands:
        d = np.asarray(d, dtype=float)
        d = d / np.linalg.norm(d)
        if all(float(np.dot(d, e)) < 0.999 for e in out):
            out.append(d)
    return out


def choose_defect_view(probe, loop_point_arrays, mesh_center, mesh_diag):
    """Pick the camera pose from which the defect is actually visible.

    Each candidate direction (view_candidates) puts the camera at the framing
    distance; rays are cast from the camera to sample points of ALL the defect's
    loops, stopping VIEW_TOL_SPANS x span short of the ring point so the ring's own
    adjacent faces do not count; the blocked fraction is the score. A camera
    that sits INSIDE the head (the ray from it away from the defect hits the
    surface) scores 1.0. The best score wins; VIEW_OUTWARD_BIAS x angle/pi makes
    ties go to the outward direction. Returns a dict: pos, focal, up, dist,
    direction, frac (blocked fraction of the chosen view), outward_frac,
    hidden (frac > HIDDEN_FRAC: even the best view is mostly hidden)."""
    arrays = [np.asarray(a, dtype=float) for a in loop_point_arrays if len(a)]
    mesh_center = np.asarray(mesh_center, dtype=float)
    pos0, focal, _up0, dist = defect_camera_pose(arrays, mesh_center, mesh_diag)
    outward = (pos0 - focal) / dist
    allp = np.vstack(arrays)
    span = float(np.linalg.norm(allp.max(axis=0) - allp.min(axis=0)))
    tol = VIEW_TOL_SPANS * max(span, 1e-9)
    targets = []
    for a in arrays:
        idx = np.unique(np.linspace(0, len(a) - 1,
                                    min(len(a), VIEW_SAMPLES_PER_LOOP)).astype(int))
        targets.append(a[idx])
    targets = np.vstack(targets)
    far = 4.0 * float(mesh_diag)

    def blocked_fraction(d):
        cam = focal + d * dist
        if probe.blocked(cam, cam + d * far):      # camera is inside / behind the surface
            return 1.0
        nb = 0
        for q in targets:
            u = cam - q
            un = np.linalg.norm(u)
            if un < 1e-12:
                continue
            if probe.blocked(cam, q + u / un * tol):
                nb += 1
        return nb / len(targets)

    best = None
    outward_frac = None
    for i, d in enumerate(view_candidates(arrays, outward)):
        frac = blocked_fraction(d)
        if i == 0:
            outward_frac = frac
        ang = float(np.arccos(np.clip(np.dot(d, outward), -1.0, 1.0)))
        score = frac + VIEW_OUTWARD_BIAS * ang / np.pi
        if best is None or score < best[0] - 1e-12:
            best = (score, d, frac)
    _score, d, frac = best
    pos, focal, up, dist = defect_camera_pose(arrays, mesh_center, mesh_diag, direction=d)
    return {"pos": pos, "focal": focal, "up": up, "dist": dist, "direction": d,
            "frac": float(frac), "outward_frac": float(outward_frac),
            "hidden": bool(frac > HIDDEN_FRAC)}


class ChangedFaceDetector:
    """Which faces of a fix result are NOT in the original mesh (the patch caps /
    remeshed region). Compares face centroids against the original's with a
    cKDTree (built lazily, once; thread-safe); unchanged faces keep their exact
    coordinates, so any centroid further than `tol` from every original centroid
    is new. numpy / scipy only (worker-safe)."""

    def __init__(self, pts, faces, tol=None):
        self._pts = np.asarray(pts, dtype=float)
        self._faces = np.asarray(faces)
        diag = (float(np.linalg.norm(self._pts.max(0) - self._pts.min(0)))
                if len(self._pts) else 1.0)
        self.tol = 1e-5 * diag if tol is None else float(tol)
        self._tree = None
        self._lock = threading.Lock()

    def mask(self, new_pts, new_faces):
        from scipy.spatial import cKDTree
        with self._lock:
            if self._tree is None:
                self._tree = cKDTree(self._pts[self._faces].mean(axis=1))
        cen = np.asarray(new_pts, dtype=float)[np.asarray(new_faces)].mean(axis=1)
        d, _ = self._tree.query(cen)
        return d > self.tol


def _cut_preview_serve(conn):
    """Entry point of the preview child process (spawn target; lives in
    cut_preview_server.py so the child does not import pyvista)."""
    from cut_preview_server import serve
    serve(conn)


class PreviewCache:
    """B1: the real single-loop cut_and_patch for every option of every defect,
    computed in the background on the ORIGINAL arrays and cached (result or
    failure reason), so the cards can say "Preview ready" / "Not available" and
    the main view can show what an option would really produce.

    Qt-free and VTK-free (numpy / scipy / pymeshfix only), so it runs in a
    plain worker thread and is unit-testable. Thread rules:
      * `pts` / `faces` are only ever READ (cut_and_patch never modifies its
        inputs); results are stored as read-only arrays, so previews and the
        Apply worker never share mutable state;
      * `_compute_lock` serialises the native cut/patch calls (a preview, the
        single-defect Apply fast path and the multi-defect Apply all take it),
        so two pymeshfix/pymeshlab runs never overlap;
      * `stop()` stops the background run BETWEEN options (an option that is
        already being cut finishes -- it cannot be interrupted); `run()` can be
        called again later (after Undo) and picks up what is still pending.
    Keys are (defect index, option index)."""

    def __init__(self, pts, faces, loops_by_key, use_process=False):
        self.use_process = use_process   # background run in a child process (see below)
        self._proc = None
        self._conn = None
        self._shut = False
        self.pts = pts
        self.faces = faces
        self._loops = dict(loops_by_key)
        self._entries = {k: {"state": "pending", "result": None, "reason": None,
                             "mask": None}
                         for k in self._loops}
        self.detector = ChangedFaceDetector(pts, faces)
        self._state_lock = threading.Lock()
        self._compute_lock = threading.Lock()
        self._stop = threading.Event()
        self._running = 0          # number of run() calls in flight

    # -- queries (any thread) ------------------------------------------------
    def keys(self):
        return list(self._loops)

    def state(self, key):
        with self._state_lock:
            e = self._entries[key]
            return e["state"], e["reason"]

    def result(self, key):
        """(pts, faces) of a ready preview, else None."""
        with self._state_lock:
            e = self._entries[key]
            return e["result"] if e["state"] == "ready" else None

    def changed_mask(self, key):
        """Bool array over the ready preview's faces: True = not in the original
        (computed in the worker right after the cut), else None."""
        with self._state_lock:
            return self._entries[key]["mask"]

    def pending_keys(self):
        with self._state_lock:
            return [k for k, e in self._entries.items() if e["state"] == "pending"]

    def all_done(self):
        with self._state_lock:
            return all(e["state"] in ("ready", "failed")
                       for e in self._entries.values())

    def is_running(self):
        return self._running > 0

    def summary(self):
        with self._state_lock:
            return {f"{k[0]}.{k[1]}": e["state"] for k, e in self._entries.items()}

    # -- control -------------------------------------------------------------
    def stop(self):
        self._stop.set()

    def shutdown(self):
        """Stop for good (the window is closing): no new options start and a
        preview cut running in the child process is terminated at once."""
        self._shut = True
        self._stop.set()
        self._kill_child()

    # -- child process (background previews only) -------------------------
    # The native pymeshfix / PyMeshLab calls hold the GIL for seconds, which froze
    # the GUI in a worker THREAD (2 s timer gaps measured). A child process has no
    # GIL to fight over, and shutdown() can kill it. Apply's own compute still runs
    # in-process (get_or_compute), serialised with this through _compute_lock.
    def _ensure_process(self):
        if self._proc is not None and self._proc.is_alive():
            return
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        proc = ctx.Process(target=_cut_preview_serve, args=(child,), daemon=True)
        proc.start()
        child.close()
        parent.send((np.asarray(self.pts), np.asarray(self.faces)))
        self._proc, self._conn = proc, parent

    def _kill_child(self):
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _cut_in_process(self, loop):
        self._ensure_process()
        self._conn.send(np.asarray(loop))
        # poll() in short slices (it releases the GIL) so a shutdown or a hung child
        # is noticed: the wait is capped at PREVIEW_CUT_TIMEOUT_S, after which the
        # child is killed and the caller falls back to computing in-process.
        deadline = time.monotonic() + PREVIEW_CUT_TIMEOUT_S
        while not self._conn.poll(0.25):
            if self._shut:
                raise EOFError("preview cancelled")
            if not self._proc.is_alive():
                raise EOFError("preview process died")
            if time.monotonic() > deadline:
                self._kill_child()
                raise TimeoutError("preview process timed out")
        kind, payload = self._conn.recv()
        if kind == "ok":
            return payload
        raise ValueError(payload)

    def resume(self):
        self._stop.clear()

    def exclusive(self):
        """Context manager: hold the compute lock (used by the multi-defect
        Apply so it never overlaps a background preview)."""
        return self._compute_lock

    # -- work ----------------------------------------------------------------
    def _compute(self, key, background=False):
        """Compute one preview (caller holds _compute_lock). Stores and returns
        the entry's (state, reason). background=True uses the child process when
        enabled; a cancelled (shutdown) cut leaves the entry pending."""
        with self._state_lock:
            e = self._entries[key]
            if e["state"] in ("ready", "failed"):
                return e["state"], e["reason"]
            e["state"] = "running"
        try:
            from tunnel_loop_extractor import cut_and_patch
            res = None
            if background and self.use_process and not self._shut:
                try:
                    res = self._cut_in_process(self._loops[key])
                except (EOFError, OSError, BrokenPipeError):
                    if self._shut:                       # terminated on purpose
                        with self._state_lock:
                            e["state"] = "pending"
                        return "pending", None
                    self.use_process = False             # child died: do it here
            if res is None:
                res = cut_and_patch(self.pts, self.faces, self._loops[key])
            new_pts, new_faces = res
            new_pts = np.array(new_pts, dtype=np.float64)
            new_faces = np.array(new_faces, dtype=np.int64)
            new_pts.flags.writeable = False
            new_faces.flags.writeable = False
            try:       # the "new surface" highlight; failure only loses the colour
                mask = self.detector.mask(new_pts, new_faces)
            except Exception as ex:   # noqa: BLE001
                print(f"[tunnel] changed-face detection failed: {ex}", file=sys.stderr)
                mask = None
            with self._state_lock:
                e.update(state="ready", result=(new_pts, new_faces), reason=None,
                         mask=mask)
        except Exception as ex:   # noqa: BLE001 - cached as the failure reason
            with self._state_lock:
                e.update(state="failed", result=None, reason=str(ex))
        return self.state(key)

    def run(self, order=None, on_update=None):
        """Background loop (worker thread): compute each pending option in
        `order` (default: all keys). Checks the stop flag between options."""
        with self._state_lock:
            self._running += 1
        try:
            for key in (self.keys() if order is None else order):
                if self._stop.is_set():
                    break
                with self._compute_lock:
                    if self._stop.is_set():
                        break
                    if self.state(key)[0] != "pending":
                        continue
                    self._compute(key, background=True)
                    if self.state(key)[0] == "pending":   # cancelled mid-cut
                        break
                if on_update is not None:
                    try:
                        on_update(key)
                    except Exception as ex:   # noqa: BLE001
                        print(f"[tunnel] preview update callback failed: {ex}",
                              file=sys.stderr)
        finally:
            with self._state_lock:
                self._running -= 1

    def get_or_compute(self, key):
        """The cached result for `key`, computing it now if it is not there yet
        (waits for an in-flight preview first). Raises ValueError with the
        failure reason if the option cannot be applied. Used by the
        single-defect Apply, so what was previewed is exactly what is saved."""
        with self._compute_lock:
            state, reason = self._compute(key)
        if state == "failed":
            raise ValueError(reason)
        return self.result(key)


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
    whole_head / set_see_through; Space / Tab / Shift+Tab / C / P / Esc map to
    toggle_active_loop / next_handle / prev_handle / request_apply /
    toggle_view / undo (in the after-view).

    Flow (stage 3, A6): Apply COMPUTES the cut (worker) and shows the result in
    the main view -- nothing is written. `mode` is "choose" until a result
    exists, then "after": the user presses keep() (backup-if-absent + atomic
    save + sentinel + close) or undo() (result discarded, back to choosing,
    disk untouched). B1: a PreviewCache computes the real single-loop result
    of every option in the background; a failed preview makes that option
    unavailable. Hooks:
      on_change()        -- anything the panel shows changed (selection, active
                         defect, busy, mode, preview state): re-sync the panel
      status_cb(text, c) -- show a status line (defaults to a canvas text)
      busy_cb(bool)      -- a cut / save started or ended
      job_runner(job, finish) -- run `job()` off the GUI thread and call
                         finish(error_or_None) back on it (defaults to running
                         the job inline, which is what the headless tests use).
      preview_runner(run_fn) -- start the background preview run: call
                         run_fn(on_update) off the GUI thread and, for each
                         finished option, deliver on_preview_update(key) on the
                         GUI thread. None disables previews (unless
                         `previews=True`, which headless tests use and drive by
                         hand through self.previews.run()).
    """

    def __init__(self, plotter, pts, pairs, mesh_path=None, faces=None,
                 on_success=None, busy_cb=None,
                 genus=None, on_change=None, status_cb=None, job_runner=None,
                 previews=None, preview_runner=None, preview_process=False):
        self.pl = plotter
        self.pts = np.asarray(pts)
        self.faces = None if faces is None else np.asarray(faces)
        self.mesh_path = mesh_path
        self.on_success = on_success  # callable: invoked after a successful Keep
        self.busy_cb = busy_cb      # busy_cb(bool): window enables/disables the panel
        self.genus = genus          # mesh genus, for the "k of g defects" note
        self.on_change = on_change
        self.status_cb = status_cb
        self.job_runner = job_runner
        self.preview_runner = preview_runner
        self.busy = False           # True while a cut or a save is running
        self.mode = "choose"        # "choose" | "after"
        self.view = "before"        # what the main view shows: "before" | "after"
        self.result = None          # (pts, faces) of the unkept fix, mode "after"
        self.result_genus = None
        self.kept = False           # True once Keep saved the result
        self._surface_cache = {}    # key -> pv mesh (main thread only)
        self._mask_any = {}         # key -> that result has new (green) faces
        self.see_through = False
        self._auto_see = False      # True while see-through was switched on by itself
        self._probe = None          # SurfaceRayProbe, built lazily (first zoom)
        self._poses = {}            # defect index -> choose_defect_view() result
        self._detector = None       # ChangedFaceDetector for multi-defect results
        self.result_mask = None     # changed-face mask of the unkept fix
        self.last_pose_ms = None    # time the last (uncached) pose took
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
        if previews is None:
            previews = PREVIEWS_ENABLED and preview_runner is not None
        self.previews = None
        if previews and self.faces is not None and self.handles:
            self.previews = PreviewCache(
                self.pts, self.faces,
                {(hi, li): lp for hi, h in enumerate(self.handles)
                 for li, lp in enumerate(h["loops"])},
                use_process=preview_process)
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

    def preview_state(self, hi, li):
        """(state, reason) of option li of defect hi, or None without previews."""
        if self.previews is None:
            return None
        return self.previews.state((hi, li))

    def option_usable(self, hi, li):
        """False for a missing option or one whose preview failed a safety check."""
        h = self.handles[hi]
        if not 0 <= li < len(h["loops"]):
            return False
        st = self.preview_state(hi, li)
        return st is None or st[0] != "failed"

    def cards(self, hi=None):
        hi = self.active if hi is None else hi
        h = self.handles[hi]
        pv_states = (None if self.previews is None else
                     [self.preview_state(hi, li) for li in range(len(h["loops"]))])
        return option_cards(h["labels"], [r for (_lp, r) in h["blocked"]], h["sel"],
                            pv_states)

    def can_apply(self):
        """Every defect still has a usable (not failed-preview) selected option."""
        return all(self.option_usable(hi, h["sel"])
                   for hi, h in enumerate(self.handles))

    def notes(self):
        h = self.handles[self.active]
        notes = defect_notes(h["labels"], len(self.handles), self.genus)
        if self.auto_see_through_note():
            notes.insert(0, HIDDEN_NOTE)
        if self.new_surface_legend():
            notes.append(self.new_surface_legend())
        for hi, hh in enumerate(self.handles):
            if not any(self.option_usable(hi, li) for li in range(len(hh["loops"]))):
                notes.append(
                    f"Defect {hi + 1} can't be fixed inside the app: neither way "
                    "passed the safety checks. Use 'Export Loops for Blender' and "
                    "make the cut there.")
        return notes

    def is_classified(self, hi=None):
        h = self.handles[self.active if hi is None else hi]
        return len(h["labels"]) == 2 and all(lb in OPTION_TITLE for lb in h["labels"])

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
        """Make defect `idx` the active one; re-frames the camera on it (and turns
        see-through on / off by itself when that defect needs it, see show_defect)."""
        if (not 0 <= idx < len(self.handles) or idx == self.active
                or self.mode != "choose" or self.busy):
            return
        self.active = idx
        self.redraw()
        if zoom:
            self.show_defect()
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
        if self.mode != "choose" or self.busy:
            return
        if self.option_usable(self.active, li) and h["sel"] != li:
            h["sel"] = li
            self.redraw()
            self.export()
            self._changed()

    def toggle_active_loop(self):
        """Switch to the other option of the active defect (Space key)."""
        h = self.handles[self.active]
        if len(h["loops"]) > 1:
            self.select_option((h["sel"] + 1) % len(h["loops"]))   # no-op if unusable

    def focus_points(self):
        return [list(map(float, self.pts[h["loops"][h["sel"]]].mean(0)))
                for h in self.handles]

    # ------------------------------------------------------------------
    # Camera (A4)
    # ------------------------------------------------------------------
    def _defect_arrays(self, hi):
        h = self.handles[hi]
        return ([self.pts[lp] for lp in h["loops"]]
                + [self.pts[lp] for (lp, _r) in h["blocked"]])

    def defect_pose(self, hi=None):
        """Cached camera pose of defect `hi` (default: the active one): the view
        direction with the least occluded rings (choose_defect_view). The ray
        locator is built once, on first use; without face data the plain outward
        pose is used. Never raises."""
        hi = self.active if hi is None else hi
        pose = self._poses.get(hi)
        if pose is not None:
            return pose
        arrays = self._defect_arrays(hi)
        t0 = time.perf_counter()
        try:
            if self.faces is None:
                raise ValueError("no face data")
            if self._probe is None:
                self._probe = SurfaceRayProbe(self.pts, self.faces)
            pose = choose_defect_view(self._probe, arrays, self.center, self.diag)
        except Exception as e:
            print(f"[tunnel] view search skipped ({e}); using the outward view",
                  file=sys.stderr)
            pos, focal, up, dist = defect_camera_pose(arrays, self.center, self.diag)
            pose = {"pos": pos, "focal": focal, "up": up, "dist": dist,
                    "direction": (pos - focal) / dist, "frac": 0.0,
                    "outward_frac": 0.0, "hidden": False}
        self.last_pose_ms = (time.perf_counter() - t0) * 1000.0
        self._poses[hi] = pose
        return pose

    def zoom_to_defect(self):
        """Frame the active defect (Z key / button): focal point = pair centroid,
        a few loop diameters away, from the least occluded direction (see
        defect_pose). The same pose is used for Before / After and the after-view
        because the camera is never touched by them."""
        try:
            pose = self.defect_pose()
            self.pl.camera_position = [tuple(pose["pos"]), tuple(pose["focal"]),
                                       tuple(pose["up"])]
            self.pl.reset_camera_clipping_range()
            self.pl.render()
        except Exception as e:
            print(f"[tunnel] zoom to defect skipped: {e}", file=sys.stderr)

    def show_defect(self):
        """zoom_to_defect plus the automatic see-through decision: when even the
        best view of this defect is mostly hidden (> HIDDEN_FRAC of the ring
        points blocked) see-through is switched on (and the panel says so); when
        a defect that does not need it is shown and see-through was only on
        because of the automatic rule, it is switched off again. A user's own
        X / button choice is never overridden."""
        self.zoom_to_defect()
        hidden = bool(self.defect_pose().get("hidden"))
        if hidden and not self.see_through:
            self.set_see_through(True, auto=True)
        elif not hidden and self.see_through and self._auto_see:
            self.set_see_through(False)

    def auto_see_through_note(self):
        """True while the panel should say see-through was turned on for the
        active defect because it is hidden."""
        return bool(self._auto_see and self.see_through
                    and self.mode == "choose"
                    and self._poses.get(self.active, {}).get("hidden"))

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

    def set_see_through(self, on, auto=False):
        """Make the head surface see-through (opacity ~0.4) so loops inside ear
        folds show; False restores the normal opacity. auto=True marks the change
        as the automatic hidden-defect rule (any other call is the user's)."""
        self.see_through = bool(on)
        self._auto_see = bool(auto) and self.see_through
        opacity = SEE_THROUGH_OPACITY if self.see_through else MESH_OPACITY
        try:
            for name in ("surface", "components", "result_surface"):
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
        if (self.mode == "choose" and not self.busy and self.option_usable(hi, li)
                and self.handles[hi]["sel"] != li):
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
            # Labels sit about half a defect-size from their ring (not a fixed share of
            # the head), so they stay inside the zoomed view and their leader lines
            # stay short.
            pair = np.vstack([self.pts[lp] for lp in h["loops"]])
            span = float(np.linalg.norm(pair.max(0) - pair.min(0)))
            off = float(np.clip(0.6 * span, 0.01 * self.diag, 0.11 * self.diag))

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
                self._label([cc], [_label_anchor(cc, self.center, self.diag, +1,
                                                 off=off)],
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
                                               +1 if is_sel else -1, off=off)],
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
                                [_label_anchor(bc, self.center, self.diag, -1,
                                               off=off)],
                                [UNAVAILABLE_TEXT], COLOR_BLOCKED,
                                f'blocked_lbl_{hi}_{bi}')

        # The single on-canvas hint line (everything else lives in the panel).
        try:
            self.pl.add_text(CANVAS_HINT, position="lower_right", font_size=9,
                             color=COLOR_CANVAS_HINT, name="kb_hint")
        except Exception:
            pass

        self._apply_view(render=False)
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

    # ------------------------------------------------------------------
    # Before / After display (B1 previews and the A6 after-view)
    #
    # The original surface actor ("surface", plus the problem overlays and the
    # rings) is never rebuilt: showing a result adds ONE extra actor
    # ("result_surface") and hides the original + its overlays; going back just
    # flips visibility. The camera is never touched.
    # ------------------------------------------------------------------
    OVERLAY_PREFIXES = ("sel_loop_", "blocked_loop_", "lbl_", "blocked_lbl_")
    PROBLEM_ACTORS = ("problem_edges", "components", "si_faces")

    def shown_result(self):
        """(cache_key, (pts, faces)) of the mesh the 'After' view would show, or
        None: the unkept fix in the after-view, else the selected option's ready
        preview."""
        if self.mode == "after":
            return ("fix", self.result) if self.result is not None else None
        if self.previews is None:
            return None
        key = (self.active, self.handles[self.active]["sel"])
        res = self.previews.result(key)
        return (key, res) if res is not None else None

    def can_toggle_view(self):
        return self.shown_result() is not None and not self.busy

    def toggle_view(self):
        """Before / After (key P). After = the unkept fix, or the selected
        option's cached preview. No-op (with a status line) until one exists."""
        if self.busy:
            return
        if self.shown_result() is None:
            if self.previews is not None and self.mode == "choose":
                st = self.preview_state(self.active, self.handles[self.active]["sel"])
                msg = ("This option can't be previewed."
                       if st and st[0] == "failed" else
                       "The preview isn't ready yet - it appears here when it is.")
                self._status(msg, COLOR_PALE_YELLOW)
            return
        self.view = "after" if self.view == "before" else "before"
        self._apply_view()
        self._changed()

    def _get_detector(self):
        if self.previews is not None:
            return self.previews.detector
        if self._detector is None:
            self._detector = ChangedFaceDetector(self.pts, self.faces)
        return self._detector

    def _changed_mask(self, key, result):
        """Faces of `result` that are not in the original mesh. Normally already
        computed off the GUI thread (preview worker / Apply job); only a missing
        one is computed here (~0.1 s)."""
        m = None
        if key == "fix":
            m = self.result_mask
        elif self.previews is not None:
            m = self.previews.changed_mask(key)
        if m is None or len(m) != len(result[1]):
            try:
                m = self._get_detector().mask(*result)
            except Exception as e:
                print(f"[tunnel] changed-face detection failed: {e}", file=sys.stderr)
                m = None
        return m

    def new_surface_legend(self):
        """GREEN_LEGEND while the main view shows a result that has new faces."""
        shown = self.shown_result()
        if (shown is not None and self.view == "after"
                and self._mask_any.get(shown[0])):
            return GREEN_LEGEND
        return None

    def _surface_mesh(self, key, result):
        mesh = self._surface_cache.get(key)
        if mesh is None:
            rpts, rfaces = result
            cells = np.hstack([np.full((len(rfaces), 1), 3, dtype=np.int64),
                               np.asarray(rfaces, dtype=np.int64)]).ravel()
            mesh = pv.PolyData(np.asarray(rpts, dtype=np.float64), cells)
            mask = self._changed_mask(key, result)
            has_new = mask is not None and bool(np.any(mask))
            if has_new:
                mesh.cell_data[CHANGED_ARRAY] = np.asarray(mask, dtype=np.uint8)
            self._mask_any[key] = has_new
            try:
                mesh = mesh.compute_normals(auto_orient_normals=True,
                                            consistent_normals=True, inplace=False)
            except Exception:
                pass
            if (has_new and (CHANGED_ARRAY not in mesh.cell_data
                             or mesh.n_cells != len(mask))):
                self._mask_any[key] = False        # a filter dropped it: plain colour
            self._surface_cache[key] = mesh
        return mesh

    def _set_visible(self, name, visible):
        try:
            actor = self.pl.actors.get(name)
            if actor is not None:
                actor.SetVisibility(bool(visible))
        except Exception:
            pass

    def _apply_view(self, render=True):
        """Show the original (surface + overlays + rings) or the 'After' mesh,
        according to self.view and self.mode."""
        shown = self.shown_result()
        if shown is None:
            self.view = "before"
        showing_after = self.view == "after" and shown is not None
        if showing_after:
            key, result = shown
            try:
                opacity = SEE_THROUGH_OPACITY if self.see_through else MESH_OPACITY
                rmesh = self._surface_mesh(key, result)
                look = dict(opacity=opacity, show_edges=False,
                            ambient=MESH_AMBIENT, diffuse=MESH_DIFFUSE,
                            specular=MESH_SPECULAR, name="result_surface",
                            reset_camera=False, render=False)
                if self._mask_any.get(key):
                    # the new surface (patch caps / remeshed region) in soft green
                    self.pl.add_mesh(rmesh, scalars=CHANGED_ARRAY,
                                     cmap=[COLOR_MESH_BASE, COLOR_NEW_SURFACE],
                                     clim=[0, 1], n_colors=2,
                                     show_scalar_bar=False, **look)
                else:
                    self.pl.add_mesh(rmesh, color=COLOR_MESH_BASE, **look)
            except Exception as e:
                print(f"[tunnel] could not show the result: {e}", file=sys.stderr)
                self.view = "before"
                showing_after = False
        self._set_visible("result_surface", showing_after)
        self._set_visible("surface", not showing_after)
        for name in self.PROBLEM_ACTORS:
            self._set_visible(name, not showing_after)
        rings = self.mode == "choose" and not showing_after
        try:
            names = list(self.pl.actors.keys())
        except Exception:
            names = []
        for name in names:
            if isinstance(name, str) and name.startswith(self.OVERLAY_PREFIXES):
                self._set_visible(name, rings)
        if render:
            try:
                self.pl.render()
            except Exception:
                pass

    def on_preview_update(self, key):
        """GUI thread: the preview of option `key` finished (ready or failed).
        If the option the user had selected just failed, move to the other one;
        keep the After view in sync; refresh the panel."""
        hi, li = key
        st = self.preview_state(hi, li)
        h = self.handles[hi]
        if st and st[0] == "failed" and h["sel"] == li and self.mode == "choose":
            for other in range(len(h["loops"])):
                if other != li and self.option_usable(hi, other):
                    h["sel"] = other
                    self.redraw()
                    self.export()
                    self._status(
                        f"Defect {hi + 1}: that option isn't available "
                        f"({plain_failure(st[1])}), so the other one is selected.",
                        COLOR_PALE_YELLOW)
                    break
        if self.mode == "choose" and self.view == "after":
            self._apply_view()          # the selected option's preview may have just landed
        self._changed()

    def start_previews(self):
        """Start the background preview run (call once the rings are shown).
        Priority: the selected option of the active defect, then its other
        option, then the remaining defects."""
        if self.previews is None or self.preview_runner is None:
            return
        order = []
        h = self.handles[self.active]
        order.append((self.active, h["sel"]))
        order += [(self.active, li) for li in range(len(h["loops"])) if li != h["sel"]]
        order += [(hi, li) for hi, hh in enumerate(self.handles) if hi != self.active
                  for li in sorted(range(len(hh["loops"])), key=lambda x: x != hh["sel"])]
        self.previews.resume()
        self.preview_runner(lambda cb: self.previews.run(order, on_update=cb))

    def previews_running(self):
        return self.previews is not None and self.previews.is_running()

    # ------------------------------------------------------------------
    # Apply -> after-view -> Keep / Undo (A6)
    # ------------------------------------------------------------------
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

    def has_unkept_result(self):
        return self.mode == "after" and self.result is not None and not self.kept

    def request_apply(self):
        """C key / Apply button. Computes the fix and shows it (nothing is
        written until Keep). Ignored while a cut or save is already running,
        in the after-view, or when a chosen option failed its preview."""
        if self.busy or self.mode != "choose":
            return
        if self.faces is None or not self.mesh_path:
            self._status("The fix is unavailable (no mesh data).", COLOR_CUT_INACTIVE)
            return
        if not self.can_apply():
            self._status("One of the chosen options isn't available (it failed "
                         "a safety check), so there is nothing to apply.",
                         COLOR_CUT_INACTIVE)
            return
        self.apply_cut_and_patch()

    def apply_cut_and_patch(self):
        """Compute the fix for the chosen option(s) and enter the after-view.

        One defect -> the cached verified preview if there is one (else
        cut_and_patch via the cache, which stores it), so what was previewed is
        exactly what is kept; several -> cut_and_patch_loops on the chosen set
        (its union pre-check + post-checks; the after-view shows the real
        combined result). Previews that have not started are stopped (they resume
        after Undo); a cut already in flight is waited for through the compute
        lock, so two native cut/patch runs never overlap. NOTHING is written
        here: Keep saves. The job runs through job_runner (a worker thread in
        the window); _finish_apply runs on the GUI thread. On failure the status
        names the failed check and the mesh is unchanged."""
        if self.busy or self.mode != "choose":
            return
        if self.faces is None or not self.mesh_path:
            self._status("The fix is unavailable (no mesh data).", COLOR_CUT_INACTIVE)
            return
        self._set_busy(True)
        chosen = [(hi, h["sel"]) for hi, h in enumerate(self.handles)]
        cut_loops = [self.handles[hi]["loops"][li] for hi, li in chosen]
        holder = {}
        cache = self.previews
        if cache is not None:
            cache.stop()

        def job():
            # Worker-safe: numpy/scipy/pymeshfix only, no rendering, no writes.
            from tunnel_loop_extractor import (cut_and_patch, cut_and_patch_loops,
                                               mesh_genus)
            if len(cut_loops) == 1 and cache is not None:
                res = cache.get_or_compute(chosen[0])
            elif len(cut_loops) == 1:
                res = cut_and_patch(self.pts, self.faces, cut_loops[0])
            elif cache is not None:
                with cache.exclusive():
                    res = cut_and_patch_loops(self.pts, self.faces, cut_loops)
            else:
                res = cut_and_patch_loops(self.pts, self.faces, cut_loops)
            holder["result"] = res
            holder["genus"] = int(mesh_genus(np.asarray(res[0]), np.asarray(res[1])))
            try:     # the green "new surface" faces, off the GUI thread
                m = (cache.changed_mask(chosen[0])
                     if len(cut_loops) == 1 and cache is not None else None)
                holder["mask"] = (m if m is not None
                                  else self._get_detector().mask(*res))
            except Exception as e:   # noqa: BLE001 - only the colour is lost
                print(f"[tunnel] changed-face detection failed: {e}", file=sys.stderr)

        self._status("Fixing the mesh... " + CUT_HINT, COLOR_PALE_YELLOW)

        def finish(error):
            self._finish_apply(error, holder)

        if callable(self.job_runner):
            self.job_runner(job, finish)
            return
        error = None
        try:
            job()
        except Exception as e:
            error = e
        finish(error)

    def _finish_apply(self, error, holder):
        """GUI-thread tail of apply_cut_and_patch (error is None on success)."""
        if error is not None:
            self._set_busy(False)
            self._status(
                f"The fix was NOT applied; your mesh is unchanged.\n{error}\n"
                "Use 'Export Loops for Blender' to make this cut manually instead.",
                "#e6a09f")
            print(f"[tunnel] fix failed: {error}", file=sys.stderr)
            if self.previews is not None:
                # A failed single option is now cached as unavailable; the
                # background previews (stopped by Apply) pick up again.
                self.on_preview_update_after_failure()
            return
        self.result = holder["result"]
        self.result_mask = holder.get("mask")
        self.result_genus = holder["genus"]
        self.mode = "after"
        self.view = "after"
        self._set_busy(False)
        self._apply_view()
        self._status(fixed_banner(self.result_genus), COLOR_PALE_GREEN)
        self._changed()

    def on_preview_update_after_failure(self):
        """After a failed Apply: re-sync the cards (the failed option, if it was
        a single defect's, is now cached as unavailable and the selection moves
        to the other option) and restart whatever previews are still pending."""
        for hi, h in enumerate(self.handles):
            self.on_preview_update((hi, h["sel"]))
        if self.previews.pending_keys():
            self.start_previews()

    def undo(self):
        """Discard the unkept result and go back to choosing (Undo / Esc in the
        after-view). Nothing was written, so there is nothing to restore."""
        if self.busy or self.mode != "after":
            return
        self.result = None
        self.result_mask = None
        self.result_genus = None
        self.mode = "choose"
        self.view = "before"
        self._surface_cache.pop("fix", None)
        self._mask_any.pop("fix", None)
        self.redraw()                 # rings back, original surface shown
        self._status("Undone - nothing was changed.", "#ccc")
        self._changed()
        if self.previews is not None and self.previews.pending_keys():
            self.start_previews()

    def keep(self):
        """Keep & Close: save the already-computed, already-checked result.
        project_store.apply_with_backup does backup-if-absent -> temp write +
        re-read check -> os.replace; on ANY failure the original aligned_head.ply
        is untouched and we stay in the after-view (Undo or retry). On success a
        sentinel cutpatch_report.json is written so the GUI re-inspects, then
        on_success() closes the viewer. Impossible while a worker runs."""
        if self.busy or self.mode != "after" or self.result is None:
            return
        self._set_busy(True)
        result = self.result

        def job():
            from project_store import apply_with_backup
            apply_with_backup(self.mesh_path, lambda: result, _write_ply_tmp)

        self._status("Saving the fixed mesh...", COLOR_PALE_YELLOW)
        if callable(self.job_runner):
            self.job_runner(job, self._finish_keep)
            return
        error = None
        try:
            job()
        except Exception as e:
            error = e
        self._finish_keep(error)

    def _finish_keep(self, error):
        """GUI-thread tail of keep() (error is None on success)."""
        if error is not None:
            self._set_busy(False)
            self._status(
                f"The fix was NOT saved; your mesh is unchanged.\n{error}\n"
                "You can try Keep again, or Undo.", "#e6a09f")
            print(f"[tunnel] save failed: {error}", file=sys.stderr)
            return

        # The mesh is now replaced; the rest is best-effort notification.
        self.kept = True
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

        self._status("Saved. Closing and re-checking the mesh...", COLOR_PALE_GREEN)
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
                       busy_cb=None, genus=None, on_change=None,
                       status_cb=None, job_runner=None, preview_runner=None,
                       preview_process=False):
    """Main-thread: draw the computed tunnel data onto the plotter. Returns the
    same dict shape add_tunnel_overlay used (rendered, tight, export_txt,
    n_loops, focus, selector) or False if nothing to draw.

    on_success: optional callable forwarded to TunnelSelector — called when
    Keep has saved the fix so the hosting window can close itself and trigger GUI
    re-validation. genus / on_change / status_cb / job_runner / preview_runner
    are forwarded to TunnelSelector (panel hooks, the off-thread cut runner and
    the background preview runner).
    """
    mode = data.get("mode")

    if mode == "pairs":
        pts, faces, pairs = data["pts"], data["faces"], data["pairs"]
        selector = TunnelSelector(plotter, pts, pairs, mesh_path=mesh_path,
                                  faces=faces, on_success=on_success,
                                  busy_cb=busy_cb,
                                  genus=genus, on_change=on_change,
                                  status_cb=status_cb, job_runner=job_runner,
                                  preview_runner=preview_runner,
                                  preview_process=preview_process)
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


def quick_genus(mesh_path):
    """Genus of the mesh file as PyMeshLab reports it (the same number
    mesh_inspector.inspect_mesh reports, without its other checks). Worker-safe:
    no VTK."""
    import pymeshlab
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(mesh_path))
    topo = ms.get_topological_measures()
    genus = topo.get("genus", None)
    if genus is None:
        genus = ms.compute_topological_measures().get("genus", None)
    return int(genus) if genus is not None else 0


def compute_self_intersections(mesh_path):
    """(number of self-intersecting faces, their face ids) via PyMeshLab.
    Worker-safe: no VTK (the old version did this on the GUI thread, with a
    Python loop over every face)."""
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
    cm = ms.current_mesh()
    n_si = int(cm.selected_face_number())
    ids = []
    if n_si > 0:
        try:
            ids = [int(i) for i in np.flatnonzero(np.asarray(cm.face_selection_array()))]
        except Exception:
            ids = [i for i in range(cm.face_number()) if cm.face_is_selected(i)]
    return n_si, ids


def _add_si_overlay(pl, mesh, n_si, face_ids):
    """Main-thread: draw the self-intersecting faces (yellow); returns the legend line."""
    if n_si > 0:
        si_mesh = mesh.extract_cells(face_ids) if face_ids else None
        if si_mesh is not None and si_mesh.n_points > 0:
            pl.add_mesh(si_mesh, color="yellow", opacity=MESH_OPACITY, name="si_faces")
            return f"Yellow faces: {n_si} self-intersection(s)"
        return f"{n_si} self-intersection(s)"
    return "[OK] No self-intersecting faces"


def _add_static_overlays(pl, mesh, mesh_path, include_si=True):
    """Render the FAST problem overlays (base mesh, boundary/non-manifold edges,
    components, and -- unless include_si is False -- self-intersections) on the
    plotter and return legend lines. The viewer window passes include_si=False and
    adds the self-intersection overlay later from a worker's result."""
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

    if include_si:
        try:
            n_si, face_ids = compute_self_intersections(mesh_path)
            legend_lines.append(_add_si_overlay(pl, mesh, n_si, face_ids))
        except Exception as e:
            legend_lines.append(f"Self-intersection check: {e}")

    return legend_lines


def _workers_running(win):
    """True if any of the window's QThreads is still running."""
    ws = [getattr(win, "_prep", None), getattr(win, "_worker", None),
          getattr(win, "_cut_worker", None)] + list(getattr(win, "_preview_workers", []))
    for w in ws:
        try:
            if w is not None and w.isRunning():
                return True
        except Exception:
            pass
    return False


def view_mesh_problems(mesh_path, _script=None):
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
    if _script is not None:        # smoke-test hook: _script(win, app) runs once shown
        from PySide6 import QtCore
        QtCore.QTimer.singleShot(0, lambda: _script(win, app))
    app.exec()
    # The window is closed. A QThread that is still running when the process tears
    # down aborts it (rc 0xC0000409), and the startup checks / loop finder cannot be
    # interrupted. Nothing is pending on disk at this point (a save refuses to be
    # closed over, and Keep has already written its sentinel), so make sure the
    # preview child is gone and leave right away with a clean exit code.
    try:
        sel = getattr(win, "_selector", None)
        if sel is not None and sel.previews is not None:
            sel.previews.shutdown()
    except Exception:
        pass
    if _workers_running(win):
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)


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

    class _PreviewWorker(QtCore.QThread):
        """Runs the background preview loop (PreviewCache.run) off the GUI
        thread. VTK-free; each finished option is announced with `updated`
        (delivered on the GUI thread)."""
        updated = QtCore.Signal(object)

        def __init__(self, run_fn):
            super().__init__()
            self._run_fn = run_fn

        def run(self):
            try:
                self._run_fn(self.updated.emit)
            except BaseException as e:   # noqa: BLE001
                print(f"[tunnel] preview worker error: {e}", file=sys.stderr)

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
            self._state = QtWidgets.QLabel("")
            self._state.setWordWrap(True)
            for w in (self._title, self._sub, self._state):
                w.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                lay.addWidget(w)
            self._interactive = False

        def set_state(self, title, subtitle, selected, enabled, tooltip, accent,
                      status=None, preview_state=None):
            self._interactive = enabled
            self._title.setText(("●  " if selected else "○  ") + title)
            self._sub.setText(subtitle)
            self._sub.setVisible(bool(subtitle))
            self._state.setText(status or "")
            self._state.setVisible(bool(status))
            self._state.setStyleSheet(
                "font-size: 11px; background: transparent; color: "
                + {"ready": "#7fd6a0", "failed": "#e6a09f"}.get(preview_state, "#9aa")
                + ";")
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

    class _PrepWorker(QtCore.QThread):
        """Startup checks that must not block the window (VTK-free, PyMeshLab
        only): the genus first (it is all the loop finder needs), then the
        self-intersection count for the yellow overlay."""
        genus_ready = QtCore.Signal(int, str)       # genus, error text ("" = ok)
        si_ready = QtCore.Signal(int, object, str)  # n_si, face ids, error text

        def __init__(self, mesh_path):
            super().__init__()
            self._path = mesh_path

        def run(self):
            try:
                g, err = quick_genus(self._path), ""
            except Exception as e:   # noqa: BLE001
                g, err = 0, str(e)
            self.genus_ready.emit(int(g), err)
            try:
                n, ids = compute_self_intersections(self._path)
                self.si_ready.emit(int(n), ids, "")
            except Exception as e:   # noqa: BLE001
                self.si_ready.emit(0, [], str(e))

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
            self._prompt_open = False      # the "Discard this fix?" frame
            self._discard_ok = False       # the user agreed to discard on close
            self._cut_running = False
            self._cut_worker = None
            self._preview_workers = []
            self._tight_export = None
            self.mesh = None
            self.legend_lines = []
            # Space/Tab/Shift+Tab/C/P/Esc and Z/H/X are intercepted by an
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
            self._overlay_label = QtWidgets.QLabel("Checking the mesh…")
            self._overlay_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: white; background: transparent;")
            self._overlay_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._overlay_label.setWordWrap(True)
            self._overlay_hint = QtWidgets.QLabel("")
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
            self._overlay.show()

            # After-view banner "Fixed - N defect(s) left" (top of the 3D view)
            self._banner = QtWidgets.QLabel("", self.plotter_container)
            self._banner.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._banner.setStyleSheet(
                "background: rgba(28,110,70,235); color: white; font-size: 16px; "
                "font-weight: bold; border-radius: 8px; padding: 8px 18px;")
            self._banner.hide()

            # "Discard this fix?" prompt (an in-window frame, not a QMessageBox, so the
            # key filter stays in charge; its buttons never take focus)
            self._prompt = QtWidgets.QFrame(self.plotter_container)
            self._prompt.setStyleSheet(
                "background: rgba(30,30,30,240); border-radius: 10px;")
            pr_lay = QtWidgets.QVBoxLayout(self._prompt)
            pr_lay.setContentsMargins(16, 14, 16, 14)
            self._prompt_label = QtWidgets.QLabel(DISCARD_PROMPT)
            self._prompt_label.setWordWrap(True)
            self._prompt_label.setStyleSheet(
                "font-size: 13px; color: white; background: transparent;")
            pr_lay.addWidget(self._prompt_label)
            pr_row = QtWidgets.QHBoxLayout()
            self._btn_prompt_yes = QtWidgets.QPushButton("Discard fix")
            self._btn_prompt_no = QtWidgets.QPushButton("Go back")
            for _b in (self._btn_prompt_yes, self._btn_prompt_no):
                _b.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
                pr_row.addWidget(_b)
            pr_lay.addLayout(pr_row)
            self._btn_prompt_yes.clicked.connect(self._prompt_yes)
            self._btn_prompt_no.clicked.connect(self._prompt_no)
            self._prompt.hide()

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

            # --- the after-view (hidden until a fix has been computed) ---
            self._after = QtWidgets.QWidget()
            self._after.setStyleSheet("background: transparent;")
            al = QtWidgets.QVBoxLayout(self._after)
            al.setContentsMargins(0, 0, 0, 0)
            al.setSpacing(8)
            self._lbl_after = QtWidgets.QLabel("")
            self._lbl_after.setWordWrap(True)
            self._lbl_after.setStyleSheet(
                "font-size: 15px; font-weight: bold; color: #7fd6a0; "
                "background: transparent;")
            al.addWidget(self._lbl_after)
            self._lbl_after_note = QtWidgets.QLabel(AFTER_NOTE)
            self._lbl_after_note.setWordWrap(True)
            self._lbl_after_note.setStyleSheet(
                "color: #aaa; font-size: 11px; background: transparent;")
            al.addWidget(self._lbl_after_note)
            self._btn_keep = QtWidgets.QPushButton(KEEP_LABEL.replace("&", "&&"))
            self._btn_keep.setToolTip(KEEP_TOOLTIP)
            self._btn_keep.setStyleSheet("QPushButton { background-color: #1f8f5c; }"
                                         "QPushButton:disabled { background-color: #444; }")
            self._btn_keep.clicked.connect(self._keep)
            self._btn_undo = QtWidgets.QPushButton(UNDO_LABEL + "  [Esc]")
            self._btn_undo.setToolTip(UNDO_TOOLTIP)
            self._btn_undo.clicked.connect(self._undo)
            al.addWidget(self._btn_keep)
            al.addWidget(self._btn_undo)
            panel_layout.addWidget(self._after)
            self._after.hide()

            self._panel_status = QtWidgets.QLabel("Checking the mesh…")
            self._panel_status.setWordWrap(True)
            self._panel_status.setStyleSheet(
                "color: #ccc; font-size: 11px; background: transparent;")
            panel_layout.addWidget(self._panel_status)

            panel_layout.addStretch()

            self._btn_cut = QtWidgets.QPushButton("Apply fix  [C]")
            self._btn_cut.setEnabled(False)
            self._btn_cut.setToolTip("Computes the fix and shows you the result. "
                                     "Nothing is saved until you press Keep.")
            self._btn_cut.clicked.connect(self._request_cut)
            panel_layout.addWidget(self._btn_cut)

            self._btn_view = QtWidgets.QPushButton("Show: Before / After  [P]")
            self._btn_view.setCheckable(True)
            self._btn_view.setEnabled(False)
            self._btn_view.setToolTip(
                "Switch the 3D view between the mesh as it is now (Before) and "
                "what the selected option would produce (After). The camera "
                "stays where it is.")
            self._btn_view.clicked.connect(self._toggle_view)
            panel_layout.addWidget(self._btn_view)

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
            self.status_label = QtWidgets.QLabel("Checking the mesh…")
            self.statusBar().addWidget(self.status_label, 1)

            # Panel widgets must never take keyboard focus: a focused button
            # turns Space (option toggle) into a click on that button, and Apply
            # has no other guard. Clicks still work; keys go to the filter.
            for _b in (self._btn_cut, self._btn_zoom, self._btn_whole, self._btn_see,
                       self._btn_export, self._btn_close, self._btn_prev,
                       self._btn_next, self._btn_view, self._btn_keep,
                       self._btn_undo, *self._cards):
                _b.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

            self.plotter.set_background(BG_COLOR)

            # The window shows NOW; everything slow runs after the first paint
            # (mesh load on the GUI thread is VTK I/O and is quick; the pymeshlab
            # checks run in _PrepWorker; the loop finder in _TunnelWorker).
            QtCore.QTimer.singleShot(0, self._start_loading)

        # --- startup ---------------------------------------------------------
        def _start_loading(self):
            try:
                self.mesh = pv.read(self.mesh_path)
            except Exception as e:
                self._overlay.hide()
                self.status_label.setText(f"Error loading mesh: {e}")
                self._panel_status.setText(f"Error: {e}")
                self._btn_close.setEnabled(True)
                return
            self.legend_lines = _add_static_overlays(self.plotter, self.mesh,
                                                     self.mesh_path, include_si=False)
            self._refresh_legend()
            self.plotter.add_key_event('r', lambda: self.plotter.reset_camera())
            self.plotter.reset_camera()
            self._btn_whole.setEnabled(True)
            self._arrays = _mesh_to_arrays(self.mesh)
            self._prep = _PrepWorker(self.mesh_path)
            self._prep.genus_ready.connect(self._on_genus_ready)
            self._prep.si_ready.connect(self._on_si_ready)
            self._prep.start()

        def _on_genus_ready(self, genus, err):
            if err:
                self.legend_lines.append(f"Hole check: {err}")
                self._refresh_legend()
            self._genus = genus
            if genus <= 0:
                self._overlay.hide()
                self.status_label.setText("No see-through holes.")
                self._panel_status.setText("No see-through holes detected.")
                self._btn_close.setEnabled(True)
                return
            first = "Finding the defects..." if genus > 1 else "Finding the defect..."
            self.status_label.setText(first)
            self._panel_status.setText(first)
            self._overlay_label.setText(first)
            self._overlay_hint.setText(LOOP_FIND_HINT)
            pts, faces = self._arrays
            self._worker = _TunnelWorker(pts, faces, genus)
            self._worker.progress.connect(self._on_progress)
            self._worker.done.connect(self._on_done)
            self._worker.start()
            self._place_overlays()
            self._overlay.show()
            self._overlay.raise_()

        def _on_si_ready(self, n_si, face_ids, err):
            try:
                if err:
                    self.legend_lines.append(f"Self-intersection check: {err}")
                else:
                    self.legend_lines.append(
                        _add_si_overlay(self.plotter, self.mesh, n_si, face_ids))
            except Exception as e:
                self.legend_lines.append(f"Self-intersection check: {e}")
            self._refresh_legend()
            self.plotter.render()

        # --- keys ------------------------------------------------------------
        def eventFilter(self, obj, event):
            """Application-level key filter, active only while this window is the
            active window: Space = other option, Tab/Shift+Tab = next/previous
            defect, C = apply (computes the fix and shows it), P = before / after,
            Esc = Undo in the after-view (or "Go back" on the discard prompt),
            Z / H / X = zoom to defect / whole head / see-through (no modifiers).
            None clash with VTK or pyvista defaults (q e f p r w s 3 u b v C and
            the arrow / +/- keys) because a handled key is consumed HERE: this
            application-level filter sees it before the VTK widget does -- that
            is how P (VTK's "pick") is safe to use. P is swallowed even while the
            loops are still being found. Keep & Close has no key on purpose: saving
            needs a deliberate click. Auto-repeat is swallowed so holding a key
            cannot fire twice, and nothing but Esc does anything while a worker
            runs or the discard prompt shows."""
            if (event.type() == QtCore.QEvent.Type.KeyPress
                    and QtWidgets.QApplication.activeWindow() is self):
                key = event.key()
                Qt = QtCore.Qt
                camera_keys = (Qt.Key.Key_Z, Qt.Key.Key_H, Qt.Key.Key_X, Qt.Key.Key_P)
                handled = (Qt.Key.Key_Space, Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
                           Qt.Key.Key_C, Qt.Key.Key_Escape) + camera_keys
                if key in camera_keys and event.modifiers() != Qt.KeyboardModifier.NoModifier:
                    return super().eventFilter(obj, event)
                sel = self._selector
                if key in handled and (sel is not None or key == Qt.Key.Key_P):
                    if event.isAutoRepeat():
                        return True
                    if self._prompt_open:
                        if key == Qt.Key.Key_Escape:
                            self._prompt_no()
                        return True
                    if sel is None:
                        return True            # P while loading: swallowed
                    if sel.busy:
                        return True
                    if sel.mode == "after":
                        if key == Qt.Key.Key_Escape:
                            self._undo()
                        elif key == Qt.Key.Key_P:
                            self._toggle_view()
                        elif key == Qt.Key.Key_Z:
                            self._zoom_to_defect()
                        elif key == Qt.Key.Key_H:
                            self._whole_head()
                        elif key == Qt.Key.Key_X:
                            sel.toggle_see_through()
                        return True
                    if key == Qt.Key.Key_Space:
                        sel.toggle_active_loop()
                    elif key == Qt.Key.Key_Tab:
                        sel.next_handle()
                    elif key == Qt.Key.Key_Backtab:
                        sel.prev_handle()
                    elif key == Qt.Key.Key_C:
                        self._request_cut()
                    elif key == Qt.Key.Key_P:
                        self._toggle_view()
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
            if sel is None or sel.busy or self._prompt_open or sel.mode != "choose":
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

        def _toggle_view(self, *_):
            if (self._selector is not None and not self._selector.busy
                    and not self._prompt_open):
                self._selector.toggle_view()
            self._refresh_panel()

        def _refresh_panel(self):
            """Re-sync every panel widget from the selector (called on any change
            of selection / active defect / busy / mode / preview state)."""
            sel = self._selector
            if sel is None:
                return
            after = sel.mode == "after"
            locked = sel.busy or self._prompt_open
            n = len(sel.handles)
            self._decision.setVisible(not after)
            self._after.setVisible(after)
            self._btn_cut.setVisible(not after)
            self._btn_export.setVisible(not after)
            self._lbl_defect.setText(sel.defect_title())
            for i, spec in enumerate(sel.cards()):
                self._cards[i].set_state(
                    spec["title"], spec["subtitle"], spec["selected"],
                    spec["enabled"] and not locked, spec["tooltip"],
                    "red" if spec["selected"] else "blue",
                    spec.get("status"), spec.get("preview_state"))
            self._btn_prev.setEnabled(n > 1 and not locked)
            self._btn_next.setEnabled(n > 1 and not locked)
            self._lbl_rule.setVisible(sel.is_classified())
            notes = sel.notes()
            self._lbl_note.setText("\n\n".join(notes))
            self._lbl_note.setVisible(bool(notes))
            self._btn_cut.setEnabled(not locked and sel.can_apply())
            self._btn_zoom.setEnabled(not sel.busy)
            self._btn_whole.setEnabled(not sel.busy)
            self._btn_see.setEnabled(not sel.busy)
            self._btn_see.blockSignals(True)
            self._btn_see.setChecked(sel.see_through)
            self._btn_see.blockSignals(False)
            self._btn_export.setEnabled(not sel.busy)
            self._btn_close.setEnabled(not sel.busy)
            # Before / After toggle (previews in the choose view, the fix in the after-view)
            self._btn_view.setVisible(after or sel.previews is not None)
            self._btn_view.setEnabled(sel.can_toggle_view() and not self._prompt_open)
            self._btn_view.blockSignals(True)
            self._btn_view.setChecked(sel.view == "after")
            self._btn_view.setText("Show: " + ("After" if sel.view == "after" else "Before")
                                   + "  [P]")
            self._btn_view.blockSignals(False)
            # After-view
            if after and sel.result_genus is not None:
                self._lbl_after.setText(fixed_banner(sel.result_genus))
                legend = sel.new_surface_legend()
                self._lbl_after_note.setText(
                    (AFTER_LEFT_NOTE if sel.result_genus > 0 else AFTER_NOTE)
                    + (("\n" + legend) if legend else ""))
                self._banner.setText(fixed_banner(sel.result_genus)
                                     + ("" if sel.view == "after" else "   (showing Before)"))
                self._banner.adjustSize()
            self._banner.setVisible(after)
            self._btn_keep.setEnabled(after and not locked)
            self._btn_undo.setEnabled(after and not locked)
            self._place_overlays()

        def _selector_status(self, text, color):
            self._panel_status.setText(text)
            self._panel_status.setStyleSheet(
                f"color: {color}; font-size: 11px; background: transparent;")

        # --- apply / keep / undo ------------------------------------------
        def _request_cut(self, *_):
            if self._selector is None or self._selector.busy or self._prompt_open:
                return
            self._selector.request_apply()

        def _keep(self, *_):
            sel = self._selector
            if sel is None or sel.busy or self._prompt_open or self._cut_running:
                return
            sel.keep()

        def _undo(self, *_):
            sel = self._selector
            if sel is None or sel.busy or self._prompt_open or self._cut_running:
                return
            sel.undo()

        # --- discard prompt -----------------------------------------------
        def _show_prompt(self):
            self._prompt_open = True
            self._refresh_panel()
            self._place_overlays()
            self._prompt.show()
            self._prompt.raise_()

        def _hide_prompt(self):
            self._prompt_open = False
            self._prompt.hide()
            self._refresh_panel()

        def _prompt_yes(self):
            self._hide_prompt()
            self._discard_ok = True
            self.close()

        def _prompt_no(self):
            self._hide_prompt()

        # --- background workers (cut / save / previews) -------------------
        def _set_cut_busy(self, busy):
            """Selector busy hook: lock the panel and show the wait overlay while
            a cut or a save runs; restore afterwards."""
            if busy:
                sel = self._selector
                saving = sel is not None and sel.mode == "after"
                self._overlay_label.setText("Saving the fixed mesh..." if saving
                                            else "Fixing the mesh...")
                self._overlay_hint.setText("" if saving else CUT_HINT)
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
            if worker is not None and callable(finish):
                finish(worker.error)

        def _run_preview_job(self, run_fn):
            w = _PreviewWorker(run_fn)
            w.updated.connect(self._on_preview_updated)
            w.finished.connect(lambda w=w: self._preview_worker_finished(w))
            self._preview_workers.append(w)
            w.start()

        def _preview_worker_finished(self, w):
            if w in self._preview_workers:
                self._preview_workers.remove(w)

        def _on_preview_updated(self, key):
            if self._selector is not None:
                self._selector.on_preview_update(key)

        def _kept(self):
            """on_success hook: Keep saved the fix; close without asking."""
            self._overlay_label.setText("Saved. Closing and re-checking the mesh...")
            self._overlay_hint.setText("")
            self._overlay.show()
            self._overlay.raise_()
            self.close()

        def _do_export(self):
            path = (self._selector.export() if self._selector is not None
                    else self._tight_export)
            if path:
                self._selector_status(f"Loops exported to:\n{path}", "#ccc")
                self.status_label.setText(f"Exported loops: {path}")
            else:
                self._selector_status("Loop export failed - see the console.", "#e6a09f")

        def closeEvent(self, ev):
            sel = self._selector
            decision = close_decision(
                self._cut_running,
                sel is not None and sel.has_unkept_result(),
                self._discard_ok)
            if decision == "wait":
                # Closing mid-write could cost the user the mesh; the window
                # closes by itself as soon as the fix finishes.
                ev.ignore()
                self._selector_status(
                    "The mesh is being worked on - please wait. This window "
                    "closes by itself when it is done.", COLOR_PALE_YELLOW)
                return
            if decision == "ask":
                ev.ignore()
                self._show_prompt()
                return
            if sel is not None and sel.previews is not None:
                sel.previews.shutdown()     # also terminates a preview cut in the child process
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
            pw, ph = min(520, max(300, r.width() - 40)), 130
            self._prompt.setGeometry(
                (r.width() - pw) // 2, (r.height() - ph) // 2, pw, ph)
            if self._banner.isVisible() or self._banner.text():
                self._banner.adjustSize()
                bw = min(max(self._banner.width(), 260), max(260, r.width() - 40))
                self._banner.setGeometry((r.width() - bw) // 2, 14, bw,
                                         self._banner.height())
            self._overlay.raise_()
            self._banner.raise_()
            if self._prompt_open:
                self._prompt.raise_()

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            if hasattr(self, '_prompt'):
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
                                       on_success=self._kept,
                                       busy_cb=self._set_cut_busy,
                                       genus=self._genus,
                                       on_change=self._refresh_panel,
                                       status_cb=self._selector_status,
                                       job_runner=self._run_cut_job,
                                       preview_runner=self._run_preview_job,
                                       preview_process=True)
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
                sel.show_defect()
                self.plotter.setFocus()
                sel.start_previews()       # rings first, previews fill in after
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
