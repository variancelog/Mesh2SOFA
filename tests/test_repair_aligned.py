"""Stage 4c: mesh_inspector.repair_aligned -- exit codes and the single backup,
on synthetic meshes (no zzmeshes needed)."""
import hashlib
import os

import numpy as np
import pytest
import pyvista as pv

import mesh_inspector as mi
import project_store as ps

SCALE = 10.0


def _torus_faces():
    t = pv.ParametricTorus(ringradius=3.0, crosssectionradius=1.0,
                           u_res=48, v_res=24).triangulate().clean()
    pts = np.asarray(t.points, dtype=np.float64) * SCALE
    faces = t.faces.reshape(-1, 4)[:, 1:].astype(np.int64)
    return pts, faces


def _save(path, pts, faces):
    cells = np.hstack([np.full((len(faces), 1), 3), faces]).ravel()
    pv.PolyData(pts, cells).save(str(path), binary=True)


def _digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _run(mesh_dir):
    with pytest.raises(SystemExit) as e:
        mi.repair_aligned(str(mesh_dir))
    return e.value.code


def _listing(d):
    return sorted(os.listdir(d))


def test_tunnel_only_mesh_is_untouched_exit_0_no_backup(tmp_path):
    pts, faces = _torus_faces()
    _save(tmp_path / "aligned_head.ply", pts, faces)
    before = _digest(tmp_path / "aligned_head.ply")
    try:
        mi.repair_aligned(str(tmp_path))
        code = 0
    except SystemExit as e:
        code = e.code or 0
    assert code == 0                       # tunnels left = a successful repair
    assert _digest(tmp_path / "aligned_head.ply") == before     # not rewritten
    assert not (tmp_path / ps.ALIGNED_BACKUP_NAME).exists()
    chk = ps.ProjectStore.for_mesh_dir(str(tmp_path)).read_check(ps.MESH_ALIGNED)
    assert chk == ps.CleanState.CRITICAL   # the tunnel is still reported
    assert _listing(tmp_path) == ["aligned_check.json", "aligned_head.ply"]


def test_repairable_mesh_is_repaired_exit_0_with_one_backup(tmp_path):
    pts, faces = _torus_faces()
    faces = faces[5:]                      # a hole in the surface
    _save(tmp_path / "aligned_head.ply", pts, faces)
    before = _digest(tmp_path / "aligned_head.ply")
    try:
        mi.repair_aligned(str(tmp_path))
        code = 0
    except SystemExit as e:
        code = e.code or 0
    assert code == 0
    backup = tmp_path / ps.ALIGNED_BACKUP_NAME
    assert backup.exists() and _digest(backup) == before     # the pre-repair mesh
    assert _digest(tmp_path / "aligned_head.ply") != before
    rep = mi.inspect_mesh(str(tmp_path / "aligned_head.ply"))
    assert not mi._non_tunnel_criticals(rep)
    assert _listing(tmp_path) == sorted(
        ["aligned_check.json", "aligned_head.ply", ps.ALIGNED_BACKUP_NAME])


def test_existing_backup_is_kept_not_overwritten(tmp_path):
    pts, faces = _torus_faces()
    _save(tmp_path / "aligned_head.ply", pts, faces[5:])
    (tmp_path / ps.ALIGNED_BACKUP_NAME).write_bytes(b"older backup")
    try:
        mi.repair_aligned(str(tmp_path))
    except SystemExit:
        pass
    assert (tmp_path / ps.ALIGNED_BACKUP_NAME).read_bytes() == b"older backup"


def test_failed_repair_exits_1_and_leaves_mesh_and_no_new_backup(tmp_path, monkeypatch):
    pts, faces = _torus_faces()
    _save(tmp_path / "aligned_head.ply", pts, faces[5:])
    before = _digest(tmp_path / "aligned_head.ply")

    def fake_repair(in_path, out_path):
        _save(out_path, pts, faces[5:])          # "repaired" file still broken
        after = mi.inspect_mesh(out_path)
        return {"before": after, "after": after, "success": False, "engine": "fake"}
    monkeypatch.setattr(mi, "repair_mesh", fake_repair)
    assert _run(tmp_path) == 1
    assert _digest(tmp_path / "aligned_head.ply") == before
    assert _listing(tmp_path) == ["aligned_check.json", "aligned_head.ply"]


def test_repair_that_raises_leaves_mesh_untouched(tmp_path, monkeypatch):
    pts, faces = _torus_faces()
    _save(tmp_path / "aligned_head.ply", pts, faces[5:])
    before = _digest(tmp_path / "aligned_head.ply")
    monkeypatch.setattr(mi, "repair_mesh", lambda a, b: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _run(tmp_path) == 1
    assert _digest(tmp_path / "aligned_head.ply") == before
    assert not (tmp_path / ps.ALIGNED_BACKUP_NAME).exists()


def test_missing_mesh_exits_1(tmp_path):
    assert _run(tmp_path) == 1


def test_clean_mesh_exits_0_untouched(tmp_path):
    t = pv.Sphere(radius=50, theta_resolution=40, phi_resolution=40).triangulate().clean()
    pts = np.asarray(t.points, dtype=np.float64)
    faces = t.faces.reshape(-1, 4)[:, 1:].astype(np.int64)
    _save(tmp_path / "aligned_head.ply", pts, faces)
    before = _digest(tmp_path / "aligned_head.ply")
    try:
        mi.repair_aligned(str(tmp_path))
        code = 0
    except SystemExit as e:
        code = e.code or 0
    assert code == 0 and _digest(tmp_path / "aligned_head.ply") == before
