"""Stage 4c: core/inspect_flow.py -- table-driven decisions, sentences, and the
InspectFlow controller driven with a fake command runner (no GUI, no subprocess)."""
import pytest

import inspect_flow as fl
from inspect_flow import next_action, describe, InspectFlow


def chk(severity="critical", **counts):
    base = {"holes": 0, "boundary_edges": 0, "non_manifold_edges": 0,
            "non_manifold_verts": 0, "components": 1, "si_faces": 0, "genus": 0,
            "volume": 1.0, "dup_verts": 0, "null_faces": 0,
            "unreferenced_verts": 0, "tiny_faces": 0}
    base.update(counts)
    return {"aligned": {"severity": severity, "counts": base}}


TUNNEL = chk(genus=2)


@pytest.mark.parametrize("check,repaired,expected", [
    (chk("ok"), False, "done"),
    (chk("minor", dup_verts=4), False, "done"),
    (chk("minor", tiny_faces=2), True, "done"),
    (TUNNEL, False, "viewer"),
    (TUNNEL, True, "viewer"),
    (chk(holes=3), False, "repair"),
    (chk(boundary_edges=6), False, "repair"),
    (chk(non_manifold_edges=1), False, "repair"),
    (chk(non_manifold_verts=1), False, "repair"),
    (chk(components=2), False, "repair"),
    (chk(si_faces=12), False, "repair"),
    (chk(volume=-5.0), False, "repair"),
    (chk(holes=3), True, "stuck"),
    (chk(si_faces=12), True, "stuck"),
    (chk(volume=-5.0), True, "stuck"),
    # tunnel + another critical: NOT tunnel-only, the fixer must not open
    (chk(genus=1, holes=1), False, "repair"),
    (chk(genus=1, holes=1), True, "stuck"),
    (chk(genus=1, components=3), True, "stuck"),
    (chk(genus=1, si_faces=1), True, "stuck"),
    # critical with nothing recognisable (e.g. only unknown keys) is not tunnel-only
    (chk(), False, "repair"),
    (chk(), True, "stuck"),
    # missing / malformed check -> never "done", never "viewer"
    (None, False, "stuck"),
    ({}, False, "stuck"),
    ({"aligned": "oops"}, True, "stuck"),
])
def test_next_action_table(check, repaired, expected):
    assert next_action(check, repaired) == expected


def test_unknown_keys_and_odd_values_are_tolerated():
    c = chk(genus=1)
    c["aligned"]["counts"]["brand_new_metric"] = 7
    c["aligned"]["counts"]["holes"] = None
    c["aligned"]["counts"]["volume"] = None
    assert next_action(c, False) == "viewer"
    del c["aligned"]["counts"]["holes"]
    assert next_action(c, False) == "viewer"
    c["aligned"]["counts"]["components"] = "x"
    assert next_action(c, False) == "viewer"


def test_describe_tunnel_not_mentioned_when_other_criticals_exist():
    # genus is meaningless on a non-watertight mesh: don't report it as a finding
    s = describe(chk(genus=2, holes=2))
    assert len(s) == 1 and "gaps" in s[0]


def test_describe_minor_issues_one_summary_sentence_only():
    s = describe(chk("minor", dup_verts=1, null_faces=2, unreferenced_verts=3, tiny_faces=4))
    assert len(s) == 1
    assert describe(chk("ok")) == []
    s = describe(chk(holes=1, tiny_faces=4, dup_verts=2))
    assert len(s) == 2


def test_no_gui_imports():
    import sys
    assert "tkinter" not in fl.__dict__ and "customtkinter" not in fl.__dict__


# ---------------------------------------------------------------------------
# InspectFlow with a fake command runner
# ---------------------------------------------------------------------------
class Fake:
    """Scripted io. `script` maps each worker call (in order) to
    (rc, check-after). Records every call."""
    def __init__(self, script, viewer_kept=()):
        self.script = list(script)
        self.viewer_kept = list(viewer_kept)
        self.calls, self.logs = [], []
        self.check = "unset"
        self.popups = []
        self.stuck_retry = None
        self.next_fix = None

    def run(self, kind, then):
        self.calls.append(kind)
        rc, check = self.script.pop(0)
        self.check = check
        then(rc)

    def read_check(self): return None if self.check == "unset" else self.check
    def clear_check(self): self.calls.append("clear"); self.check = "unset"
    def log(self, t): self.logs.append(t)
    def refresh(self): self.calls.append("refresh")

    def open_viewer(self, then):
        self.calls.append("viewer")
        then(self.viewer_kept.pop(0))

    def show_stuck(self, sentences, retry):
        self.calls.append("stuck"); self.popups.append(("stuck", sentences)); self.stuck_retry = retry

    def info(self, title, text): self.calls.append("info"); self.popups.append(("info", title))
    def warn(self, title, text): self.calls.append("warn"); self.popups.append(("warn", title))
    def offer_next(self, sentences, fix_next):
        self.calls.append("offer"); self.next_fix = fix_next


def kinds(fake):
    return [c for c in fake.calls if c not in ("refresh", "clear")]


def test_clean_mesh_is_one_inspect_and_a_log_line_only():
    f = Fake([(0, chk("ok"))])
    InspectFlow(f).start()
    assert kinds(f) == ["inspect"] and f.popups == []
    assert any("Mesh is ready" in l for l in f.logs)


def test_repairable_mesh_repairs_once_then_done_with_info_popup():
    f = Fake([(1, chk(holes=2)), (0, chk("ok"))])
    InspectFlow(f).start()
    assert kinds(f) == ["inspect", "repair", "info"]
    assert any("Repairing the mesh automatically" in l for l in f.logs)


def test_repair_leaves_a_tunnel_opens_viewer_without_dialog():
    f = Fake([(1, chk(si_faces=4)), (0, TUNNEL), (0, chk("ok"))], viewer_kept=[True])
    InspectFlow(f).start()
    assert kinds(f) == ["inspect", "repair", "viewer", "repair", "info"]
    assert "stuck" not in f.calls


def test_tunnel_only_goes_straight_to_viewer_no_repair():
    f = Fake([(1, TUNNEL)], viewer_kept=[False])
    InspectFlow(f).start()
    assert kinds(f) == ["inspect", "viewer"]
    assert any("1 see-through hole artifact" in l or "2 see-through hole artifacts" in l
               for l in f.logs)
    assert any("Tunnel fixer closed - mesh unchanged." in l for l in f.logs)
    assert f.popups == []                       # no dialog re-pop


def test_repair_runs_at_most_once_per_click_then_stuck():
    f = Fake([(1, chk(holes=2)), (1, chk(holes=2))])
    InspectFlow(f).start()
    assert kinds(f) == ["inspect", "repair", "stuck"]      # never a second repair
    assert f.popups[0][0] == "stuck" and "small gaps" in f.popups[0][1][0]
    assert "viewer" not in f.calls


def test_stuck_mesh_with_genus_never_opens_the_viewer():
    f = Fake([(1, chk(genus=1, holes=1)), (1, chk(genus=1, holes=1))])
    InspectFlow(f).start()
    assert "viewer" not in f.calls and "stuck" in f.calls


def test_try_again_repairs_once_more_and_can_finish():
    f = Fake([(1, chk(si_faces=3)), (1, chk(si_faces=3)), (0, chk("ok"))])
    flow = InspectFlow(f)
    flow.start()
    assert f.stuck_retry is not None
    f.stuck_retry()
    assert kinds(f) == ["inspect", "repair", "stuck", "repair", "info"]


def test_try_again_still_stuck_asks_again_but_never_loops_on_its_own():
    f = Fake([(1, chk(si_faces=3)), (1, chk(si_faces=3)), (1, chk(si_faces=3))])
    flow = InspectFlow(f)
    flow.start()
    f.stuck_retry()
    assert kinds(f).count("repair") == 2 and kinds(f).count("stuck") == 2


def test_inspect_that_did_not_write_a_check_stops():
    f = Fake([(1, "unset")])
    InspectFlow(f).start()
    assert kinds(f) == ["inspect"] and any("did not finish" in l for l in f.logs)


def test_kept_fix_with_more_defects_offers_next_and_reopens_viewer():
    f = Fake([(1, TUNNEL), (0, chk(genus=1))], viewer_kept=[True, False])
    InspectFlow(f).start()
    assert kinds(f) == ["inspect", "viewer", "repair", "offer"]
    f.next_fix()
    assert kinds(f)[-1] == "viewer"
    assert any("closed - mesh unchanged" in l for l in f.logs)


def test_kept_fix_leaving_a_real_problem_warns():
    f = Fake([(1, TUNNEL), (1, chk(si_faces=2))], viewer_kept=[True])
    InspectFlow(f).start()
    assert kinds(f) == ["inspect", "viewer", "repair", "warn"]


def test_state_resets_on_each_click():
    f = Fake([(1, chk(holes=1)), (0, chk("ok")), (0, chk("ok"))])
    flow = InspectFlow(f)
    flow.start()
    assert flow.changed
    flow.start()
    assert not flow.changed and f.popups == [("info", "Mesh is ready")]   # no 2nd popup
