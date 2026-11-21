"""Stage 3 (see the result) tests for core/mesh_problem_viewer.py -- all headless:
the after-view state machine (Apply -> result -> Keep writes / Undo writes
nothing / close with an unkept fix asks), the background PreviewCache (caching,
failures, stop between options), the card states, "single-defect Apply reuses the
cached verified preview", the multi-defect combined result, the Before/After
display, and the PREVIEWS_ENABLED switch. The real window (focus, Esc, the
discard frame, P vs VTK) is exercised by plans/tunnel-loop/viewer_smoke.py.
"""
import os
import re
from unittest import mock

import numpy as np
import pytest
import pyvista as pv

import mesh_problem_viewer as mpv
import project_store as ps
import tunnel_loop_extractor as tle
from tunnel_loop_extractor import mesh_genus
from tunnel_loop_locator import select_cut_loop, locate_cut_loops
from _mesh_helpers import confirm_all, make_selector, _write_ply, _inline_runner  # noqa: F401


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
# torus / pair / dtorus_pairs are session fixtures in conftest.py; make_selector and
# _write_ply live in _mesh_helpers.py.


def spy_cut_and_patch(monkeypatch, fail_for=None):
    """Count real cut_and_patch calls; optionally raise for loops in fail_for."""
    calls = []
    real = tle.cut_and_patch

    def spy(pts, faces, loop, **kw):
        calls.append(tuple(int(x) for x in loop[:3]))
        if fail_for is not None and fail_for(loop):
            raise ValueError("cut_and_patch: loop does not sever a handle (test)")
        return real(pts, faces, loop, **kw)

    monkeypatch.setattr(tle, "cut_and_patch", spy)
    return calls


def test_stage3_visible_text_has_no_jargon():
    texts = [mpv.PREVIEW_PENDING_TEXT, mpv.PREVIEW_READY_TEXT,
             mpv.PREVIEW_UNAVAILABLE_PREFIX, mpv.KEEP_LABEL, mpv.UNDO_LABEL,
             mpv.KEEP_TOOLTIP, mpv.UNDO_TOOLTIP, mpv.DISCARD_PROMPT, mpv.AFTER_NOTE,
             mpv.AFTER_LEFT_NOTE, mpv.fixed_banner(2)]
    for msg in ("cut_and_patch: loop does not sever a handle (genus 1->1)",
                "cut_and_patch: post-patch check failed: capped genus 1 != expected 0",
                "post-patch check failed: mesh not watertight (2 open boundary loop(s) remain)",
                "post-patch check failed: 2 components (a piece was detached)",
                "post-patch check failed: face count dropped 10 -> 5",
                "deleting the loop band removed all faces"):
        texts.append(mpv.preview_status_text("failed", msg))
    for t in texts:
        assert not re.search(r"\b(tunnel|handle|genus|linking|loop|cut_and_patch)\b", t,
                             re.I), t
    assert mpv.preview_status_text("running") == mpv.PREVIEW_PENDING_TEXT


def test_close_decision_table():
    cd = mpv.close_decision
    assert cd(True, True, False) == "wait"          # a cut/save is running
    assert cd(False, True, False) == "ask"          # unkept fix -> ask first
    assert cd(False, True, True) == "close"         # user agreed to discard
    assert cd(False, False, False) == "close"       # always closes: no deferral


# ---------------------------------------------------------------------------
# PreviewCache
# ---------------------------------------------------------------------------
def test_cache_computes_each_option_once_and_never_touches_inputs(torus, pair, monkeypatch):
    pts, faces = torus
    p0, f0 = pts.copy(), faces.copy()
    calls = spy_cut_and_patch(monkeypatch)
    cache = mpv.PreviewCache(pts, faces, {(0, 0): pair["cut"], (0, 1): pair["avoid"]})
    assert cache.state((0, 0))[0] == "pending" and not cache.all_done()
    updates = []
    cache.run(on_update=updates.append)
    assert updates == [(0, 0), (0, 1)] and cache.all_done() and len(calls) == 2
    for key in cache.keys():
        assert cache.state(key) == ("ready", None)
        rp, rf = cache.result(key)
        assert mesh_genus(rp, rf) == 0
        assert not rp.flags.writeable and not rf.flags.writeable   # shared read-only
    cache.run()                                   # nothing pending: no recompute
    cache.get_or_compute((0, 0))
    assert len(calls) == 2
    assert np.array_equal(pts, p0) and np.array_equal(faces, f0), "inputs modified"
    assert cache.summary() == {"0.0": "ready", "0.1": "ready"}


def test_cache_failure_is_cached_with_reason(torus, pair, monkeypatch):
    pts, faces = torus
    bad = np.asarray(faces[0], dtype=np.int64)        # one triangle: cannot sever
    cache = mpv.PreviewCache(pts, faces, {(0, 0): bad, (0, 1): pair["avoid"]})
    cache.run()
    state, reason = cache.state((0, 0))
    assert state == "failed" and "does not sever" in reason
    assert cache.result((0, 0)) is None and cache.state((0, 1))[0] == "ready"
    with pytest.raises(ValueError, match="does not sever"):
        cache.get_or_compute((0, 0))


def test_cache_stops_between_options_and_resumes(torus, pair):
    pts, faces = torus
    cache = mpv.PreviewCache(pts, faces, {(0, 0): pair["cut"], (0, 1): pair["avoid"]})
    seen = []

    def on_update(key):
        seen.append(key)
        cache.stop()                                  # e.g. the window is closing
    cache.run(on_update=on_update)
    assert seen == [(0, 0)] and cache.pending_keys() == [(0, 1)]
    assert not cache.is_running()
    cache.run()                                       # still stopped: does nothing
    assert cache.pending_keys() == [(0, 1)]
    cache.resume()
    cache.run()
    assert cache.all_done()


# ---------------------------------------------------------------------------
# cards, unavailable options
# ---------------------------------------------------------------------------
def test_cards_show_preview_states(tmp_path, torus, pair):
    sel, mesh = make_selector(tmp_path, torus, pair)
    st = [c["status"] for c in sel.cards()]
    assert st == [mpv.PREVIEW_PENDING_TEXT] * 2
    sel.start_previews()                              # inline: runs both previews
    cards = sel.cards()
    assert [c["status"] for c in cards] == ["Preview ready"] * 2
    assert all(c["enabled"] for c in cards)


def test_unavailable_option_is_greyed_unselectable_and_switched_away_from(
        tmp_path, torus, pair, monkeypatch):
    avoid = np.asarray(pair["avoid"])
    spy_cut_and_patch(monkeypatch,
                      fail_for=lambda lp: np.array_equal(np.asarray(lp), avoid))
    sel, mesh = make_selector(tmp_path, torus, pair)
    sel.start_previews()
    cards = sel.cards()
    assert cards[0]["enabled"] and cards[0]["status"] == "Preview ready"
    assert not cards[1]["enabled"] and not cards[1]["selected"]
    assert cards[1]["status"].startswith("Not available — ")
    assert "does not sever" in cards[1]["tooltip"]         # technical reason on hover
    sel.select_option(1)                                   # refused
    assert sel.handles[0]["sel"] == 0
    sel.toggle_active_loop()                               # Space: refused too
    assert sel.handles[0]["sel"] == 0
    assert sel.selected_usable()


def test_selected_option_failing_moves_selection_and_all_failed_blocks_apply(
        tmp_path, torus, pair, monkeypatch):
    cut = np.asarray(pair["cut"])
    spy_cut_and_patch(monkeypatch,
                      fail_for=lambda lp: np.array_equal(np.asarray(lp), cut))
    sel, mesh = make_selector(tmp_path, torus, pair)
    statuses = []
    sel.status_cb = lambda t, c: statuses.append(t)
    sel.start_previews()
    assert sel.handles[0]["sel"] == 1, "moved off the option that failed"
    assert any("isn't available" in t for t in statuses)
    # now make the other one unusable as well: nothing can be applied
    sel.previews._entries[(0, 1)].update(state="failed", reason="test")
    assert not sel.can_apply()
    before = open(mesh, "rb").read()
    confirm_all(sel)
    sel.request_apply()
    assert sel.mode == "choose" and open(mesh, "rb").read() == before
    assert any("Defect 1 can't be fixed inside the app" in n for n in sel.notes())


# ---------------------------------------------------------------------------
# single defect: Apply uses the cached preview; what you saw is what is saved
# ---------------------------------------------------------------------------
def test_single_defect_apply_reuses_cached_preview_and_saves_exactly_it(
        tmp_path, torus, pair, monkeypatch):
    calls = spy_cut_and_patch(monkeypatch)
    sel, mesh = make_selector(tmp_path, torus, pair)
    sel.start_previews()
    assert len(calls) == 2
    for li in (0, 1):                      # whichever option is selected
        d = tmp_path / f"o{li}"
        d.mkdir()
        sel2, mesh2 = make_selector(d, torus, pair)
        sel2.start_previews()
        n = len(calls)
        sel2.select_option(li)
        preview = sel2.previews.result((0, li))
        confirm_all(sel2)
        sel2.request_apply()
        assert len(calls) == n, "Apply must not recompute a ready preview"
        assert sel2.mode == "after" and sel2.result[0] is preview[0] \
            and sel2.result[1] is preview[1], "the result IS the cached preview"
        assert sel2.result_genus == 0
        before = open(mesh2, "rb").read()
        sel2.keep()
        saved = pv.read(mesh2)
        sfaces = saved.faces.reshape(-1, 4)[:, 1:]
        assert np.array_equal(np.sort(sfaces, axis=1), np.sort(preview[1], axis=1)) \
            or np.array_equal(sfaces, preview[1])
        assert np.allclose(np.asarray(saved.points), preview[0], atol=1e-3)
        assert open(ps.backup_path_for(mesh2), "rb").read() == before


def test_apply_before_preview_ready_computes_once_and_fills_the_cache(
        tmp_path, torus, pair, monkeypatch):
    calls = spy_cut_and_patch(monkeypatch)
    sel, mesh = make_selector(tmp_path, torus, pair)      # previews never started
    confirm_all(sel)
    sel.request_apply()
    assert sel.mode == "after" and len(calls) == 1
    assert sel.previews.state((0, 0))[0] == "ready"
    assert sel.result[0] is sel.previews.result((0, 0))[0]
    assert sel.previews._stop.is_set(), "pending previews are stopped while applying"


def test_undo_restarts_stopped_previews(tmp_path, torus, pair, monkeypatch):
    calls = spy_cut_and_patch(monkeypatch)
    sel, mesh = make_selector(tmp_path, torus, pair)
    confirm_all(sel)
    sel.request_apply()                                   # computes (0,0), stops the rest
    assert sel.previews.pending_keys() == [(0, 1)]
    sel.undo()                                            # back to choosing: previews resume
    assert sel.previews.all_done() and len(calls) == 2


def test_apply_failure_marks_option_unavailable_and_changes_nothing(
        tmp_path, torus, pair, monkeypatch):
    cut = np.asarray(pair["cut"])
    spy_cut_and_patch(monkeypatch,
                      fail_for=lambda lp: np.array_equal(np.asarray(lp), cut))
    sel, mesh = make_selector(tmp_path, torus, pair)
    before = open(mesh, "rb").read()
    confirm_all(sel)
    sel.request_apply()
    assert sel.mode == "choose" and not sel.busy
    assert open(mesh, "rb").read() == before
    assert sel.previews.state((0, 0))[0] == "failed"
    assert sel.handles[0]["sel"] == 1 and sel.selected_usable()
    assert not sel.handles[0]["confirmed"]       # the selection moved: confirm again


# ---------------------------------------------------------------------------
# several defects: Apply cuts the chosen set together; the after-view is the real result
# ---------------------------------------------------------------------------
def test_multi_defect_apply_runs_cut_and_patch_loops(tmp_path, dtorus_pairs, monkeypatch):
    pts, faces, pairs = dtorus_pairs
    mesh = str(tmp_path / "aligned_head.ply")
    _write_ply(mesh, pts, faces)
    real = tle.cut_and_patch_loops
    seen = []

    def spy(p, f, loops, **kw):
        seen.append(len(loops))
        return real(p, f, loops, **kw)
    monkeypatch.setattr(tle, "cut_and_patch_loops", spy)
    sel = mpv.TunnelSelector(mock.MagicMock(), pts, pairs, mesh_path=mesh, faces=faces,
                             genus=2, previews=True)
    sel.previews.run()                                   # per-defect single-loop previews
    assert all(sel.previews.state(k)[0] == "ready" for k in sel.previews.keys())
    assert all(mesh_genus(*sel.previews.result(k)) == 1 for k in sel.previews.keys()), \
        "a single-loop preview of a genus-2 mesh still has one defect left"
    before = open(mesh, "rb").read()
    confirm_all(sel)
    sel.request_apply()
    assert seen == [2], "combined cut_and_patch_loops, not a cached single preview"
    assert sel.mode == "after" and sel.result_genus == 0
    assert mpv.fixed_banner(sel.result_genus).endswith("0 defects left")
    assert open(mesh, "rb").read() == before
    sel.keep()
    new = pv.read(mesh)
    assert mesh_genus(np.asarray(new.points), new.faces.reshape(-1, 4)[:, 1:]) == 0


# ---------------------------------------------------------------------------
# Before / After display
# ---------------------------------------------------------------------------
def _actor_pl():
    pl = mock.MagicMock()
    pl.actors = {n: mock.MagicMock() for n in
                 ("surface", "problem_edges", "sel_loop_0_0", "sel_loop_0_1",
                  "lbl_0_0", "lbl_0_0_leader_0")}
    return pl


def test_p_toggles_between_original_and_cached_preview_without_moving_the_camera(
        tmp_path, torus, pair):
    pts, faces = torus
    mesh = str(tmp_path / "aligned_head.ply")
    _write_ply(mesh, pts, faces)
    pl = _actor_pl()
    sel = mpv.TunnelSelector(pl, pts, [pair], mesh_path=mesh, faces=faces, previews=True)
    statuses = []
    sel.status_cb = lambda t, c: statuses.append(t)
    sel.toggle_view()                                    # preview not ready yet
    assert sel.view == "before" and "isn't ready" in statuses[-1]
    sel.previews.run()
    pl.reset_camera.reset_mock()
    sel.toggle_view()
    assert sel.view == "after"
    kw = [c.kwargs for c in pl.add_mesh.call_args_list if c.kwargs.get("name") == "result_surface"]
    assert kw and kw[-1]["reset_camera"] is False
    pl.actors["surface"].SetVisibility.assert_called_with(False)
    pl.actors["sel_loop_0_0"].SetVisibility.assert_called_with(False)
    pl.actors["problem_edges"].SetVisibility.assert_called_with(False)
    sel.toggle_view()
    assert sel.view == "before"
    pl.actors["surface"].SetVisibility.assert_called_with(True)
    pl.actors["sel_loop_0_0"].SetVisibility.assert_called_with(True)
    pl.reset_camera.assert_not_called()
    # a card change while viewing After keeps the rings hidden and shows the new option
    sel.toggle_view()
    sel.select_option(1)
    assert sel.view == "after"
    pl.actors["sel_loop_0_0"].SetVisibility.assert_called_with(False)


def test_after_view_p_compares_fix_with_original_and_undo_restores_rings(
        tmp_path, torus, pair):
    pts, faces = torus
    mesh = str(tmp_path / "aligned_head.ply")
    _write_ply(mesh, pts, faces)
    pl = _actor_pl()
    sel = mpv.TunnelSelector(pl, pts, [pair], mesh_path=mesh, faces=faces, previews=True)
    confirm_all(sel)
    sel.request_apply()
    assert sel.mode == "after" and sel.view == "after" and sel.can_toggle_view()
    pl.actors["sel_loop_0_0"].SetVisibility.assert_called_with(False)
    sel.toggle_view()
    assert sel.view == "before" and sel.mode == "after"
    pl.actors["surface"].SetVisibility.assert_called_with(True)
    pl.actors["sel_loop_0_0"].SetVisibility.assert_called_with(False)   # no rings in after mode
    sel.undo()
    assert sel.mode == "choose" and sel.view == "before"
    pl.actors["sel_loop_0_0"].SetVisibility.assert_called_with(True)
    assert not sel.has_unkept_result()


def test_unkept_result_is_what_close_asks_about(tmp_path, torus, pair):
    sel, mesh = make_selector(tmp_path, torus, pair, runner=False)
    assert not sel.has_unkept_result()
    confirm_all(sel)
    sel.request_apply()
    assert sel.has_unkept_result()
    sel.undo()
    assert not sel.has_unkept_result()
    confirm_all(sel)
    sel.request_apply()
    sel.keep()
    assert not sel.has_unkept_result(), "a kept fix never asks about discarding"


def test_keep_and_undo_ignored_while_a_worker_runs(tmp_path, torus, pair):
    queued = {}
    sel, mesh = make_selector(tmp_path, torus, pair, runner=False,
                              job_runner=lambda job, finish: queued.update(job=job,
                                                                           finish=finish))
    confirm_all(sel)
    sel.request_apply()
    assert sel.busy
    sel.keep()
    sel.undo()
    confirm_all(sel)
    sel.request_apply()
    assert sel.mode == "choose" and queued["job"] is not None
    queued["job"]()
    queued["finish"](None)
    assert sel.mode == "after" and not sel.busy
    queued.clear()
    sel.keep()                                           # saving goes to the runner
    assert sel.busy
    sel.undo()                                           # impossible while saving
    sel.keep()
    assert sel.mode == "after" and "job" in queued


# ---------------------------------------------------------------------------
# the B1 switch
# ---------------------------------------------------------------------------
def test_previews_disabled_flag_gives_stage2_behaviour_plus_after_view(
        tmp_path, torus, pair, monkeypatch):
    monkeypatch.setattr(mpv, "PREVIEWS_ENABLED", False)
    calls = spy_cut_and_patch(monkeypatch)
    pts, faces = torus
    mesh = str(tmp_path / "aligned_head.ply")
    _write_ply(mesh, pts, faces)
    started = []
    with mock.patch.object(mpv, "long_ring_flags",
                           lambda _p, loops, _d, *a, **k: [False] * len(loops)):
        sel = mpv.TunnelSelector(mock.MagicMock(), pts, [pair], mesh_path=mesh,
                                 faces=faces, preview_runner=lambda fn: started.append(fn))
    assert sel.previews is None
    sel.start_previews()
    assert started == []
    assert all(c["status"] is None and c["enabled"] for c in sel.cards())
    assert sel.selected_usable() and not sel.can_toggle_view()
    sel.toggle_view()
    assert sel.view == "before"
    confirm_all(sel)
    sel.request_apply()
    assert sel.mode == "after" and len(calls) == 1
    sel.keep()
    assert os.path.exists(ps.backup_path_for(mesh))


def test_no_preview_runner_means_no_previews_by_default(torus, pair):
    pts, faces = torus
    sel = mpv.TunnelSelector(mock.MagicMock(), pts, [pair], faces=faces)
    assert sel.previews is None


# ---------------------------------------------------------------------------
# background previews in a child process (keeps the GUI off the GIL-holding native calls)
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_process_mode_previews_match_in_process_result_and_forward_failures(torus, pair):
    pts, faces = torus
    bad = np.asarray(faces[0], dtype=np.int64)
    cache = mpv.PreviewCache(pts, faces, {(0, 0): pair["cut"], (0, 1): bad},
                             use_process=True)
    try:
        cache.run()
        assert cache.state((0, 0)) == ("ready", None)
        st, reason = cache.state((0, 1))
        assert st == "failed" and "does not sever" in reason
        rp, rf = cache.result((0, 0))
        ip, if_ = tle.cut_and_patch(pts, faces, pair["cut"])
        assert mesh_genus(rp, rf) == 0 and rp.shape == ip.shape and rf.shape == if_.shape
        assert np.allclose(rp, ip) and np.array_equal(rf, if_), "same real cut_and_patch"
        assert not rp.flags.writeable
    finally:
        cache.shutdown()


@pytest.mark.slow
def test_shutdown_cancels_the_child_and_leaves_the_option_pending(torus, pair):
    pts, faces = torus
    cache = mpv.PreviewCache(pts, faces, {(0, 0): pair["cut"], (0, 1): pair["avoid"]},
                             use_process=True)
    seen = []

    def on_update(key):
        seen.append(key)
        cache.shutdown()                       # the window closed after the first one
    cache.run(on_update=on_update)
    assert seen == [(0, 0)] and cache.pending_keys() == [(0, 1)]
    cache.run()                                # shut down: starts nothing
    assert cache.pending_keys() == [(0, 1)] and not cache.is_running()


def test_hung_child_times_out_is_killed_and_falls_back(torus, pair, monkeypatch):
    """A child that never answers must not hold the compute lock forever: the poll
    loop gives up after PREVIEW_CUT_TIMEOUT_S, kills the child and the option is
    computed in-process instead."""
    pts, faces = torus
    monkeypatch.setattr(mpv, "PREVIEW_CUT_TIMEOUT_S", 0.5)

    class HungProc:
        terminated = False

        def is_alive(self):
            return True

        def terminate(self):
            HungProc.terminated = True

    class HungConn:
        def send(self, x):
            pass

        def poll(self, t):
            import time as _t
            _t.sleep(min(t, 0.05))
            return False

    cache = mpv.PreviewCache(pts, faces, {(0, 0): pair["cut"]}, use_process=True)
    cache._proc, cache._conn = HungProc(), HungConn()
    cache.run()
    assert HungProc.terminated, "the hung child was killed"
    assert cache.state((0, 0)) == ("ready", None), "fell back to computing in-process"
    assert cache.use_process is False


def test_shutdown_interrupts_a_waiting_cut_promptly(torus, pair):
    import threading, time
    pts, faces = torus

    class SlowProc:
        def is_alive(self):
            return True

        def terminate(self):
            pass

    class SlowConn:
        def send(self, x):
            pass

        def poll(self, t):
            time.sleep(min(t, 0.05))
            return False

    cache = mpv.PreviewCache(pts, faces, {(0, 0): pair["cut"]}, use_process=True)
    cache._proc, cache._conn = SlowProc(), SlowConn()
    t = threading.Thread(target=cache.run)
    t.start()
    time.sleep(0.3)
    t0 = time.time()
    cache.shutdown()
    t.join(5)
    assert not t.is_alive() and time.time() - t0 < 2
    assert cache.state((0, 0))[0] == "pending"
