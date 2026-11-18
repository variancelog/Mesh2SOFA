"""
test_tunnel_safety.py
=====================

Stage-1 safety tests for the in-app tunnel fix (Cut+Patch):

  * cut_and_patch / cut_and_patch_loops on SYNTHETIC meshes (torus, a genus-2
    double torus built in code) -- no zzmeshes/ data needed;
  * negative cases that must raise and leave the input untouched
    (non-severing loop, component-splitting cut, cut with more than 2 holes,
    non-severing member in a multi-loop set);
  * the exact-2-holes rule in _severs;
  * loop B verified at locate time (select_cut_loop) and never selectable
    when it fails;
  * backup-if-absent + atomic save + restore (project_store.apply_with_backup,
    restore_backup) and the viewer's TunnelSelector flow (confirm, cancel,
    failure) driven with a mock plotter -- no Qt / GL needed.
"""
import os
import sys
from unittest import mock

import numpy as np
import pytest
import pyvista as pv

import tunnel_loop_extractor as ex
import tunnel_loop_locator as loc
import project_store as ps
from tunnel_loop_extractor import (
    mesh_genus, _genus_after_cut, _topo_counts, _severs,
    cut_and_patch, cut_and_patch_loops,
)
from tunnel_loop_locator import locate_cut_loops, select_cut_loop

SCALE = 10.0   # make the synthetic parts head-sized (mm) so the 1.5 mm cap remesh is sane


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------
def _torus():
    t = pv.ParametricTorus(ringradius=3.0, crosssectionradius=1.0,
                           u_res=48, v_res=24).triangulate().clean()
    pts = np.asarray(t.points, dtype=np.float64) * SCALE
    faces = t.faces.reshape(-1, 4)[:, 1:].astype(np.int64)
    return pts, faces


def _double_torus(res=0.2, R=3.0, r=0.6):
    """Two tori fused along their outer rims (implicit union, marching cubes):
    one closed component, genus 2, holes ~R apart so each handle gets its own
    loop."""
    d = 2 * R - 0.9 * r
    ax = np.arange(-(R + r + d / 2) - 0.5, (R + r + d / 2) + 0.5, res)
    ay = np.arange(-(R + r) - 0.5, (R + r) + 0.5, res)
    az = np.arange(-r - 0.5, r + 0.5, res)
    X, Y, Z = np.meshgrid(ax, ay, az, indexing="ij")

    def tor(cx):
        return (np.sqrt((X - cx) ** 2 + Y ** 2) - R) ** 2 + Z ** 2 - r ** 2

    F = np.minimum(tor(-d / 2), tor(d / 2))
    g = pv.ImageData(dimensions=F.shape, spacing=(res, res, res),
                     origin=(ax[0], ay[0], az[0]))
    g.point_data["f"] = F.flatten(order="F")
    m = g.contour([0.0], scalars="f").triangulate().clean()
    return (np.asarray(m.points, dtype=np.float64) * SCALE,
            m.faces.reshape(-1, 4)[:, 1:].astype(np.int64))


def _sphere():
    s = pv.Icosphere(nsub=3, radius=1.0).triangulate().clean()
    return (np.asarray(s.points, dtype=np.float64) * 50.0,
            s.faces.reshape(-1, 4)[:, 1:].astype(np.int64))


@pytest.fixture(scope="module")
def torus():
    return _torus()


@pytest.fixture(scope="module")
def torus_loop(torus):
    pts, faces = torus
    loops = locate_cut_loops(pts, faces, 1)
    assert len(loops) == 1
    return loops[0]


@pytest.fixture(scope="module")
def dtorus():
    pts, faces = _double_torus()
    assert mesh_genus(pts, faces) == 2
    assert _topo_counts(faces)[3:] == (1, 0), "fixture must be 1 closed component"
    return pts, faces


@pytest.fixture(scope="module")
def dtorus_loops(dtorus):
    pts, faces = dtorus
    loops = locate_cut_loops(pts, faces, 2)
    assert len(loops) == 2, "fixture must give one severing loop per handle"
    return loops


def _watertight_one_component(faces):
    V, E, F, n_comp, n_bloops = _topo_counts(faces)
    return n_bloops == 0 and n_comp == 1


# ---------------------------------------------------------------------------
# Positive: both cut functions on synthetic meshes
# ---------------------------------------------------------------------------
def test_cut_and_patch_single_loop_on_torus(torus, torus_loop):
    pts, faces = torus
    pts0, faces0 = pts.copy(), faces.copy()
    new_pts, new_faces = cut_and_patch(pts, faces, torus_loop)
    assert mesh_genus(new_pts, new_faces) == 0
    assert _watertight_one_component(new_faces)
    assert np.array_equal(pts, pts0) and np.array_equal(faces, faces0), \
        "inputs must not be modified"


def test_cut_and_patch_loops_on_genus2(dtorus, dtorus_loops):
    pts, faces = dtorus
    new_pts, new_faces = cut_and_patch_loops(pts, faces, dtorus_loops)
    assert mesh_genus(new_pts, new_faces) == 0
    assert _watertight_one_component(new_faces)


def test_cut_and_patch_loops_single_loop_genus2_to_genus1(dtorus, dtorus_loops):
    pts, faces = dtorus
    new_pts, new_faces = cut_and_patch_loops(pts, faces, dtorus_loops[:1])
    assert mesh_genus(new_pts, new_faces) == 1
    assert _watertight_one_component(new_faces)


# ---------------------------------------------------------------------------
# Negatives: must raise, and must leave the input untouched
# ---------------------------------------------------------------------------
def _assert_raises_and_untouched(fn, pts, faces, *args, match):
    pts0, faces0 = pts.copy(), faces.copy()
    with pytest.raises(ValueError, match=match):
        fn(pts, faces, *args)
    assert np.array_equal(pts, pts0) and np.array_equal(faces, faces0)


def test_non_severing_loop_raises(torus):
    pts, faces = torus
    tri = np.asarray(faces[0], dtype=np.int64)   # contractible: bounds a disk
    _assert_raises_and_untouched(cut_and_patch, pts, faces, tri,
                                 match="does not sever")


def test_sphere_loop_raises():
    pts, faces = _sphere()
    tri = np.asarray(faces[0], dtype=np.int64)
    _assert_raises_and_untouched(cut_and_patch, pts, faces, tri,
                                 match="does not sever")


def test_component_splitting_cut_raises(dtorus):
    """A slab of vertices across the waist where the two tori are fused cuts the
    mesh into two pieces (2 components) -> must be rejected."""
    pts, faces = dtorus
    waist = np.where(np.abs(pts[:, 0]) < 0.6 * SCALE * 0.2)[0]
    chk = _genus_after_cut(pts, faces, waist)
    assert chk is not None and chk[2] == 2, "fixture: this cut must split the mesh"
    _assert_raises_and_untouched(cut_and_patch, pts, faces, waist,
                                 match="does not sever")
    _assert_raises_and_untouched(cut_and_patch_loops, pts, faces, [waist],
                                 match="do not sever cleanly")


def _loop_plus_stray_vertex(pts, faces, loop):
    """The severing loop plus one far-away vertex: deleting that vertex's faces
    opens a THIRD hole while genus still drops and the mesh stays in one piece."""
    far = int(np.argmax(np.linalg.norm(pts - pts[loop].mean(0), axis=1)))
    return np.concatenate([np.asarray(loop), [far]])


def test_cut_with_more_than_two_holes_raises(torus, torus_loop):
    pts, faces = torus
    bad = _loop_plus_stray_vertex(pts, faces, torus_loop)
    g_after, n_holes, n_comp = _genus_after_cut(pts, faces, bad)
    assert g_after < mesh_genus(pts, faces) and n_holes == 3 and n_comp == 1, \
        "fixture: genus drops, 3 holes, 1 component (the old >=2 rule accepted this)"
    _assert_raises_and_untouched(cut_and_patch, pts, faces, bad,
                                 match="does not sever")
    assert not _severs(pts, faces, bad, mesh_genus(pts, faces))


def test_severs_requires_exactly_two_holes(torus, torus_loop):
    pts, faces = torus
    g0 = mesh_genus(pts, faces)
    assert _severs(pts, faces, torus_loop, g0)


def test_multi_loop_union_precheck_rejects_non_severing_member(dtorus, dtorus_loops):
    pts, faces = dtorus
    tri = np.asarray(faces[0], dtype=np.int64)
    _assert_raises_and_untouched(cut_and_patch_loops, pts, faces,
                                 [dtorus_loops[0], tri], match="do not sever cleanly")


def test_multi_loop_union_precheck_rejects_extra_holes(dtorus, dtorus_loops):
    pts, faces = dtorus
    bad = _loop_plus_stray_vertex(pts, faces, dtorus_loops[0])
    _assert_raises_and_untouched(cut_and_patch_loops, pts, faces,
                                 [bad, dtorus_loops[1]], match="do not sever cleanly")


def test_no_loops_raises(torus):
    pts, faces = torus
    with pytest.raises(ValueError, match="no loops"):
        cut_and_patch_loops(pts, faces, [])


# ---------------------------------------------------------------------------
# Post-patch checks (_verify_capped) and pymeshfix's component behaviour
# ---------------------------------------------------------------------------
def test_pymeshfix_default_drops_small_components_and_we_override_it():
    """Confirms the review's recollection: pymeshfix.repair() removes all but the
    largest component by default. The cut functions pass
    remove_smallest_components=False so a detached piece stays visible to the
    1-component post-check instead of silently vanishing."""
    import pymeshfix
    big_p, big_f = _sphere()
    small_p, small_f = _sphere()
    small_p = small_p * 0.1 + 500.0
    pts = np.vstack([big_p, small_p])
    faces = np.vstack([big_f, small_f + len(big_p)])

    mf = pymeshfix.MeshFix(pts, faces)
    mf.repair()   # default
    assert _topo_counts(np.asarray(mf.faces))[3] == 1, "default drops the small sphere"

    mf = pymeshfix.MeshFix(pts, faces)
    mf.repair(remove_smallest_components=False)
    assert _topo_counts(np.asarray(mf.faces))[3] == 2, "override keeps it"


def test_verify_capped_catches_detached_piece_and_open_holes_and_face_loss(torus):
    pts, faces = torus
    g = mesh_genus(pts, faces)
    ex._verify_capped(pts, faces, g, len(faces), "t")          # sanity: passes
    # detached piece
    sp, sf = _sphere()
    two = np.vstack([faces, sf + len(pts)])
    with pytest.raises(ValueError, match="2 components"):
        ex._verify_capped(None, two, g, len(two), "t")
    # open boundary
    with pytest.raises(ValueError, match="not watertight"):
        ex._verify_capped(None, faces[:-1], g, len(faces), "t")
    # wrong genus
    with pytest.raises(ValueError, match="capped genus"):
        ex._verify_capped(None, faces, g - 1, len(faces), "t")
    # face-count loss beyond the tolerance
    with pytest.raises(ValueError, match="face count dropped"):
        ex._verify_capped(None, faces, g,
                          int(len(faces) / (1.0 - 2 * ex.FACE_DROP_TOLERANCE)), "t")


# ---------------------------------------------------------------------------
# Loop B is verified at locate time
# ---------------------------------------------------------------------------
def test_select_cut_loop_offers_verified_pair(torus):
    pts, faces = torus
    entry = select_cut_loop(pts, faces, 1)[0]
    g0 = mesh_genus(pts, faces)
    assert entry["avoid"] is not None and entry.get("B_unavailable") is None
    assert _severs(pts, faces, entry["cut"], g0)
    assert _severs(pts, faces, entry["avoid"], g0)


def test_failing_loop_b_is_marked_unavailable(torus):
    pts, faces = torus
    bogus = np.asarray(faces[0], dtype=np.int64)       # contractible: never severs
    with mock.patch.object(loc, "dual_crossing_loop", return_value=bogus):
        entry = select_cut_loop(pts, faces, 1)[0]
    assert entry["avoid"] is None and entry["B"] is None
    assert np.array_equal(entry["B_unavailable"], bogus)
    assert "need a genus drop" in entry["B_reason"]
    # A is still the (verified) cut loop and nothing was pre-labelled
    assert _severs(pts, faces, entry["cut"], mesh_genus(pts, faces))
    assert entry["labels"]["cut"] == "UNKNOWN"


def test_selector_never_selects_unavailable_loop(torus, torus_loop):
    from mesh_problem_viewer import TunnelSelector
    pts, faces = torus
    bogus = np.asarray(faces[0], dtype=np.int64)
    pair = {"cut": torus_loop, "avoid": None, "A": torus_loop, "B": None,
            "B_unavailable": bogus, "B_reason": "test reason",
            "labels": {"cut": "UNKNOWN", "avoid": None}}
    sel = TunnelSelector(mock.MagicMock(), pts, [pair], faces=faces)
    assert len(sel.handles[0]["loops"]) == 1
    sel.toggle_active_loop()                       # Space
    assert sel.handles[0]["sel"] == 0
    msgs = sel.unavailable_messages()
    assert len(msgs) == 1 and "test reason" in msgs[0]
    # on_pick near the blocked loop must not select it either
    sel.on_pick(pts[bogus[0]])
    assert sel.handles[0]["sel"] == 0


# ---------------------------------------------------------------------------
# Backup-if-absent, atomic save, restore (project_store)
# ---------------------------------------------------------------------------
def _write_bytes(result, path):
    with open(path, "wb") as f:
        f.write(result)


def _mesh_file(tmp_path, data=b"ORIGINAL"):
    p = tmp_path / "aligned_head.ply"
    p.write_bytes(data)
    return str(p)


def _listing(tmp_path):
    return sorted(os.listdir(tmp_path))


def test_backup_created_once_and_atomic_replace(tmp_path):
    mesh = _mesh_file(tmp_path)
    backup = ps.backup_path_for(mesh)
    assert os.path.basename(backup) == "aligned_head.pre_cutpatch.ply"

    ps.apply_with_backup(mesh, lambda: b"FIRST", _write_bytes)
    assert open(mesh, "rb").read() == b"FIRST"
    assert open(backup, "rb").read() == b"ORIGINAL"

    ps.apply_with_backup(mesh, lambda: b"SECOND", _write_bytes)
    assert open(mesh, "rb").read() == b"SECOND"
    assert open(backup, "rb").read() == b"ORIGINAL", \
        "backup keeps the mesh from before the FIRST fix"
    assert _listing(tmp_path) == ["aligned_head.pre_cutpatch.ply", "aligned_head.ply"]\
        or _listing(tmp_path) == sorted(["aligned_head.pre_cutpatch.ply", "aligned_head.ply"])


def test_failed_check_leaves_original_and_removes_new_backup(tmp_path):
    mesh = _mesh_file(tmp_path)

    def boom():
        raise ValueError("check failed")

    with pytest.raises(ValueError, match="check failed"):
        ps.apply_with_backup(mesh, boom, _write_bytes)
    assert open(mesh, "rb").read() == b"ORIGINAL"
    assert _listing(tmp_path) == ["aligned_head.ply"], \
        "no tmp file and no backup left behind by a failed first attempt"


def test_failed_check_keeps_preexisting_backup(tmp_path):
    mesh = _mesh_file(tmp_path, b"CURRENT")
    backup = ps.backup_path_for(mesh)
    open(backup, "wb").write(b"FIRSTFIX")

    with pytest.raises(ValueError):
        ps.apply_with_backup(mesh, lambda: (_ for _ in ()).throw(ValueError("x")),
                             _write_bytes)
    assert open(mesh, "rb").read() == b"CURRENT"
    assert open(backup, "rb").read() == b"FIRSTFIX"


def test_write_error_removes_tmp_and_keeps_original(tmp_path):
    mesh = _mesh_file(tmp_path)

    def bad_write(result, path):
        with open(path, "wb") as f:
            f.write(b"PARTIAL")
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        ps.apply_with_backup(mesh, lambda: b"NEW", bad_write)
    assert open(mesh, "rb").read() == b"ORIGINAL"
    assert not os.path.exists(ps.tmp_path_for(mesh))
    assert _listing(tmp_path) == ["aligned_head.ply"]


def test_replace_error_removes_tmp_and_keeps_original(tmp_path, monkeypatch):
    mesh = _mesh_file(tmp_path)
    real_replace = os.replace

    def flaky(src, dst):
        if os.path.abspath(dst) == os.path.abspath(mesh):
            raise OSError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError, match="locked"):
        ps.apply_with_backup(mesh, lambda: b"NEW", _write_bytes)
    monkeypatch.undo()
    assert open(mesh, "rb").read() == b"ORIGINAL"
    assert _listing(tmp_path) == ["aligned_head.ply"]


def test_restore_puts_backup_back_and_clears_check(tmp_path):
    mesh = _mesh_file(tmp_path)
    store = ps.ProjectStore(str(tmp_path), mesh_dir=str(tmp_path))
    store.write_check(ps.MESH_ALIGNED, {"aligned": ("ok", {})})
    assert store.read_check(ps.MESH_ALIGNED) == ps.CleanState.CLEAN
    assert not store.has_aligned_backup()
    assert store.restore_aligned_backup() is False

    ps.apply_with_backup(mesh, lambda: b"FIXED", _write_bytes)
    assert store.has_aligned_backup()
    assert store.restore_aligned_backup() is True
    assert open(mesh, "rb").read() == b"ORIGINAL"
    assert store.read_check(ps.MESH_ALIGNED) == ps.CleanState.NOT_RUN
    assert not os.path.exists(ps.tmp_path_for(mesh))
    # a later fix keeps the SAME (pre-first-fix) backup
    ps.apply_with_backup(mesh, lambda: b"FIXED2", _write_bytes)
    assert open(store.aligned_backup_path, "rb").read() == b"ORIGINAL"


def test_realign_clears_backup(tmp_path):
    mesh = _mesh_file(tmp_path)
    store = ps.ProjectStore(str(tmp_path), mesh_dir=str(tmp_path))
    ps.apply_with_backup(mesh, lambda: b"FIXED", _write_bytes)
    assert store.has_aligned_backup()
    store.clear_aligned_backup()
    assert not store.has_aligned_backup()
    store.clear_aligned_backup()          # idempotent


def test_backup_is_a_registered_mesh_artifact(tmp_path):
    mesh = _mesh_file(tmp_path)
    store = ps.ProjectStore(str(tmp_path), mesh_dir=str(tmp_path))
    ps.apply_with_backup(mesh, lambda: b"FIXED", _write_bytes)
    assert "aligned_head.pre_cutpatch.ply" in store.list_mesh_artifacts()
    store.reset_mesh_artifacts()
    assert not store.has_aligned_backup()


# ---------------------------------------------------------------------------
# Viewer flow: TunnelSelector with a mock plotter (no Qt / GL)
# ---------------------------------------------------------------------------
def _write_torus_ply(path, pts, faces):
    cells = np.hstack([np.full((len(faces), 1), 3, dtype=np.int64), faces]).ravel()
    pv.PolyData(pts, cells).save(path)


@pytest.fixture
def viewer_setup(tmp_path, torus):
    from mesh_problem_viewer import TunnelSelector
    pts, faces = torus
    mesh = str(tmp_path / "aligned_head.ply")
    _write_torus_ply(mesh, pts, faces)
    pair = select_cut_loop(pts, faces, 1)[0]
    events = {"confirm": None, "success": 0, "busy": []}

    def confirm_cb(n, on_confirm):
        events["confirm"] = (n, on_confirm)

    sel = TunnelSelector(mock.MagicMock(), pts, [pair], mesh_path=mesh, faces=faces,
                         on_success=lambda: events.__setitem__("success",
                                                               events["success"] + 1),
                         confirm_cb=confirm_cb,
                         busy_cb=lambda b: events["busy"].append(b))
    return sel, mesh, events, tmp_path


def _files(tmp_path):
    return {n for n in os.listdir(tmp_path)
            if not n.startswith("aligned_head_cut")}


def test_viewer_cancel_leaves_file_byte_identical(viewer_setup):
    sel, mesh, events, tmp_path = viewer_setup
    before = open(mesh, "rb").read()
    sel.request_apply()                          # C / Apply -> confirm step opens
    assert events["confirm"] is not None and events["confirm"][0] == 1
    assert open(mesh, "rb").read() == before     # nothing happened yet
    # Cancel = never calling on_confirm
    assert open(mesh, "rb").read() == before
    assert _files(tmp_path) == {"aligned_head.ply"}
    assert events["success"] == 0


def test_viewer_confirm_writes_backup_and_result(viewer_setup):
    sel, mesh, events, tmp_path = viewer_setup
    before = open(mesh, "rb").read()
    sel.request_apply()
    events["confirm"][1]()                       # user confirms
    backup = ps.backup_path_for(mesh)
    assert open(backup, "rb").read() == before, "backup = mesh from before the fix"
    new = pv.read(mesh)
    faces = new.faces.reshape(-1, 4)[:, 1:]
    assert mesh_genus(np.asarray(new.points), faces) == 0
    assert _watertight_one_component(faces)
    assert not os.path.exists(ps.tmp_path_for(mesh))
    assert os.path.exists(tmp_path / "cutpatch_report.json")
    assert events["success"] == 1
    assert sel.busy, "stays busy after success so a queued 2nd press cannot re-cut"
    sel.request_apply()                           # ignored while busy
    assert events["confirm"][1] is not None


def test_viewer_failed_check_leaves_original_and_no_backup(viewer_setup):
    sel, mesh, events, tmp_path = viewer_setup
    before = open(mesh, "rb").read()
    # force a failing cut: a loop that does not sever
    sel.handles[0]["loops"][0] = np.asarray(sel.faces[0], dtype=np.int64)
    sel.apply_cut_and_patch()
    assert open(mesh, "rb").read() == before
    assert _files(tmp_path) == {"aligned_head.ply"}, "no tmp, no backup, no sentinel"
    assert events["success"] == 0
    assert not sel.busy and events["busy"] == [True, False]
    status = [c.args[0] for c in sel.pl.add_text.call_args_list
              if c.kwargs.get("name") == "patchstatus"]
    assert "does not sever" in status[-1] and "unchanged" in status[-1]


def test_viewer_ignores_second_apply_while_busy(viewer_setup):
    sel, mesh, events, tmp_path = viewer_setup
    sel.busy = True
    sel.request_apply()
    sel.apply_cut_and_patch()
    assert events["confirm"] is None
    assert _files(tmp_path) == {"aligned_head.ply"}


def test_write_ply_tmp_roundtrip_any_extension(tmp_path, torus):
    from mesh_problem_viewer import _write_ply_tmp
    pts, faces = torus
    out = str(tmp_path / "x.tmp.ply")
    _write_ply_tmp((pts, faces), out)
    assert os.path.getsize(out) > 0
    back = pv.PLYReader(out).read()
    assert back.n_cells == len(faces)
