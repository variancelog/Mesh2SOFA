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


HISTORY_NAME = "cutpatch_history.json"


def append_cutpatch_history(mesh_dir, event):
    """Append one event to <mesh_dir>/cutpatch_history.json ({"version": 1,
    "events": [...]}), via a temp file + os.replace. An unreadable / malformed
    file is renamed aside (cutpatch_history.corrupt-<timestamp>.json) and a fresh
    one started. Never raises (the record must not endanger a mesh save);
    returns True if the event was written. Deliberately NOT a mesh-prep
    artifact: the history outlives re-align and re-import."""
    import time
    path = os.path.join(mesh_dir, HISTORY_NAME)
    tmp = path + ".tmp"
    try:
        events = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                events = data["events"]
                if not isinstance(events, list):
                    raise ValueError("events is not a list")
            except Exception:
                aside = os.path.join(
                    mesh_dir, "cutpatch_history.corrupt-%s.json"
                    % time.strftime("%Y%m%d-%H%M%S"))
                n = 1
                while os.path.exists(aside):
                    n += 1
                    aside = os.path.join(
                        mesh_dir, "cutpatch_history.corrupt-%s-%d.json"
                        % (time.strftime("%Y%m%d-%H%M%S"), n))
                os.replace(path, aside)
                events = []
        events.append(event)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "events": events}, f, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[history] could not record the choice: {e}")
        _remove_quiet(tmp)
        return False


_PLY_TYPE_SIZE = {"char": 1, "int8": 1, "uchar": 1, "uint8": 1, "short": 2,
                  "int16": 2, "ushort": 2, "uint16": 2, "int": 4, "int32": 4,
                  "uint": 4, "uint32": 4, "float": 4, "float32": 4,
                  "double": 8, "float64": 8}


def _check_ply_header_and_size(backup):
    """Parse the PLY header ourselves and check the file is long enough for the
    data it announces. A truncated binary PLY crashes VTK's reader natively
    (segfault, not an exception), so no native reader may see a file that fails
    this. Raises ValueError with a plain reason. Returns the number of faces
    the header declares (the child process that reads the mesh checks it got
    exactly that many, which catches a truncated ASCII PLY)."""
    name = os.path.basename(backup)
    size = os.path.getsize(backup)
    with open(backup, "rb") as f:
        head = f.read(1 << 16)
    end = head.find(b"end_header")
    if not head.startswith(b"ply") or end < 0:
        raise ValueError(f"the backup {name} is not a valid PLY file")
    nl = head.find(b"\n", end)
    if nl < 0:
        raise ValueError(f"the backup {name} has an incomplete header")
    data_start = nl + 1
    fmt, elements = None, []          # elements: [name, count, [props]]
    try:
        for raw in head[:end].decode("ascii", "replace").splitlines():
            w = raw.split()
            if not w:
                continue
            if w[0] == "format":
                fmt = w[1]
            elif w[0] == "element":
                elements.append([w[1], int(w[2]), []])
            elif w[0] == "property" and elements:
                elements[-1][2].append(w[1:])
    except (ValueError, IndexError):
        raise ValueError(f"the backup {name} has a damaged header")
    counts = {e[0]: e[1] for e in elements}
    if counts.get("vertex", 0) <= 0:
        raise ValueError(f"the backup {name} contains no vertices")
    if counts.get("face", 0) <= 0:
        raise ValueError(f"the backup {name} contains no faces")
    if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise ValueError(f"the backup {name} has an unknown PLY format")
    if fmt == "ascii":
        if size <= data_start:
            raise ValueError(f"the backup {name} is truncated")
        # every element row is one text line: a file with fewer data lines than
        # vertices + faces was cut short
        with open(backup, "rb") as f:
            f.seek(data_start)
            rows = sum(1 for ln in f if ln.strip())
        if rows < sum(c for _n, c, _p in elements):
            raise ValueError(f"the backup {name} is truncated or damaged "
                             f"({rows} data rows, {sum(c for _n, c, _p in elements)} "
                             "declared)")
        return counts["face"]
    need = data_start
    for ename, cnt, props in elements:
        per = 0
        min_per = 0
        for p in props:
            if p[0] == "list":
                cs, isz = _PLY_TYPE_SIZE.get(p[1]), _PLY_TYPE_SIZE.get(p[2])
                if cs is None or isz is None:
                    raise ValueError(f"the backup {name} has an unknown property type")
                min_per += cs + (3 * isz if ename == "face" else 0)
            else:
                sz = _PLY_TYPE_SIZE.get(p[0])
                if sz is None:
                    raise ValueError(f"the backup {name} has an unknown property type")
                per += sz
                min_per += sz
        need += cnt * min_per
    if size < need:
        raise ValueError(f"the backup {name} is truncated or damaged "
                         f"({size} bytes, at least {need} expected)")
    return counts["face"]


# argv: backup path, number of faces the header declares. Exit 3 = no faces,
# 4 = a different number of faces than declared, or empty (<3 vertex) faces: a
# truncated ASCII PLY reads "fine" but the rows it never reached come back as
# empty faces; any other non-zero = unreadable / native crash.
_READ_CHECK = ("import sys, numpy as np, pyvista as pv\n"
               "m = pv.PLYReader(sys.argv[1]).read()\n"
               "f = np.asarray(m.faces)\n"
               "if f.size == 0:\n"
               "    sys.exit(3)\n"
               "n = i = 0\n"
               "while i < f.size:\n"
               "    if f[i] < 3:\n"
               "        sys.exit(4)  # unread rows come back as empty faces\n"
               "    i += int(f[i]) + 1; n += 1\n"
               "sys.exit(0 if i == f.size and n == int(sys.argv[2]) else 4)\n")


def _validate_backup(backup):
    """Raise ValueError with a plain reason unless `backup` is a non-empty,
    readable mesh with at least one face. Never crashes the calling process:
    the header/size are checked in pure Python first, and the full read runs in
    a child interpreter so a native reader crash becomes a ValueError."""
    import subprocess
    import sys
    name = os.path.basename(backup)
    if os.path.getsize(backup) == 0:
        raise ValueError(f"the backup {name} is empty (0 bytes)")
    declared_faces = _check_ply_header_and_size(backup)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    try:
        r = subprocess.run([sys.executable, "-I", "-c", _READ_CHECK, backup,
                            str(declared_faces)],
                           capture_output=True, timeout=60, **kw)
    except subprocess.TimeoutExpired:
        raise ValueError(f"reading the backup {name} timed out")
    if r.returncode == 3:
        raise ValueError(f"the backup {name} contains no faces")
    if r.returncode == 4:
        raise ValueError(f"the backup {name} is truncated or damaged (it holds "
                         "a different number of faces than its header declares)")
    if r.returncode != 0:
        raise ValueError(f"the backup {name} could not be read as a mesh")


def restore_backup(mesh_path):
    """MOVE aligned_head.pre_cutpatch.ply over `mesh_path` atomically
    (os.replace). The backup is consumed, so the GUI's Restore button hides; the
    next fix / auto-repair creates a fresh backup (backup-if-absent). Returns
    True if restored, False if there is no backup. Raises ValueError (mesh_path untouched) if the backup is empty or
    does not parse as a mesh with faces -- never replace a good mesh with a bad
    backup."""
    backup = backup_path_for(mesh_path)
    if not os.path.exists(backup):
        return False
    _validate_backup(backup)
    os.replace(backup, mesh_path)
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

    def clear_import_report(self):
        """Delete import_report.json (a new import must never be judged by the
        previous mesh's report). True if it existed."""
        p = os.path.join(self.mesh_dir, "import_report.json")
        try:
            os.remove(p)
            return True
        except FileNotFoundError:
            return False

    def write_project(self, data):
        with open(self.project_json_path, "w") as f:
            json.dump(data, f, indent=4)
