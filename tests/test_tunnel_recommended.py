"""Stage 4b: "Apply recommended fix" + Advanced section -- headless parts.
The panel widgets / R key routing / focus policy run in a real window and are
checked by plans/tunnel-loop/viewer_smoke.py."""
import copy
import json
import os
from unittest import mock

import numpy as np
import pytest

import mesh_problem_viewer as mpv
import project_store as ps
from test_tunnel_stage3 import make_selector, torus, pair  # noqa: F401 (fixtures)


def _h(labels, sel=0, chosen=False, blocked=()):
    n = len(labels)
    return {"loops": [np.array([0, 1, 2])] * n, "labels": list(labels), "sel": sel,
            "blocked": list(blocked), "linking": [[0.0, 1.0]] * n, "chosen": chosen}


def _labelled(pair, cut, avoid):
    p = copy.deepcopy(pair)
    p["labels"] = {"cut": cut, "avoid": avoid}
    return p


# --- recommended_selection ---------------------------------------------------
def test_all_classified_picks_the_tunnel_loop_whatever_its_index():
    sel, reason = mpv.recommended_selection(
        [_h(["TUNNEL", "HANDLE"]), _h(["HANDLE", "TUNNEL"], sel=0)])
    assert sel == [0, 1] and reason == ""


def test_single_classified_defect():
    assert mpv.recommended_selection([_h(["HANDLE", "TUNNEL"])]) == ([1], "")


def test_unknown_unchosen_is_refused_with_its_number():
    sel, reason = mpv.recommended_selection(
        [_h(["TUNNEL", "HANDLE"]), _h(["UNKNOWN", "UNKNOWN"])])
    assert sel is None
    assert reason.startswith("Defect 2:") and "choose an option" in reason


def test_unknown_chosen_keeps_the_users_option():
    sel, _ = mpv.recommended_selection(
        [_h(["TUNNEL", "HANDLE"]), _h(["UNKNOWN", "UNKNOWN"], sel=1, chosen=True)])
    assert sel == [0, 1]


def test_single_option_defect_needs_an_explicit_choice():
    assert mpv.recommended_selection([_h(["UNKNOWN"])])[0] is None
    assert mpv.recommended_selection([_h(["UNKNOWN"], chosen=True)])[0] == [0]


def test_unavailable_tunnel_option_is_refused_no_fallback_to_handle():
    usable = lambda hi, li: not (hi == 1 and li == 0)  # noqa: E731
    sel, reason = mpv.recommended_selection(
        [_h(["TUNNEL", "HANDLE"]), _h(["TUNNEL", "HANDLE"])], usable)
    assert sel is None and reason.startswith("Defect 2:")
    assert "Fill the hole" in reason


def test_chosen_unknown_option_that_failed_is_refused():
    sel, reason = mpv.recommended_selection(
        [_h(["UNKNOWN", "UNKNOWN"], sel=1, chosen=True)], lambda hi, li: li != 1)
    assert sel is None and "Defect 1" in reason


def test_no_defects():
    assert mpv.recommended_selection([])[0] is None


def test_advanced_rows_have_technical_details():
    pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0.0]])
    h = _h(["TUNNEL", "HANDLE"], blocked=[(np.array([0, 1, 2]), "does not cut")])
    rows = mpv.advanced_rows([h], pts)
    assert len(rows) == 3
    assert "TUNNEL" in rows[0] and "mm" in rows[0] and "linking" in rows[0]
    assert "<- selected" in rows[0] and "<- selected" not in rows[1]
    assert "not available" in rows[2] and "does not cut" in rows[2]


def test_tight_note_points_at_advanced():
    assert "Advanced" in mpv.TIGHT_MODE_NOTE and "Blender" in mpv.TIGHT_MODE_NOTE


# --- selector ---------------------------------------------------------------
def test_explicit_choice_flag_is_set_by_select_option_and_space(tmp_path, torus, pair):  # noqa: F811
    sel, _ = make_selector(tmp_path, torus, pair, runner=False, previews=False)
    assert sel.handles[0]["chosen"] is False
    sel.select_option(0)             # the already-selected card still counts
    assert sel.handles[0]["chosen"] is True
    sel.handles[0]["chosen"] = False
    sel.toggle_active_loop()
    assert sel.handles[0]["chosen"] is True


def test_apply_recommended_on_classified_defect_records_via(tmp_path, torus, pair):  # noqa: F811
    p = _labelled(pair, "HANDLE", "TUNNEL")
    pts, faces = torus
    sel, _ = make_selector(tmp_path, torus, p, runner=False, previews=False)
    assert sel.can_apply_recommended()
    sel.apply_recommended()
    assert sel.mode == "after" and sel.handles[0]["sel"] == 1   # the TUNNEL loop
    assert sel.via == "recommended"
    sel.keep()
    with open(os.path.join(str(tmp_path), ps.HISTORY_NAME), encoding="utf-8") as f:
        ev = json.load(f)["events"]
    assert ev[-1]["event"] == "kept" and ev[-1]["via"] == "recommended"
    assert ev[-1]["defects"][0]["chosen"] == "TUNNEL"


def test_undo_resets_via_and_records_recommended(tmp_path, torus, pair):  # noqa: F811
    sel, _ = make_selector(tmp_path, torus, _labelled(pair, "TUNNEL", "HANDLE"),
                           runner=False, previews=False)
    sel.apply_recommended()
    sel.undo()
    assert sel.via == "manual" and sel.mode == "choose"
    sel.request_apply()
    sel.undo()
    with open(os.path.join(str(tmp_path), ps.HISTORY_NAME), encoding="utf-8") as f:
        ev = json.load(f)["events"]
    assert [e["via"] for e in ev] == ["recommended", "manual"]


def test_apply_recommended_refused_for_unknown_and_changes_nothing(tmp_path, torus, pair):  # noqa: F811
    sel, mesh = make_selector(tmp_path, torus, _labelled(pair, "UNKNOWN", "UNKNOWN"),
                              runner=False, previews=False)
    assert not sel.can_apply_recommended()
    status = []
    sel.status_cb = lambda text, color: status.append(text)
    sel.apply_recommended()
    assert sel.mode == "choose" and sel.via == "manual"
    assert status and "choose an option" in status[-1]
    sel.select_option(0)             # explicit pick
    assert sel.can_apply_recommended()
    sel.apply_recommended()
    assert sel.mode == "after" and sel.via == "recommended"
    assert not os.path.exists(ps.backup_path_for(mesh))   # nothing saved until Keep


def test_manual_change_after_recommendation_is_manual(tmp_path, torus, pair):  # noqa: F811
    sel, _ = make_selector(tmp_path, torus, _labelled(pair, "TUNNEL", "HANDLE"),
                           runner=False, previews=False)
    sel.via = "recommended"
    sel.select_option(1)
    assert sel.via == "manual"


def test_failed_recommended_apply_resets_via(tmp_path, torus, pair, monkeypatch):  # noqa: F811
    import tunnel_loop_extractor as tle
    monkeypatch.setattr(tle, "cut_and_patch",
                        mock.Mock(side_effect=ValueError("check failed")))
    sel, _ = make_selector(tmp_path, torus, _labelled(pair, "TUNNEL", "HANDLE"),
                           runner=False, previews=False)
    sel.apply_recommended()
    assert sel.mode == "choose" and sel.via == "manual"


def test_recommended_ignored_while_busy_or_in_after_view(tmp_path, torus, pair):  # noqa: F811
    sel, _ = make_selector(tmp_path, torus, _labelled(pair, "TUNNEL", "HANDLE"),
                           runner=False, previews=False)
    sel.busy = True
    assert not sel.can_apply_recommended()
    sel.apply_recommended()
    assert sel.mode == "choose"
    sel.busy = False
    sel.apply_recommended()
    assert sel.mode == "after" and not sel.can_apply_recommended()


def test_selector_advanced_rows(tmp_path, torus, pair):  # noqa: F811
    sel, _ = make_selector(tmp_path, torus, pair, runner=False, previews=False)
    rows = sel.advanced_rows()
    assert len(rows) >= 2 and all("mm" in r for r in rows)
