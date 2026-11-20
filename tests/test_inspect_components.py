"""Separate pieces must be reported as critical (stage 5d).

Regression: PyMeshLab builds name the topological keys differently
(connected_components_number), so inspect_mesh reported components=0 / ok for a
mesh in two pieces. components is now also computed from the face array.
All meshes are written under tmp_path.
"""
import numpy as np
import pytest

pv = pytest.importorskip("pyvista")
pytest.importorskip("pymeshlab")

import inspect_flow
import mesh_inspector as mi


def _sphere(radius, centre, res=24):
    return pv.Sphere(radius=radius, center=centre, theta_resolution=res, phi_resolution=res)


def _save(mesh, path):
    mesh.extract_surface().triangulate().save(str(path))
    return str(path)


@pytest.fixture
def one_sphere(tmp_path):
    return _save(_sphere(50, (0, 0, 0)), tmp_path / "one.ply")


@pytest.fixture
def two_spheres(tmp_path):
    m = _sphere(50, (0, 0, 0)).merge(_sphere(50, (200, 0, 0)))
    return _save(m, tmp_path / "two.ply")


@pytest.fixture
def sphere_plus_speck(tmp_path):
    m = _sphere(50, (0, 0, 0), res=48).merge(_sphere(2, (100, 0, 0), res=5))
    return _save(m, tmp_path / "speck.ply")


def test_count_components_helper():
    f = np.array([[0, 1, 2], [2, 3, 4], [10, 11, 12]])
    assert mi.count_components(f) == 2          # 0-1-2-3-4 chain, and 10-11-12
    assert mi.count_components(np.zeros((0, 3), dtype=int)) == 0


def test_single_sphere_is_one_piece(one_sphere):
    r = mi.inspect_mesh(one_sphere)
    assert r["counts"]["components"] == 1
    assert r["severity"] == "ok"


def test_two_spheres_critical(two_spheres):
    r = mi.inspect_mesh(two_spheres)
    assert r["counts"]["components"] == 2
    assert r["severity"] == "critical"
    assert any("disconnected" in c["issue"] for c in r["critical"])
    check = {"aligned": {"severity": r["severity"], "counts": r["counts"]}}
    assert r["counts"]["large_pieces"] == 2
    sentences = inspect_flow.describe(check)
    assert sentences[0] == "The mesh is in 2 separate pieces."
    assert "remove the unwanted piece in Blender" in sentences[1]
    assert inspect_flow.next_action(check, False) == "repair"
    assert inspect_flow.next_action(check, True) == "stuck"


def test_sphere_plus_tiny_piece_critical(sphere_plus_speck):
    r = mi.inspect_mesh(sphere_plus_speck)
    assert r["counts"]["components"] == 2
    assert r["severity"] == "critical"


def test_components_survive_a_failing_topology_call(two_spheres, monkeypatch):
    """An exception in get_topological_measures must not zero the count."""
    import pymeshlab

    def boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(pymeshlab.MeshSet, "get_topological_measures", boom)
    r = mi.inspect_mesh(two_spheres)
    assert r["counts"]["components"] == 2
    assert r["severity"] == "critical"
    assert r["measure_errors"] and any("topological" in m for m in r["minor"][0]["issue"].split(";"))


def test_repair_drops_small_debris(sphere_plus_speck, tmp_path):
    """A piece under 1 % of the faces is debris: pymeshfix drops it."""
    pytest.importorskip("pymeshfix")
    res = mi.repair_mesh(sphere_plus_speck, str(tmp_path / "rep.ply"))
    assert res["before"]["counts"]["components"] == 2
    assert res["before"]["counts"]["large_pieces"] == 1
    assert res["after"]["counts"]["components"] == 1
    assert res["success"] is True
    assert res["dropped_pieces"] == 1


def test_repair_refuses_to_delete_a_large_piece(two_spheres, tmp_path):
    out = tmp_path / "rep.ply"
    res = mi.repair_mesh(two_spheres, str(out))
    assert res["success"] is False
    assert "2 large separate pieces" in res["message"]
    assert "Blender" in res["message"]
    assert not out.exists()


def test_repair_aligned_leaves_two_spheres_untouched_and_flow_is_stuck(
        two_spheres, tmp_path, capsys):
    import hashlib
    import json
    import shutil
    d = tmp_path / "Meshes"
    d.mkdir()
    dest = d / "aligned_head.ply"
    shutil.copy(two_spheres, dest)
    before = hashlib.sha256(dest.read_bytes()).hexdigest()
    with pytest.raises(SystemExit) as ei:
        mi.repair_aligned(str(d))
    assert ei.value.code == 1
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == before
    assert "large separate pieces" in capsys.readouterr().out
    assert sorted(x.name for x in d.iterdir()) == ["aligned_check.json", "aligned_head.ply"]
    check = json.loads((d / "aligned_check.json").read_text())
    assert inspect_flow.next_action(check, True) == "stuck"
    assert any("Blender" in s for s in inspect_flow.describe(check))
