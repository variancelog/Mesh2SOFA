"""Stage 4a: cutpatch_history.json (record every tunnel-fix choice)."""
import json
import os
from unittest import mock

import pytest

import mesh_problem_viewer as mpv
import project_store as ps
from test_tunnel_stage3 import make_selector, torus, pair, confirm_all  # noqa: F401 (fixtures)


def _events(d):
    with open(os.path.join(str(d), ps.HISTORY_NAME), encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == 1
    return data["events"]


# --- helper -----------------------------------------------------------------
def test_round_trip_and_order(tmp_path):
    for i in range(3):
        assert ps.append_cutpatch_history(str(tmp_path), {"event": "kept", "n": i})
    assert [e["n"] for e in _events(tmp_path)] == [0, 1, 2]
    assert os.listdir(tmp_path) == [ps.HISTORY_NAME], "no temp file left behind"


@pytest.mark.parametrize("junk", ["{not json", '{"version": 1}', '{"events": 5}', ""])
def test_corrupt_file_is_renamed_aside_and_restarted(tmp_path, junk):
    (tmp_path / ps.HISTORY_NAME).write_text(junk)
    assert ps.append_cutpatch_history(str(tmp_path), {"event": "undone"})
    assert len(_events(tmp_path)) == 1
    aside = [f for f in os.listdir(tmp_path) if f.startswith("cutpatch_history.corrupt-")]
    assert len(aside) == 1
    assert (tmp_path / aside[0]).read_text() == junk
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_helper_never_raises(tmp_path):
    assert ps.append_cutpatch_history(str(tmp_path / "missing_dir"), {"event": "x"}) is False
    with mock.patch("project_store.os.replace", side_effect=OSError("boom")):
        assert ps.append_cutpatch_history(str(tmp_path), {"event": "x"}) is False
    assert os.listdir(tmp_path) == [], "failed write leaves no temp file"
    # an unserialisable event must not raise either
    assert ps.append_cutpatch_history(str(tmp_path), {"event": object()}) is False
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_history_survives_reset_mesh_artifacts(tmp_path):
    ps.append_cutpatch_history(str(tmp_path), {"event": "kept"})
    (tmp_path / "aligned_head.ply").write_text("x")
    (tmp_path / "cutpatch_report.json").write_text("{}")
    store = ps.ProjectStore.for_mesh_dir(str(tmp_path))
    assert ps.HISTORY_NAME not in store.list_mesh_artifacts()
    removed = store.reset_mesh_artifacts()
    assert "aligned_head.ply" in removed and ps.HISTORY_NAME not in removed
    assert len(_events(tmp_path)) == 1


# --- viewer -------------------------------------------------------------------
def test_keep_records_event_and_undo_records_event(tmp_path, torus, pair):  # noqa: F811
    sel, mesh = make_selector(tmp_path, torus, pair, runner=False, previews=False)
    confirm_all(sel)
    sel.request_apply()
    assert sel.mode == "after"
    sel.undo()
    sel.select_option(1)
    confirm_all(sel)
    sel.request_apply()
    sel.keep()
    ev = _events(tmp_path)
    assert [e["event"] for e in ev] == ["undone", "kept"]
    und, kept = ev
    assert "genus_after" not in und and "face_count_after" not in und
    assert kept["genus_before"] == 1 and kept["genus_after"] == 0
    assert kept["face_count_before"] == len(torus[1]) and kept["face_count_after"] > 0
    assert kept["mesh"] == "aligned_head.ply"
    assert kept["time"] and und["time"]
    d = kept["defects"][0]
    assert d["chosen"] in ("TUNNEL", "HANDLE", "UNKNOWN") and d["other"] in (
        "TUNNEL", "HANDLE", "UNKNOWN")
    assert d["chosen_length_mm"] > 0 and d["other_length_mm"] > 0
    assert d["confirmed"] is True and und["defects"][0]["confirmed"] is True
    assert d["was_default"] is False and und["defects"][0]["was_default"] is True
    assert d["chosen_linking"] == pair["linking"]["avoid"] or d["chosen_linking"] is None \
        or len(d["chosen_linking"]) == 2
    assert d["other_linking"] is not None and len(d["other_linking"]) == 2
    assert os.path.exists(os.path.join(str(tmp_path), "cutpatch_report.json"))


def test_history_has_no_via_or_recommended_field(tmp_path, torus, pair):  # noqa: F811
    sel, mesh = make_selector(tmp_path, torus, pair, runner=False, previews=False)
    confirm_all(sel)
    sel.request_apply()
    sel.keep()
    ev = _events(tmp_path)[0]
    assert "via" not in ev and "recommended" not in json.dumps(ev)


def test_history_failure_never_blocks_keep_or_undo(tmp_path, torus, pair, monkeypatch):  # noqa: F811
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ps, "append_cutpatch_history", boom)
    sel, mesh = make_selector(tmp_path, torus, pair, runner=False, previews=False)
    confirm_all(sel)
    sel.request_apply()
    sel.undo()
    assert sel.mode == "choose"
    confirm_all(sel)
    sel.request_apply()
    sel.keep()
    assert sel.kept is True and os.path.exists(ps.backup_path_for(mesh))
    assert os.path.exists(os.path.join(str(tmp_path), "cutpatch_report.json"))


def test_failed_save_records_nothing(tmp_path, torus, pair, monkeypatch):  # noqa: F811
    sel, mesh = make_selector(tmp_path, torus, pair, runner=False, previews=False)
    confirm_all(sel)
    sel.request_apply()
    monkeypatch.setattr(mpv, "_write_ply_tmp",
                        mock.Mock(side_effect=OSError("write failed")))
    sel.keep()
    assert not sel.kept
    assert not os.path.exists(os.path.join(str(tmp_path), ps.HISTORY_NAME))
