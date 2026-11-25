"""
test_project_store.py
=====================

Tests for project_store.ProjectStore — the owner of the GUI<->worker JSON
protocol. stdlib only (unittest + tempfile); no GUI / pymeshlab imports, so it
runs anywhere.

Run with:   python -m pytest tests/test_project_store.py
"""
import json
import os
import tempfile
import unittest

from project_store import (
    ProjectStore, CleanState, MESH_ALIGNED, MESH_GRADED,
    RESOLUTION_MODES, resolution_mode, normalize_step, export_step_hz,
    frequency_plan,
)


def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


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

    def test_corrupt_graded_is_not_run(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "mesh_check.json"), "w") as f:
                f.write("{not valid json")
            self.assertEqual(self._store(root).read_check(MESH_GRADED),
                             CleanState.NOT_RUN)


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

    def test_finds_at_depth_zero(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "project.json"), {"a": 1})
            self.assertEqual(ProjectStore.locate(root).project_root, root)

    def test_none_at_depth_three_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "project.json"), {"a": 1})
            deep = os.path.join(root, "a", "b", "c")
            os.makedirs(deep)
            self.assertIsNone(ProjectStore.locate(deep))


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

    def test_hires_passes_through(self):
        with tempfile.TemporaryDirectory() as root:
            _write_json(os.path.join(root, "project.json"),
                        {"project_resolution": "hires"})
            self.assertEqual(ProjectStore(root).resolution(), "hires")

    def test_resolution_mode_lookup(self):
        self.assertEqual(resolution_mode("hires")["max_hz"], 20000)
        self.assertNotIn("step_hz", resolution_mode("hires"))
        self.assertIs(resolution_mode("bogus"), RESOLUTION_MODES["standard"])

    def test_normalize_step(self):
        for raw, want in [(150, 150), (100, 100), (100.0, 100), ("100", 150),
                          (125, 150), (None, 150), (True, 150)]:
            self.assertEqual(normalize_step(raw), want, raw)

    def test_frequency_plan_unknown_mode_is_standard(self):
        self.assertEqual(frequency_plan("bogus", 100)["max_hz"], 18000)

    def _write_export(self, root, content):
        d = os.path.join(root, "Exports", "Left_Project")
        os.makedirs(d)
        with open(os.path.join(d, "parameters.json"), "w") as f:
            f.write(content)

    def test_export_step_hz(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(export_step_hz(root))
        for content, want in [('{"frequencyStepSize": 100}', 100),
                              ('{"frequencyStepSize": 150.0}', 150),
                              ('{"frequencyStepSize": 125}', None),
                              ('{"other": 1}', None),
                              ('not json', None)]:
            with tempfile.TemporaryDirectory() as root:
                self._write_export(root, content)
                self.assertEqual(export_step_hz(root), want, content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
