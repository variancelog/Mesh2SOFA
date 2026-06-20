"""ProjectStore — owns the GUI<->worker JSON protocol.

The per-project JSON files (`project.json`, `aligned_check.json`,
`mesh_check.json`) *are* the protocol between the long-lived GUI
(`_project_manager_gui.py`) and the short-lived worker subprocesses
(`mesh_inspector.py`, `process_and_grade.py`, `run_numcalc_test.py`).

This module is the single owner of:
- where each file lives (project root vs. mesh dir),
- the `{ <key>: {"severity", "counts"} }` check-file envelope,
- the "is any entry critical?" rule (-> CleanState),
- the project-root walk (absorbs the old `find_project_json`),
- the `project_resolution` default.

stdlib-only; never imports GUI / pymeshlab so workers and tests can use it.
Output is byte-identical to the old hand-written `json.dump(..., indent=4)`
calls so the Blender add-on (which reads `project.json` directly) stays
compatible.
"""

import json
import os
from enum import Enum


class CleanState(Enum):
    CLEAN = "clean"          # check file present, no critical entry
    CRITICAL = "critical"    # check file present, >=1 critical entry
    NOT_RUN = "not_run"      # absent OR unreadable/corrupt


# mesh kind -> check filename
MESH_ALIGNED = "aligned"
MESH_GRADED = "graded"
_CHECK_FILE = {MESH_ALIGNED: "aligned_check.json", MESH_GRADED: "mesh_check.json"}


class ProjectStore:
    def __init__(self, project_root, *, mesh_dir=None):
        self.project_root = project_root
        self._mesh_dir = mesh_dir            # explicit override; else derived

    # --- constructors for the two real entry points ---
    @classmethod
    def locate(cls, start_path, max_levels=3):
        """Worker path: walk up for project.json. Returns store or None.
        Absorbs the old find_project_json()."""
        curr = start_path
        for _ in range(max_levels):
            if os.path.exists(os.path.join(curr, "project.json")):
                return cls(curr)
            curr = os.path.dirname(curr)
        return None

    @classmethod
    def for_mesh_dir(cls, mesh_dir):
        """Anchor checks at a known mesh dir (used by mesh_inspector, and as the
        write fallback when project.json can't be located). Best-effort root for
        resolution()."""
        located = cls.locate(mesh_dir)
        return cls(located.project_root if located else mesh_dir, mesh_dir=mesh_dir)

    # --- paths (single source of truth) ---
    @property
    def mesh_dir(self):
        if self._mesh_dir:
            return self._mesh_dir
        cand = os.path.join(self.project_root, "Meshes")
        return cand if os.path.isdir(cand) else self.project_root

    @property
    def project_json_path(self):
        return os.path.join(self.project_root, "project.json")

    def _check_path(self, mesh):
        return os.path.join(self.mesh_dir, _CHECK_FILE[mesh])

    # --- reads ---
    def read_check(self, mesh):
        """The CleanState of a check file (CLEAN / CRITICAL / NOT_RUN)."""
        data = self.read_check_data(mesh)
        if data is None:
            return CleanState.NOT_RUN
        if any(v.get("severity") == "critical" for v in data.values()):
            return CleanState.CRITICAL
        return CleanState.CLEAN

    def read_check_data(self, mesh):
        """Parsed envelope, or None if absent/unreadable. For dialogs that need
        counts. Corrupt JSON folds into None (-> NOT_RUN)."""
        p = self._check_path(mesh)
        if not os.path.exists(p):
            return None
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return None

    def load_project(self):
        try:
            with open(self.project_json_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def get(self, key, default=None):
        return self.load_project().get(key, default)

    def resolution(self):
        """Raw project_resolution string; default 'standard'. The number mapping
        (max_freq / remesh args / NumCalc index) stays in the callers."""
        return self.load_project().get("project_resolution", "standard")

    # --- writes (envelope + format owned here; severity/counts computed by caller) ---
    def write_check(self, mesh, results):
        """results: {envelope_key: (severity, counts)}. Builds the
        {key: {"severity", "counts"}} envelope so the literals live with
        read_check."""
        payload = {k: {"severity": sev, "counts": counts}
                   for k, (sev, counts) in results.items()}
        with open(self._check_path(mesh), "w") as f:
            json.dump(payload, f, indent=4)

    def clear_check(self, mesh):
        """Remove a check file so the mesh folds back to NOT_RUN. Idempotent.
        Used when a mesh is overwritten (e.g. re-alignment) and the prior
        inspection result no longer applies to the new mesh content."""
        try:
            os.remove(self._check_path(mesh))
        except FileNotFoundError:
            pass

    def write_project(self, data):
        with open(self.project_json_path, "w") as f:
            json.dump(data, f, indent=4)
