"""
test_project_store.py
=====================

Tests for project_store.ProjectStore — the owner of the GUI<->worker JSON
protocol. stdlib only (unittest + tempfile); no GUI / pymeshlab imports, so it
runs anywhere.

Run with:   python test_project_store.py
or:         pytest test_project_store.py
"""
import json
import os
import tempfile
import unittest

from project_store import (
    ProjectStore, CleanState, MESH_ALIGNED, MESH_GRADED,
)


def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


# --- reference implementations of the OLD inline rules, to lock the truth table ---
def _old_aligned_check_passed(mesh_path):
    """Verbatim logic of the old GUI _aligned_check_passed."""
    p = os.path.join(mesh_path, "aligned_check.json")
    if not os.path.exists(p):
        return False
    try:
        with open(p) as f:
            data = json.load(f)
        return not any(v.get("severity") == "critical" for v in data.values())
    except Exception:
        return False


def _old_mesh_check_passed(mesh_path):
    """Verbatim logic of the old GUI _mesh_check_passed."""
    p = os.path.join(mesh_path, "mesh_check.json")
    if not os.path.exists(p):
        return True
    try:
        with open(p) as f:
            data = json.load(f)
        return not any(v.get("severity") == "critical" for v in data.values())
    except Exception:
        return True


def _old_find_project_json(start_path):
    """Verbatim logic of the deleted process_and_grade.find_project_json."""
    curr = start_path
    for _ in range(3):
        candidate = os.path.join(curr, "project.json")
        if os.path.exists(candidate):
            return candidate
        curr = os.path.dirname(curr)
    return None


class MeshDirDerivation(unittest.TestCase):
    def test_no_meshes_subfolder_uses_root(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(ProjectStore(root).mesh_dir, root)

    def test_meshes_subfolder_used_when_present(self):
        with tempfile.TemporaryDirectory() as root:
            meshes = os.path.join(root, "Meshes")
            os.mkdir(meshes)
            self.assertEqual(ProjectStore(root).mesh_dir, meshes)

    def test_explicit_mesh_dir_overrides(self):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "Meshes"))
            self.assertEqual(
                ProjectStore(root, mesh_dir=root).mesh_dir, root)


class ReadCheckStates(unittest.TestCase):
    def _store(self, root):
        return ProjectStore(root, mesh_dir=root)

    def test_absent_is_not_run(self):
        with tempfile.TemporaryDirectory() as root:
            s = self._store(root)
            self.assertEqual(s.read_check(MESH_ALIGNED), CleanState.NOT_RUN)
            self.assertEqual(s.read_check(MESH_GRADED), CleanState.NOT_RUN)
            self.assertIsNone(s.read_check_data(MESH_ALIGNED))

    def test_clean(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "aligned_check.json"),
                        {"aligned": {"severity": "ok", "counts": {}}})
            _write_json(os.path.join(root, "mesh_check.json"),
                        {"left": {"severity": "ok", "counts": {}},
                         "right": {"severity": "repaired", "counts": {}}})
            s = self._store(root)
            self.assertEqual(s.read_check(MESH_ALIGNED), CleanState.CLEAN)
            self.assertEqual(s.read_check(MESH_GRADED), CleanState.CLEAN)

    def test_critical(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "aligned_check.json"),
                        {"aligned": {"severity": "critical", "counts": {"genus": 1}}})
            _write_json(os.path.join(root, "mesh_check.json"),
                        {"left": {"severity": "ok", "counts": {}},
                         "right": {"severity": "critical", "counts": {}}})
            s = self._store(root)
            self.assertEqual(s.read_check(MESH_ALIGNED), CleanState.CRITICAL)
            self.assertEqual(s.read_check(MESH_GRADED), CleanState.CRITICAL)

    def test_corrupt_is_not_run(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "aligned_check.json"), "w") as f:
                f.write("{not valid json")
            s = self._store(root)
            self.assertEqual(s.read_check(MESH_ALIGNED), CleanState.NOT_RUN)
            self.assertIsNone(s.read_check_data(MESH_ALIGNED))


class AbsentPolicyTruthTable(unittest.TestCase):
    """read_check==CLEAN must equal old _aligned_check_passed; read_check!=CRITICAL
    must equal old _mesh_check_passed, across absent/clean/critical/corrupt."""

    def _make(self, root, name, payload):
        if payload is not None:
            _write_json(os.path.join(root, name), payload)

    CASES = {
        "absent": None,
        "clean": {"x": {"severity": "ok", "counts": {}}},
        "critical": {"x": {"severity": "critical", "counts": {}}},
    }

    def test_aligned_matches_old(self):
        for label, payload in self.CASES.items():
            with tempfile.TemporaryDirectory() as root:
                self._make(root, "aligned_check.json", payload)
                store = ProjectStore(root, mesh_dir=root)
                new = store.read_check(MESH_ALIGNED) == CleanState.CLEAN
                self.assertEqual(new, _old_aligned_check_passed(root),
                                 f"aligned mismatch on {label}")

    def test_mesh_matches_old(self):
        for label, payload in self.CASES.items():
            with tempfile.TemporaryDirectory() as root:
                self._make(root, "mesh_check.json", payload)
                store = ProjectStore(root, mesh_dir=root)
                new = store.read_check(MESH_GRADED) != CleanState.CRITICAL
                self.assertEqual(new, _old_mesh_check_passed(root),
                                 f"mesh mismatch on {label}")


class WriteRoundTripAndBytes(unittest.TestCase):
    def test_write_then_read(self):
        with tempfile.TemporaryDirectory() as root:
            s = ProjectStore(root, mesh_dir=root)
            s.write_check(MESH_ALIGNED, {"aligned": ("critical", {"genus": 1})})
            self.assertEqual(s.read_check(MESH_ALIGNED), CleanState.CRITICAL)
            data = s.read_check_data(MESH_ALIGNED)
            self.assertEqual(data, {"aligned": {"severity": "critical",
                                                "counts": {"genus": 1}}})

    def test_byte_identical_to_handwritten(self):
        """Protects Blender compatibility: same dict + indent=4 the old code used."""
        with tempfile.TemporaryDirectory() as root:
            s = ProjectStore(root, mesh_dir=root)
            s.write_check(MESH_GRADED,
                          {"left": ("ok", {"genus": 0}),
                           "right": ("critical", {"genus": 1})})
            with open(os.path.join(root, "mesh_check.json"), "rb") as f:
                got = f.read()

            expected_dict = {
                "left": {"severity": "ok", "counts": {"genus": 0}},
                "right": {"severity": "critical", "counts": {"genus": 1}},
            }
            ref = os.path.join(root, "_ref.json")
            with open(ref, "w") as f:
                json.dump(expected_dict, f, indent=4)
            with open(ref, "rb") as f:
                expected = f.read()
            self.assertEqual(got, expected)

    def test_write_project_byte_identical(self):
        with tempfile.TemporaryDirectory() as root:
            s = ProjectStore(root)
            data = {"base_path": root, "project_resolution": "lowres", "n": 3}
            s.write_project(data)
            with open(s.project_json_path, "rb") as f:
                got = f.read()
            ref = os.path.join(root, "_ref.json")
            with open(ref, "w") as f:
                json.dump(data, f, indent=4)
            with open(ref, "rb") as f:
                expected = f.read()
            self.assertEqual(got, expected)


class Locate(unittest.TestCase):
    def test_finds_one_and_two_levels_up(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "project.json"), {"a": 1})
            one = os.path.join(root, "Meshes")
            two = os.path.join(root, "Exports", "Left_Project")
            os.makedirs(one)
            os.makedirs(two)
            self.assertEqual(ProjectStore.locate(one).project_root, root)
            self.assertEqual(ProjectStore.locate(two).project_root, root)

    def test_none_past_three_levels(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "project.json"), {"a": 1})
            deep = os.path.join(root, "a", "b", "c", "d")
            os.makedirs(deep)
            self.assertIsNone(ProjectStore.locate(deep))

    def test_equals_old_find_project_json(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "project.json"), {"a": 1})
            for depth in range(0, 5):
                start = root
                for i in range(depth):
                    start = os.path.join(start, f"lvl{i}")
                os.makedirs(start, exist_ok=True)
                store = ProjectStore.locate(start)
                old = _old_find_project_json(start)
                if old is None:
                    self.assertIsNone(store, f"depth {depth}")
                else:
                    self.assertEqual(store.project_json_path, old, f"depth {depth}")


class ForMeshDir(unittest.TestCase):
    def test_writes_land_in_mesh_dir_without_project_json(self):
        with tempfile.TemporaryDirectory() as root:
            mesh_dir = os.path.join(root, "loose")
            os.mkdir(mesh_dir)
            s = ProjectStore.for_mesh_dir(mesh_dir)
            s.write_check(MESH_GRADED, {"left": ("ok", {})})
            self.assertTrue(
                os.path.exists(os.path.join(mesh_dir, "mesh_check.json")))

    def test_root_resolved_when_project_json_present(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "project.json"),
                        {"project_resolution": "lowres"})
            meshes = os.path.join(root, "Meshes")
            os.mkdir(meshes)
            s = ProjectStore.for_mesh_dir(meshes)
            self.assertEqual(s.project_root, root)
            self.assertEqual(s.mesh_dir, meshes)
            self.assertEqual(s.resolution(), "lowres")


class Resolution(unittest.TestCase):
    def test_reads_value(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "project.json"),
                        {"project_resolution": "lowres"})
            self.assertEqual(ProjectStore(root).resolution(), "lowres")

    def test_default_when_key_missing(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "project.json"), {"other": 1})
            self.assertEqual(ProjectStore(root).resolution(), "standard")

    def test_default_when_file_missing(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(ProjectStore(root).resolution(), "standard")


if __name__ == "__main__":
    unittest.main(verbosity=2)
