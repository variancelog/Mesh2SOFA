"""Stage 2 (clear UI) tests for core/mesh_problem_viewer.py -- all headless:
the text/label mapping, k-of-g / UNKNOWN / unavailable-B wording, the camera
framing maths, progress-message translation, and the TunnelSelector driven with
a mock plotter (card selection, defect navigation, off-thread cut runner hook).
No Qt / GL is needed; the real window is exercised by plans/tunnel-loop/viewer_smoke.py.
"""
import os
import re
from unittest import mock

import numpy as np
import pytest
import pyvista as pv

import mesh_problem_viewer as mpv
import project_store as ps
from tunnel_loop_extractor import mesh_genus
from tunnel_loop_locator import select_cut_loop
from _mesh_helpers import confirm_all


def test_technical_names_only_in_tooltips():
    visible = [mpv.QUESTION_TEXT, mpv.RULE_OF_THUMB, mpv.UNKNOWN_WARNING,
               mpv.SINGLE_UNKNOWN_WARNING, mpv.TIGHT_MODE_NOTE, mpv.NO_LOOPS_NOTE,
               mpv.CANVAS_HINT, mpv.UNAVAILABLE_TEXT, mpv.LOOP_FIND_HINT, mpv.CUT_HINT]
    visible += list(mpv.OPTION_TITLE.values()) + list(mpv.OPTION_SUBTITLE.values())
    for text in visible:
        assert not re.search(r"\b(tunnel|handle|genus|linking)\b", text, re.I), text
    assert "TUNNEL" in mpv.OPTION_TOOLTIP["TUNNEL"]
    assert "HANDLE" in mpv.OPTION_TOOLTIP["HANDLE"]


# ---------------------------------------------------------------------------
# Honest text: k of g, UNKNOWN, unavailable B
# ---------------------------------------------------------------------------
def test_remaining_defects_note():
    assert mpv.remaining_defects_note(2, 3)      # only when some defects are not shown
    assert mpv.remaining_defects_note(3, 3) is None
    assert mpv.remaining_defects_note(1, None) is None


def test_cards_classified_pair():
    cards = mpv.option_cards(["TUNNEL", "HANDLE"], [], 0)
    assert [c["title"] for c in cards] == ["Fill the hole", "Remove the bridge"]
    assert [c["selected"] for c in cards] == [True, False]
    assert all(c["enabled"] for c in cards)
    assert "TUNNEL" in cards[0]["tooltip"] and "HANDLE" in cards[1]["tooltip"]


def test_cards_unknown_pair_are_numbered_with_warning():
    cards = mpv.option_cards(["UNKNOWN", "UNKNOWN"], [], 0)
    assert [c["title"] for c in cards] == ["Option 1", "Option 2"]
    assert all(c["enabled"] for c in cards)
    assert cards[0]["selected"]          # A still pre-selected
    notes = mpv.defect_notes(["UNKNOWN", "UNKNOWN"], 1, 1)
    assert notes == [mpv.UNKNOWN_WARNING]
    assert "look closely" in notes[0]


def test_cards_unavailable_b_second_card_disabled():
    # B failed the locate-time check: A has no label (the classifier needs both
    # loops), shown as UNKNOWN-for-A: "Option 1"; card 2 is disabled.
    cards = mpv.option_cards(["UNKNOWN"], ["cutting it gives genus 1->1"], 0)
    assert cards[0]["title"] == "Option 1" and cards[0]["enabled"]
    assert cards[1]["enabled"] is False
    assert cards[1]["subtitle"] == "Not available for this defect"
    assert "genus 1->1" in cards[1]["tooltip"]
    assert not cards[1]["selected"]
    assert mpv.defect_notes(["UNKNOWN"], 1, 1) == [mpv.SINGLE_UNKNOWN_WARNING]


def test_notes_combine_unknown_and_k_of_g():
    notes = mpv.defect_notes(["UNKNOWN", "UNKNOWN"], 2, 3)
    assert notes[0] == mpv.UNKNOWN_WARNING
    assert notes[1].startswith("Showing 2 of 3 defects.")
    assert mpv.defect_notes(["TUNNEL", "HANDLE"], 3, 3) == []


# ---------------------------------------------------------------------------
# Progress messages
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Building tree-cotree homology...", "Finding the defect..."),
    ("Tightening cut loop 2/3...", "Finding defect 2 of 3..."),
    ("Orienting surface normals (parity probe)...",
     "Working out both ways to fix it..."),
    ("Computing dual loop 1/2...", "Working out both ways to fix defect 1 of 2..."),
    ("Dual loop retry on loose progenitor (40 verts)...",
     "Working out both ways to fix it..."),
    ("Classifying handle/tunnel 2/2...",
     "Checking which way is which (defect 2 of 2)..."),
    ("something unexpected", "Working..."),
])
def test_friendly_progress(raw, expected):
    assert mpv.friendly_progress(raw) == expected


def test_friendly_progress_never_leaks_jargon():
    for raw in ("Building tree-cotree homology...", "Classifying handle/tunnel 1/1...",
                "Orienting surface normals (parity probe)...",
                "Dual loop retry on loose progenitor (9 verts)..."):
        assert not re.search(r"tunnel|handle|cotree|probe|progenitor",
                             mpv.friendly_progress(raw), re.I)


# ---------------------------------------------------------------------------
# Camera framing
# ---------------------------------------------------------------------------
def _ring(center, radius, n=24, axis=2):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    pts = np.zeros((n, 3))
    a, b = [i for i in range(3) if i != axis]
    pts[:, a] = np.cos(t) * radius
    pts[:, b] = np.sin(t) * radius
    return pts + np.asarray(center, float)


def test_camera_focal_is_pair_centroid_and_looks_from_outside():
    c = np.array([100.0, 0.0, 0.0])
    r1, r2 = _ring(c + [0, 0, 5], 8), _ring(c - [0, 0, 5], 8)
    pos, focal, up, dist = mpv.defect_camera_pose([r1, r2], [0, 0, 0], 250.0)
    assert np.allclose(focal, c, atol=1e-9)
    # camera sits further out along +x (away from the mesh centre)
    assert pos[0] > focal[0] and abs(pos[1]) < 1e-9 and abs(pos[2]) < 1e-9
    assert np.isclose(np.linalg.norm(pos - focal), dist)
    assert abs(float(np.dot(up, (pos - focal) / dist))) < 0.95   # view-up not parallel


def test_camera_distance_is_a_few_spans_within_clamp():
    diag = 300.0
    big = _ring([100, 0, 0], 20.0)          # span ~ 57 -> 3 x 57 = 170 (inside the clamp)
    _p, _f, _u, d = mpv.defect_camera_pose([big], [0, 0, 0], diag)
    span = np.linalg.norm(big.max(0) - big.min(0))
    assert np.isclose(d, mpv.DEFECT_ZOOM_SPANS * span)
    assert mpv.DEFECT_ZOOM_MIN_FRAC * diag <= d <= mpv.DEFECT_ZOOM_MAX_FRAC * diag


def test_camera_distance_clamped_for_tiny_and_huge_loops():
    diag = 300.0
    tiny = _ring([100, 0, 0], 0.5)
    _p, _f, _u, d_tiny = mpv.defect_camera_pose([tiny], [0, 0, 0], diag)
    assert np.isclose(d_tiny, mpv.DEFECT_ZOOM_MIN_FRAC * diag)
    huge = _ring([100, 0, 0], 600.0)
    _p, _f, _u, d_huge = mpv.defect_camera_pose([huge], [0, 0, 0], diag)
    assert np.isclose(d_huge, mpv.DEFECT_ZOOM_MAX_FRAC * diag)


def test_camera_degenerate_direction_and_polar_view_up():
    ring = _ring([0, 0, 0], 3.0)            # focal at the mesh centre exactly
    pos, focal, up, dist = mpv.defect_camera_pose([ring], [0, 0, 0], 100.0)
    assert np.isfinite(pos).all()
    top = _ring([0, 0, 90], 3.0)            # straight above the centre
    pos, focal, up, dist = mpv.defect_camera_pose([top], [0, 0, 0], 100.0)
    assert abs(float(np.dot(up, (pos - focal) / dist))) < 0.95


# ---------------------------------------------------------------------------
# TunnelSelector driven with a mock plotter
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def torus_pair(torus):
    pts, faces = torus
    pair = select_cut_loop(pts, faces, 1)[0]
    assert pair["labels"]["cut"] in ("TUNNEL", "UNKNOWN")
    return pair


def _two_defect_selector(torus, pair, **kw):
    """The same torus pair twice: enough to exercise navigation."""
    pts, faces = torus
    return mpv.TunnelSelector(mock.MagicMock(), pts, [pair, pair], faces=faces, **kw)


def test_select_option_and_cards_follow_selection(torus, torus_pair):
    pts, faces = torus
    changes = []
    sel = mpv.TunnelSelector(mock.MagicMock(), pts, [torus_pair], faces=faces,
                             genus=1, on_change=lambda: changes.append(1))
    assert sel.handles[0]["sel"] == 0
    first = sel.cards()
    assert first[0]["selected"] and not first[1]["selected"]
    sel.select_option(1)
    assert sel.handles[0]["sel"] == 1 and sel.cards()[1]["selected"]
    sel.select_option(1)                 # no change -> no extra notification
    sel.toggle_active_loop()             # Space
    assert sel.handles[0]["sel"] == 0
    assert len(changes) == 2
    sel.select_option(5)                 # out of range: ignored
    assert sel.handles[0]["sel"] == 0


def test_unavailable_card_cannot_be_selected(torus):
    pts, faces = torus
    loop = np.asarray(faces[0], dtype=np.int64)
    pair = {"cut": loop, "avoid": None, "A": loop, "B": None,
            "B_unavailable": np.asarray(faces[1], dtype=np.int64),
            "B_reason": "test reason",
            "labels": {"cut": "UNKNOWN", "avoid": None}}
    sel = mpv.TunnelSelector(mock.MagicMock(), pts, [pair], faces=faces, genus=1)
    cards = sel.cards()
    assert cards[1]["enabled"] is False
    sel.select_option(1)
    assert sel.handles[0]["sel"] == 0
    assert sel.notes() == [mpv.SINGLE_UNKNOWN_WARNING]
    assert not sel.is_classified()


def test_defect_navigation_zooms_and_notifies(torus, torus_pair):
    changes = []
    sel = _two_defect_selector(torus, torus_pair, genus=3,
                               on_change=lambda: changes.append(sel.active))
    assert sel.defect_title() == "Defect 1 of 2"
    sel.next_handle()                                    # Tab / Next
    assert sel.active == 1 and sel.defect_title() == "Defect 2 of 2"
    # the camera was re-framed on the new defect
    assert sel.pl.reset_camera_clipping_range.called
    pos, focal, up = sel.pl.camera_position
    h = sel.handles[1]
    expected_focal = np.mean([sel.pts[lp].mean(0) for lp in h["loops"]], axis=0)
    assert np.allclose(focal, expected_focal)
    sel.prev_handle()                                    # Shift+Tab / Prev
    assert sel.active == 0 and changes == [1, 0]
    assert sel.notes()[-1] == ("Showing 2 of 3 defects. The rest will be found "
                               "after these are fixed.")


def test_single_defect_navigation_is_a_noop(torus, torus_pair):
    pts, faces = torus
    sel = mpv.TunnelSelector(mock.MagicMock(), pts, [torus_pair], faces=faces)
    sel.next_handle()
    sel.prev_handle()
    assert sel.active == 0


def test_see_through_sets_surface_opacity(torus, torus_pair):
    pts, faces = torus
    pl = mock.MagicMock()
    actor = mock.MagicMock()
    pl.actors = {"surface": actor}
    sel = mpv.TunnelSelector(pl, pts, [torus_pair], faces=faces)
    sel.toggle_see_through()
    actor.GetProperty.return_value.SetOpacity.assert_called_with(mpv.SEE_THROUGH_OPACITY)
    assert sel.see_through
    sel.toggle_see_through()
    actor.GetProperty.return_value.SetOpacity.assert_called_with(mpv.MESH_OPACITY)
    assert not sel.see_through


def test_ring_labels_use_plain_names_and_old_jargon_is_gone(torus, torus_pair):
    pts, faces = torus
    pl = mock.MagicMock()
    mpv.TunnelSelector(pl, pts, [torus_pair], faces=faces)
    texts = []
    for c in pl.add_point_labels.call_args_list:
        texts += [str(t) for t in c.args[1]]
    assert texts, "ring labels were drawn"
    for t in texts:
        assert not re.search(r"Cut\+Patch|Avoid|HANDLE|TUNNEL", t), t
    canvas = [c.args[0] for c in pl.add_text.call_args_list
              if c.kwargs.get("name") == "kb_hint"]
    assert canvas and canvas[-1] == mpv.CANVAS_HINT


# ---------------------------------------------------------------------------
# The cut runs through job_runner (the window's QThread) -- headless check
# ---------------------------------------------------------------------------
def _write_torus_ply(path, pts, faces):
    cells = np.hstack([np.full((len(faces), 1), 3, dtype=np.int64), faces]).ravel()
    pv.PolyData(pts, cells).save(path)


def test_cut_goes_through_job_runner_and_finishes_on_callback(tmp_path, torus, torus_pair):
    pts, faces = torus
    mesh = str(tmp_path / "aligned_head.ply")
    _write_torus_ply(mesh, pts, faces)
    before = open(mesh, "rb").read()
    queued, events = {}, {"success": 0, "busy": []}

    def runner(job, finish):          # stands in for _CutWorker: deferred, not inline
        queued["job"], queued["finish"] = job, finish

    sel = mpv.TunnelSelector(
        mock.MagicMock(), pts, [torus_pair], mesh_path=mesh, faces=faces,
        on_success=lambda: events.__setitem__("success", events["success"] + 1),
        busy_cb=lambda b: events["busy"].append(b), job_runner=runner)
    confirm_all(sel)
    sel.apply_cut_and_patch()
    # nothing has run yet: the GUI thread returned immediately, panel is busy
    assert sel.busy and events["busy"] == [True]
    assert open(mesh, "rb").read() == before and "job" in queued
    # a second press while the cut is queued/running is ignored
    confirm_all(sel)
    sel.apply_cut_and_patch()
    assert events["busy"] == [True]

    queued["job"]()                    # "worker thread" does the cut
    assert sel.mode == "choose"        # the after-view starts only from finish()
    queued["finish"](None)             # GUI-thread tail: the result is shown, NOT saved
    assert sel.mode == "after" and not sel.busy and events["success"] == 0
    assert open(mesh, "rb").read() == before
    assert not os.path.exists(ps.backup_path_for(mesh))
    assert not os.path.exists(tmp_path / "cutpatch_report.json")

    queued.clear()
    sel.keep()                         # saving also goes through the runner
    assert sel.busy and open(mesh, "rb").read() == before and "job" in queued
    sel.keep()                         # Keep is impossible while a worker runs
    sel.undo()
    assert sel.mode == "after"
    queued["job"]()
    assert events["success"] == 0
    queued["finish"](None)
    assert events["success"] == 1
    new = pv.read(mesh)
    assert mesh_genus(np.asarray(new.points), new.faces.reshape(-1, 4)[:, 1:]) == 0
    assert os.path.exists(ps.backup_path_for(mesh))
    assert os.path.exists(tmp_path / "cutpatch_report.json")


def test_cut_failure_reported_via_finish_leaves_mesh_alone(tmp_path, torus, torus_pair):
    pts, faces = torus
    mesh = str(tmp_path / "aligned_head.ply")
    _write_torus_ply(mesh, pts, faces)
    before = open(mesh, "rb").read()
    queued, statuses, busy = {}, [], []
    sel = mpv.TunnelSelector(
        mock.MagicMock(), pts, [torus_pair], mesh_path=mesh, faces=faces,
        busy_cb=busy.append, status_cb=lambda t, c: statuses.append(t),
        job_runner=lambda job, finish: queued.update(job=job, finish=finish))
    sel.handles[0]["loops"][0] = np.asarray(faces[0], dtype=np.int64)  # will not sever
    confirm_all(sel)
    sel.apply_cut_and_patch()
    err = None
    try:
        queued["job"]()
    except Exception as e:
        err = e
    assert err is not None
    queued["finish"](err)
    assert busy == [True, False] and not sel.busy and sel.mode == "choose"
    assert open(mesh, "rb").read() == before
    assert "unchanged" in statuses[-1] and "NOT applied" in statuses[-1]
    assert sorted(os.listdir(tmp_path))[0].startswith("aligned_head")
    assert not os.path.exists(ps.backup_path_for(mesh))
