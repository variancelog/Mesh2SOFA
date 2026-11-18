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

import glob as _glob
import json
import os
from enum import Enum


# Project resolution modes (project_resolution in project.json). The frequency
# range lives here; mesh-grading args stay in process_and_grade.py.
# Keep (max_hz, step_hz) in sync with RESOLUTION_MODES in
# blender_scripts/mesh2SOFA_blender_addon.py (tests/test_resolution_modes.py checks it).
RESOLUTION_MODES = {
    "lowres":   {"max_hz": 16050, "step_hz": 150, "label": "Lowres (16 kHz Max)",   "double_length": False},
    "standard": {"max_hz": 18000, "step_hz": 150, "label": "Standard (18 kHz Max)", "double_length": False},
    "hires":    {"max_hz": 20000, "step_hz": 100, "label": "HiRes (20 kHz Max)",    "double_length": True},
}


def resolution_mode(name):
    """Settings dict for a project_resolution name; unknown -> standard."""
    return RESOLUTION_MODES.get(name, RESOLUTION_MODES["standard"])


class CleanState(Enum):
    CLEAN = "clean"          # check file present, no critical entry
    CRITICAL = "critical"    # check file present, >=1 critical entry
    NOT_RUN = "not_run"      # absent OR unreadable/corrupt


# mesh kind -> check filename
MESH_ALIGNED = "aligned"
MESH_GRADED = "graded"
_CHECK_FILE = {MESH_ALIGNED: "aligned_check.json", MESH_GRADED: "mesh_check.json"}


# Backup of the aligned mesh from before the FIRST in-app tunnel fix since the
# last alignment (one backup, not one per cut). Lives next to aligned_head.ply.
ALIGNED_MESH_NAME = "aligned_head.ply"
ALIGNED_BACKUP_NAME = "aligned_head.pre_cutpatch.ply"


def backup_path_for(mesh_path):
    """aligned_head.pre_cutpatch.ply next to the given aligned mesh."""
    return os.path.join(os.path.dirname(os.path.abspath(mesh_path)), ALIGNED_BACKUP_NAME)


def tmp_path_for(mesh_path):
    """Temp file used while (re)writing the mesh: aligned_head.tmp.ply. It keeps
    the .ply suffix on purpose -- VTK's PLY writer appends '.ply' to any other
    name, so 'aligned_head.ply.tmp' would silently end up as
    'aligned_head.ply.tmp.ply'."""
    root, ext = os.path.splitext(mesh_path)
    return root + ".tmp" + ext


def _remove_quiet(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def apply_with_backup(mesh_path, compute, write):
    """Safely replace `mesh_path` with the result of `compute()`.

    1. back the current file up to aligned_head.pre_cutpatch.ply, but only if
       that backup does not exist yet (it keeps the pre-FIRST-fix mesh);
    2. result = compute()   (the cut and its checks; may raise);
    3. write(result, tmp_path_for(mesh_path))  (aligned_head.tmp.ply);
    4. os.replace(tmp, mesh_path).
    If any step raises, the original mesh_path is untouched, the temp file is
    removed, and a backup created by THIS call is removed again (a backup that
    pre-dates the call is kept). The exception is re-raised. Returns result.
    """
    backup = backup_path_for(mesh_path)
    tmp = tmp_path_for(mesh_path)
    made_backup = False
    try:
        if not os.path.exists(backup):
            import shutil
            btmp = tmp_path_for(backup)
            shutil.copy2(mesh_path, btmp)
            os.replace(btmp, backup)
            made_backup = True
        result = compute()
        write(result, tmp)
        os.replace(tmp, mesh_path)
        return result
    except BaseException:
        _remove_quiet(tmp)
        _remove_quiet(tmp_path_for(backup))
        if made_backup:
            _remove_quiet(backup)
        raise


def restore_backup(mesh_path):
    """Copy aligned_head.pre_cutpatch.ply back over `mesh_path` atomically
    (tmp + os.replace). The backup is kept (it is still the pre-fix mesh) until
    the next alignment deletes it. Returns True if restored, False if there is
    no backup."""
    backup = backup_path_for(mesh_path)
    if not os.path.exists(backup):
        return False
    import shutil
    tmp = tmp_path_for(mesh_path)
    try:
        shutil.copy2(backup, tmp)
        os.replace(tmp, mesh_path)
    except BaseException:
        _remove_quiet(tmp)
        raise
    return True


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
        """Raw project_resolution string; default 'standard'. Use
        resolution_mode() for the frequency settings; remesh args stay in
        process_and_grade.py."""
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

    # --- aligned-mesh backup (made by the first in-app tunnel fix) ---
    @property
    def aligned_mesh_path(self):
        return os.path.join(self.mesh_dir, ALIGNED_MESH_NAME)

    @property
    def aligned_backup_path(self):
        return os.path.join(self.mesh_dir, ALIGNED_BACKUP_NAME)

    def has_aligned_backup(self):
        return os.path.exists(self.aligned_backup_path)

    def clear_aligned_backup(self):
        """Delete the pre-tunnel-fix backup. Idempotent. Called on re-alignment
        (next to clear_check(MESH_ALIGNED)): the backup belongs to the old
        alignment and must not be restorable over a new one."""
        _remove_quiet(self.aligned_backup_path)

    def restore_aligned_backup(self):
        """Put the pre-tunnel-fix mesh back as aligned_head.ply and drop the
        aligned check so the mesh counts as uninspected (grading then warns).
        Returns True if a backup was restored."""
        if not restore_backup(self.aligned_mesh_path):
            return False
        self.clear_check(MESH_ALIGNED)
        return True

    # --- mesh-prep artifact management (used on new-mesh import) ---

    def _mesh_artifact_paths(self):
        """Return full paths of every derived mesh-prep file that currently
        exists in mesh_dir.  Raw user meshes and import_report.json are never
        included — only the fixed-name / pattern outputs produced by the
        pipeline steps.  Used by both list_mesh_artifacts() and
        reset_mesh_artifacts() so the artifact set is defined in one place."""
        md = self.mesh_dir
        candidates = [
            # Step 1 outputs
            os.path.join(md, "aligned_head.ply"),
            os.path.join(md, "aligned_head_info.json"),
            # Step 2 check files (via _check_path for consistency)
            self._check_path(MESH_ALIGNED),   # aligned_check.json
            self._check_path(MESH_GRADED),    # mesh_check.json
            # Step 2 sentinel + loop exports
            os.path.join(md, "cutpatch_report.json"),
            os.path.join(md, ALIGNED_BACKUP_NAME),
        ]
        # Step 3 graded meshes (e.g. Left_Graded.ply, Right_Graded.ply)
        candidates.extend(_glob.glob(os.path.join(md, "*_Graded.ply")))
        # Tunnel-loop exports: aligned_head_cut_loops.npy / .txt
        candidates.extend(_glob.glob(os.path.join(md, "aligned_head_cut*")))
        return [p for p in candidates if os.path.exists(p)]

    def list_mesh_artifacts(self):
        """Basenames of derived mesh-prep files that exist in mesh_dir.
        Non-destructive — used to populate the confirmation warning before
        a new mesh import overwrites prior pipeline work."""
        return [os.path.basename(p) for p in self._mesh_artifact_paths()]

    def reset_mesh_artifacts(self):
        """Delete all derived mesh-prep files in mesh_dir.  Call before
        importing a new base mesh so prior alignment / inspection / grading
        results don't ghost-persist onto the new mesh.  Returns the list of
        deleted basenames for caller logging.  Idempotent."""
        removed = []
        for p in self._mesh_artifact_paths():
            try:
                os.remove(p)
                removed.append(os.path.basename(p))
            except FileNotFoundError:
                pass
        return removed

    def write_project(self, data):
        with open(self.project_json_path, "w") as f:
            json.dump(data, f, indent=4)
