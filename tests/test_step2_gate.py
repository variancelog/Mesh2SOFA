"""Stage 5b: Process & Grade is gated by Step 2.
step2_state is table-tested; update_workflow_state / run_processing are driven
headlessly on a stub "self" (no window). All files live under tmp_path."""
import importlib.util
import json
import os
import types

import pytest

import inspect_flow as fl
from inspect_flow import step2_state, describe
from project_store import ProjectStore

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def imp(severity="ok", genus=0, **extra):
    counts = {"holes": 0, "boundary_edges": 0, "non_manifold_edges": 0,
              "non_manifold_verts": 0, "components": 1, "si_faces": 0,
              "genus": genus, "volume": 1.0}
    counts.update(extra)
    after = {"counts": counts}
    if severity is not None:
        after["severity"] = severity
    return {"filename": "m.ply", "before": {"severity": "critical"}, "after": after}


def aligned(severity, **counts):
    return {"aligned": {"severity": severity, "counts": counts}}


CLEAN_IMP = imp("ok")
CRIT_IMP = imp("critical", genus=1)


@pytest.mark.parametrize("report,check,expected", [
    # aligned_check present decides on its own
    (CLEAN_IMP, aligned("ok"), "done"),
    (CRIT_IMP, aligned("ok"), "done"),
    (None, aligned("minor"), "done"),
    (CLEAN_IMP, aligned("critical", genus=1), "needed"),
    (None, aligned("critical"), "needed"),
    # no aligned_check: the import report decides
    (CLEAN_IMP, None, "done"),
    (imp("minor"), None, "done"),
    (CRIT_IMP, None, "needed"),
    (imp("ok", genus=1), None, "needed"),
    (imp("critical", components=2), None, "needed"),
    (None, None, "needed"),                      # missing report
    ("not a dict", None, "needed"),              # corrupt report
    ({}, None, "needed"),
    ({"after": "x"}, None, "needed"),
    ({"after": {"severity": "ok", "counts": {"genus": "abc"}}}, None, "needed"),
    # severity absent: fall back to counts; negative = unknown = not clean
    (imp(None), None, "done"),
    (imp(None, holes=-1), None, "needed"),
    (imp(None, holes=2), None, "needed"),
    (imp(None, genus=1), None, "needed"),
    (imp(None, volume=-3.0), None, "needed"),
    ({"after": {"counts": {}}}, None, "needed"),
    # severity ok but a sentinel count: severity already accounts for it
    (imp("ok", holes=-1), None, "done"),
    # unreadable aligned_check (None) behaves as absent; empty one too
    (CLEAN_IMP, {}, "done"),
    (CRIT_IMP, {"aligned": {}}, "needed"),
])
def test_step2_state_table(report, check, expected):
    assert step2_state(report, check, "m.ply") == expected


@pytest.mark.parametrize("report,raw,expected", [
    (imp("ok"), "m.ply", "done"),
    (imp("ok"), "C:/p/Meshes/m.ply", "done"),          # path compares by file name
    (imp("ok"), "other.ply", "needed"),                # stale report of another mesh
    (imp("ok"), "", "needed"),
    (imp("ok"), None, "needed"),
    ({"after": imp("ok")["after"]}, "m.ply", "needed"),  # no filename recorded
])
def test_step2_state_requires_matching_mesh(report, raw, expected):
    assert step2_state(report, None, raw) == expected
    # an aligned check still decides on its own
    assert step2_state(report, aligned("ok"), raw) == "done"


def test_clear_import_report(gui, tmp_path):
    s = ProjectStore(str(tmp_path), mesh_dir=str(tmp_path))
    assert s.clear_import_report() is False
    (tmp_path / "import_report.json").write_text("{}")
    assert s.clear_import_report() is True
    assert not (tmp_path / "import_report.json").exists()


def test_import_mesh_start_deletes_old_report(tmp_path):
    """A failing import (missing file) must not leave the previous report."""
    import mesh_inspector
    (tmp_path / "import_report.json").write_text(json.dumps(imp("ok")))
    with pytest.raises(Exception):
        mesh_inspector.import_mesh(str(tmp_path / "nope.ply"), "blender")
    assert not (tmp_path / "import_report.json").exists()


def test_describe_ignores_negative_counts():
    chk = {"aligned": {"severity": "critical", "counts": {
        "holes": -1, "boundary_edges": -1, "non_manifold_edges": -1,
        "non_manifold_verts": -1, "components": 1, "genus": 0, "si_faces": 0}}}
    sentences = describe(chk)
    assert all("-1" not in s for s in sentences)
    assert not any("gap" in s or "pinched" in s or "open edges" in s for s in sentences)
    assert sentences  # a critical mesh still gets one plain sentence


# ---- GUI gating, headless -------------------------------------------------

@pytest.fixture(scope="module")
def gui():
    spec = importlib.util.spec_from_file_location(
        "gui_5b", os.path.join(REPO, "_project_manager_gui.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Btn:
    def __init__(self):
        self.state = None

    def configure(self, **kw):
        if "state" in kw:
            self.state = kw["state"]

    def grid(self, *a, **k): pass
    def grid_remove(self, *a, **k): pass


class Stub:
    def __init__(self, gui, root):
        self.g = gui
        self.root = str(root)
        self.mesh = os.path.join(self.root, "Meshes")
        os.makedirs(self.mesh, exist_ok=True)
        self.project_data = {}
        self.logs = []
        self.launched = False
        for n in ("align", "process", "blender", "numcalc", "sofa", "inspect",
                  "restore_tunnel", "extras", "vtk"):
            setattr(self, "btn_" + n, Btn())
        self.lbl_title = Btn()
        self.entry_base = types.SimpleNamespace(get=lambda: self.root)
        self.raw = "m.ply"
        self.entry_raw = types.SimpleNamespace(get=lambda: self.raw)

    def title(self, *a): pass
    def get_project_name(self): return "proj"
    def get_mesh_dir(self): return self.mesh
    def log(self, t, **k): self.logs.append(t)
    def _store(self, mesh_dir=None):
        return self.g.ProjectStore(self.root, mesh_dir=mesh_dir)
    def _mesh_check_passed(self, p): return self.g.HRTFProjectManager._mesh_check_passed(self, p)
    def _step2_state(self, p): return self.g.HRTFProjectManager._step2_state(self, p)
    def _launch_processing(self, *a): self.launched = True

    def put(self, name, data):
        with open(os.path.join(self.mesh, name), "w") as f:
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f)

    def refresh(self):
        self.g.HRTFProjectManager.update_workflow_state(self)


def make(gui, tmp_path, report, check):
    s = Stub(gui, tmp_path)
    s.put("aligned_head.ply", "ply")
    if report is not None:
        s.put("import_report.json", report)
    if check is not None:
        s.put("aligned_check.json", check)
    return s


def test_gate_needed_locks_process(gui, tmp_path):
    s = make(gui, tmp_path, imp("critical", genus=1), None)
    s.refresh()
    assert s.btn_process.state == "disabled"
    assert s.btn_inspect.state == "normal"
    gui.HRTFProjectManager.run_processing(s)
    assert not s.launched and len(s.logs) == 1


def test_gate_done_enables_process(gui, tmp_path):
    s = make(gui, tmp_path, imp("ok"), None)
    s.refresh()
    assert s.btn_process.state == "normal"
    gui.HRTFProjectManager.run_processing(s)
    assert s.launched


def test_gate_missing_or_corrupt_report_locks(gui, tmp_path):
    s = make(gui, tmp_path / "a", None, None)
    s.refresh()
    assert s.btn_process.state == "disabled"
    s = make(gui, tmp_path / "b", "{not json", None)
    s.refresh()
    assert s.btn_process.state == "disabled"


def test_gate_follows_aligned_check(gui, tmp_path):
    s = make(gui, tmp_path, imp("critical", genus=1), aligned("ok"))
    s.refresh()
    assert s.btn_process.state == "normal"          # inspected and fixed
    s.put("aligned_check.json", aligned("critical", genus=1))
    s.refresh()
    assert s.btn_process.state == "disabled"
    os.remove(os.path.join(s.mesh, "aligned_check.json"))   # e.g. after Restore
    s.refresh()
    assert s.btn_process.state == "disabled"        # bad import report -> needed


def test_stale_report_for_other_mesh_locks(gui, tmp_path):
    s = make(gui, tmp_path, imp("ok"), None)     # clean report for m.ply
    s.raw = "other.ply"                          # current mesh is another one
    s.refresh()
    assert s.btn_process.state == "disabled"
    s.raw = "m.ply"
    s.refresh()
    assert s.btn_process.state == "normal"


def test_next_step_is_step2_while_needed(gui, tmp_path):
    s = make(gui, tmp_path, imp("critical", genus=1), None)
    s.put("Left_Graded.ply", "ply")             # leftovers from an earlier run
    s.put("Right_Graded.ply", "ply")
    s.put("proj.blend", "x")
    os.makedirs(os.path.join(s.root, "Exports", "Left_Project"))
    s.refresh()
    assert s.project_data["progress"] == 1
    assert s.btn_process.state == "disabled"
    for b in ("blender", "numcalc", "sofa"):
        assert getattr(s, "btn_" + b).state == "disabled"
    assert s.btn_inspect.state == "normal"


def test_not_aligned_keeps_process_locked(gui, tmp_path):
    s = Stub(gui, tmp_path)
    s.refresh()
    assert s.btn_process.state == "disabled" and s.btn_inspect.state == "disabled"
