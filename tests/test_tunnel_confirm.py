"""Stage 6a: per-defect confirmation (headless TunnelSelector logic + pure helpers).
The panel widgets / F key routing are checked in the real window (6b)."""
import json
import os
from unittest import mock

import pytest

import mesh_problem_viewer as mpv
import project_store as ps
import tunnel_loop_extractor as tle
from test_tunnel_stage3 import (make_selector, torus, pair, dtorus_pairs,  # noqa: F401 (fixtures)
                                _write_ply)


def _sel(tmp_path, torus, pair, **kw):  # noqa: F811
    sel, mesh = make_selector(tmp_path, torus, pair, runner=False,
                              previews=kw.pop("previews", False), **kw)
    sel.status = []
    sel.status_cb = lambda text, color: sel.status.append(text)
    return sel, mesh


def _two_defects(tmp_path, dtorus_pairs):  # noqa: F811
    pts, faces, pairs = dtorus_pairs
    mesh = str(tmp_path / "aligned_head.ply")
    _write_ply(mesh, pts, faces)
    sel = mpv.TunnelSelector(mock.MagicMock(), pts, pairs, mesh_path=mesh,
                             faces=faces, genus=2, previews=False)
    sel.status = []
    sel.status_cb = lambda text, color: sel.status.append(text)
    return sel, mesh


# --- state / toggle ----------------------------------------------------------
def test_defects_start_unconfirmed_and_no_chosen_flag(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair)
    assert sel.handles[0]["confirmed"] is False
    assert "chosen" not in sel.handles[0]
    assert sel.confirmed_count() == 0 and not sel.all_confirmed()
    assert not sel.can_apply()


def test_confirm_toggles_and_does_not_advance(tmp_path, dtorus_pairs):  # noqa: F811
    sel, _ = _two_defects(tmp_path, dtorus_pairs)
    sel.confirm_active()
    assert sel.handles[0]["confirmed"] and sel.active == 0
    assert sel.confirmed_count() == 1 and not sel.all_confirmed()
    sel.confirm_active()                       # toggle off
    assert not sel.handles[0]["confirmed"] and sel.confirmed_count() == 0


def test_apply_refused_until_every_defect_is_confirmed(tmp_path, dtorus_pairs):  # noqa: F811
    sel, mesh = _two_defects(tmp_path, dtorus_pairs)
    sel.confirm_active()
    assert not sel.can_apply()
    sel.request_apply()
    assert sel.mode == "choose" and not sel.busy
    assert sel.status[-1] == "Confirm every defect first (1 of 2 confirmed)."
    sel.apply_cut_and_patch()                  # the worker entry is guarded too
    assert sel.mode == "choose" and sel.result is None
    sel.set_active(1, zoom=False)
    sel.confirm_active()
    assert sel.all_confirmed() and sel.can_apply()
    sel.request_apply()
    assert sel.mode == "after" and sel.result is not None
    assert not os.path.exists(ps.backup_path_for(mesh))   # nothing saved until Keep


# --- option changes ------------------------------------------------------------
def test_changing_option_clears_confirmation_with_note(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair)
    sel.confirm_active()
    assert mpv.OPTION_CHANGED_NOTE not in sel.notes()
    sel.select_option(1)
    assert sel.handles[0]["confirmed"] is False and not sel.can_apply()
    assert mpv.OPTION_CHANGED_NOTE == ("Option changed, so this defect needs "
                                       "confirming again.")
    assert sel.notes()[0] == mpv.OPTION_CHANGED_NOTE
    sel.confirm_active()                       # confirming again drops the note
    assert mpv.OPTION_CHANGED_NOTE not in sel.notes()


def test_on_pick_of_other_loop_clears_confirmation(tmp_path, torus, pair):  # noqa: F811
    """A 3D-view pick goes through select_option, so it can't bypass the rule."""
    sel, _ = _sel(tmp_path, torus, pair)
    sel.confirm_active()
    import numpy as np
    own, other = sel.handles[0]["loops"]
    # the vertex of the other loop farthest from the selected one (the two
    # loops cross, so their first vertices can coincide)
    d = [np.linalg.norm(sel.pts[own] - sel.pts[v], axis=1).min() for v in other]
    sel.on_pick(sel.pts[other[int(np.argmax(d))]])
    assert sel.handles[0]["sel"] == 1
    assert sel.handles[0]["confirmed"] is False and not sel.can_apply()


def test_same_option_changes_nothing(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair)
    sel.confirm_active()
    calls = []
    sel.on_change = lambda: calls.append(1)
    sel.select_option(sel.handles[0]["sel"])
    assert sel.handles[0]["confirmed"] is True and not calls
    assert mpv.OPTION_CHANGED_NOTE not in sel.notes()


def test_space_toggles_option_and_clears_confirmation(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair)
    sel.confirm_active()
    sel.toggle_active_loop()
    assert sel.handles[0]["sel"] == 1 and not sel.handles[0]["confirmed"]


def test_space_on_single_option_defect_does_nothing(tmp_path, dtorus_pairs):  # noqa: F811
    sel, _ = _two_defects(tmp_path, dtorus_pairs)
    sel.toggle_active_loop()
    assert not sel.handles[0]["confirmed"] and sel.handles[0]["sel"] == 0
    sel.confirm_active()
    sel.toggle_active_loop()
    assert sel.handles[0]["confirmed"]         # nothing to switch to, nothing cleared


def test_single_option_defect_starts_selected_but_unconfirmed(tmp_path, dtorus_pairs):  # noqa: F811
    sel, _ = _two_defects(tmp_path, dtorus_pairs)
    for h in sel.handles:
        assert len(h["loops"]) == 1 and h["sel"] == 0 and not h["confirmed"]
    card = sel.cards(0)[0]
    assert card["selected"] and card["status"].startswith(mpv.ONLY_ONE_WAY_TEXT)
    assert mpv.ONLY_ONE_WAY_TEXT == "Only one way to fix this defect"


def test_two_option_cards_have_no_only_one_way_status(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair)
    assert all(c["status"] != mpv.ONLY_ONE_WAY_TEXT for c in sel.cards())


def test_preview_failure_moving_selection_clears_confirmation(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair, previews=True)
    sel.confirm_active()
    assert sel.handles[0]["confirmed"]
    sel.previews._entries[(0, 0)].update(state="failed", reason="test")
    sel.on_preview_update((0, 0))
    assert sel.handles[0]["sel"] == 1 and not sel.handles[0]["confirmed"]
    assert sel.notes()[0] == mpv.OPTION_CHANGED_NOTE


def test_preview_failure_with_no_alternative_clears_confirmation(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair, previews=True)
    sel.confirm_active()
    sel.previews._entries[(0, 1)].update(state="failed", reason="test")
    sel.previews._entries[(0, 0)].update(state="failed", reason="test")
    sel.on_preview_update((0, 0))
    assert sel.handles[0]["sel"] == 0 and not sel.handles[0]["confirmed"]
    assert sel.notes()[0] == mpv.OPTION_CHANGED_NOTE and not sel.can_apply()


def test_preview_failure_of_unselected_option_keeps_confirmation(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair, previews=True)
    sel.confirm_active()
    sel.previews._entries[(0, 1)].update(state="failed", reason="test")
    sel.on_preview_update((0, 1))
    assert sel.handles[0]["sel"] == 0 and sel.handles[0]["confirmed"]


# --- refusals ------------------------------------------------------------------
def test_confirm_refused_while_busy(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair)
    sel.busy = True
    sel.confirm_active()
    assert not sel.handles[0]["confirmed"]


def test_confirm_refused_in_after_view(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair)
    sel.confirm_active()
    sel.request_apply()
    assert sel.mode == "after"
    sel.confirm_active()
    assert sel.handles[0]["confirmed"] is True          # not toggled off


def test_confirm_refused_on_unusable_option(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair, previews=True)
    for k in ((0, 0), (0, 1)):
        sel.previews._entries[k].update(state="failed", reason="test")
    sel.confirm_active()
    assert not sel.handles[0]["confirmed"] and sel.status
    assert not sel.can_apply()


# --- undo / failed apply keep confirmations ---------------------------------------
def test_undo_keeps_confirmations(tmp_path, dtorus_pairs):  # noqa: F811
    sel, _ = _two_defects(tmp_path, dtorus_pairs)
    sel.confirm_active()
    sel.set_active(1, zoom=False)
    sel.confirm_active()
    sel.request_apply()
    assert sel.mode == "after"
    sel.undo()
    assert sel.mode == "choose" and sel.all_confirmed() and sel.can_apply()


def test_failed_apply_keeps_confirmations(tmp_path, torus, pair, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tle, "cut_and_patch",
                        mock.Mock(side_effect=ValueError("check failed")))
    sel, _ = _sel(tmp_path, torus, pair)
    sel.confirm_active()
    sel.request_apply()
    assert sel.mode == "choose" and sel.handles[0]["confirmed"] is True


# --- history -------------------------------------------------------------------
def _events(d):
    with open(os.path.join(str(d), ps.HISTORY_NAME), encoding="utf-8") as f:
        return json.load(f)["events"]


def test_history_records_confirmed_and_never_recommended(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair)
    sel.confirm_active()
    sel.request_apply()
    sel.undo()
    sel.request_apply()
    sel.keep()
    ev = _events(tmp_path)
    assert [e["event"] for e in ev] == ["undone", "kept"]
    for e in ev:
        assert "via" not in e and "recommended" not in json.dumps(e)
        assert e["defects"][0]["confirmed"] is True


def test_history_event_reflects_unconfirmed_state(tmp_path, torus, pair):  # noqa: F811
    sel, _ = _sel(tmp_path, torus, pair)
    assert sel.history_event("undone")["defects"][0]["confirmed"] is False


# --- pure helpers ----------------------------------------------------------------
def test_confirm_label():
    assert mpv.confirm_label(False) == "Confirm this fix  [F]"
    assert mpv.confirm_label(True) == "Confirmed"


@pytest.mark.parametrize("n,text", [
    (0, "Apply selected fix  [C]"), (1, "Apply selected fix  [C]"),
    (2, "Apply all selected fixes  [C]"), (5, "Apply all selected fixes  [C]")])
def test_apply_label(n, text):
    assert mpv.apply_label(n) == text


@pytest.mark.parametrize("k,n,text", [
    (0, 1, "Confirm 1 more defect to enable."),
    (1, 2, "Confirm 1 more defect to enable."),
    (0, 2, "Confirm 2 more defects to enable."),
    (2, 5, "Confirm 3 more defects to enable."),
    (1, 1, "All defects confirmed."), (2, 2, "All defects confirmed.")])
def test_apply_caption(k, n, text):
    assert mpv.apply_caption(k, n) == text


def test_progress_chips():
    hs = [{"confirmed": True}, {"confirmed": False}, {"confirmed": False},
          {"confirmed": True}]
    chips = mpv.progress_chips(hs, 1, {0, 3})
    assert chips == [
        {"index": 0, "state": "confirmed", "current": False},
        {"index": 1, "state": "seen", "current": True},        # active counts as seen
        {"index": 2, "state": "unseen", "current": False},
        {"index": 3, "state": "confirmed", "current": False}]
    # confirmed wins over visited; a visited unconfirmed defect is "seen"
    assert mpv.progress_chips(hs, 2, {0, 1})[1]["state"] == "seen"
    assert mpv.progress_chips([], 0, set()) == []


def test_selector_tracks_visited_for_chips(tmp_path, dtorus_pairs):  # noqa: F811
    sel, _ = _two_defects(tmp_path, dtorus_pairs)
    assert [c["state"] for c in sel.progress_chips()] == ["seen", "unseen"]
    sel.set_active(1, zoom=False)
    sel.confirm_active()
    assert [c["state"] for c in sel.progress_chips()] == ["seen", "confirmed"]


# --- advanced rows / tight note (moved here from the removed recommend tests) ------
def test_advanced_rows_have_technical_details():
    import numpy as np
    pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0.0]])
    h = {"loops": [np.array([0, 1, 2])] * 2, "labels": ["TUNNEL", "HANDLE"], "sel": 0,
         "blocked": [(np.array([0, 1, 2]), "does not cut")],
         "linking": [[0.0, 1.0]] * 2}
    rows = mpv.advanced_rows([h], pts)
    assert len(rows) == 3
    assert "TUNNEL" in rows[0] and "mm" in rows[0] and "linking" in rows[0]
    assert "<- selected" in rows[0] and "<- selected" not in rows[1]
    assert "not available" in rows[2] and "does not cut" in rows[2]


def test_tight_note_points_at_advanced():
    assert "Advanced" in mpv.TIGHT_MODE_NOTE and "Blender" in mpv.TIGHT_MODE_NOTE


def test_no_recommendation_api_left():
    for name in ("recommended_selection", "recommended_label", "recommended_caption",
                 "RECOMMEND_LABEL", "RECOMMEND_CAPTION"):
        assert not hasattr(mpv, name)
    for name in ("recommendation", "can_apply_recommended", "apply_recommended"):
        assert not hasattr(mpv.TunnelSelector, name)
