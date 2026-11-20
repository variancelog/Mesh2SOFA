"""Stage 3b tests (all headless): the zoom-to-defect camera looks at the defect from a
direction where it is actually visible (ray-cast occlusion), switches see-through on
by itself when even the best view is mostly hidden, and the result view can tell
which faces are new (the patch caps). The real window is checked by
plans/tunnel-loop/viewer_smoke.py.
"""
from unittest import mock

import numpy as np
import pytest
import pyvista as pv

import mesh_problem_viewer as mpv
from tunnel_loop_locator import select_cut_loop
from test_tunnel_safety import _torus, SCALE, confirm_all


def _poly_arrays(poly):
    poly = poly.triangulate().clean()
    return (np.asarray(poly.points, dtype=np.float64),
            poly.faces.reshape(-1, 4)[:, 1:].astype(np.int64))


def _merge(*meshes):
    """Concatenate (pts, faces) pairs into one (disconnected) mesh."""
    pts, faces, off = [], [], 0
    for p, f in meshes:
        pts.append(p)
        faces.append(f + off)
        off += len(p)
    return np.vstack(pts), np.vstack(faces)


def _meridian_ring(n=24):
    """A ring around the +x tube of the torus (the cross-section circle)."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([(3.0 + np.cos(t)) * SCALE, np.zeros(n), np.sin(t) * SCALE], axis=1)


# ---------------------------------------------------------------------------
# the probe itself
# ---------------------------------------------------------------------------
def test_ray_probe_sees_a_wall():
    wall = _poly_arrays(pv.Plane(center=(0, 0, 0), direction=(1, 0, 0),
                                 i_size=10, j_size=10, i_resolution=4, j_resolution=4))
    probe = mpv.SurfaceRayProbe(*wall)
    assert probe.blocked((-5, 0, 0), (5, 0, 0))
    assert not probe.blocked((-5, 0, 0), (-1, 0, 0))
    assert not probe.blocked((1, 20, 0), (5, 20, 0))      # misses the plate


# ---------------------------------------------------------------------------
# camera choice
# ---------------------------------------------------------------------------
def test_view_avoids_a_wall_in_front_of_the_defect():
    pts, faces = _torus()
    ring = _meridian_ring()
    center = pts.mean(0)
    diag = float(np.linalg.norm(pts.max(0) - pts.min(0)))
    # a big wall just outside the +x tube: the outward camera is behind it
    wall_x = (3.0 + 1.0) * SCALE + 4.0
    wall = _poly_arrays(pv.Plane(center=(wall_x, 0, 0), direction=(1, 0, 0),
                                 i_size=10 * 3 * SCALE, j_size=10 * 3 * SCALE,
                                 i_resolution=6, j_resolution=6))
    mp, mf = _merge((pts, faces), wall)
    probe = mpv.SurfaceRayProbe(mp, mf)
    pose = mpv.choose_defect_view(probe, [ring], center, diag)
    assert pose["outward_frac"] == pytest.approx(1.0)     # outward view is shut off
    assert pose["frac"] < 0.9
    assert pose["pos"][0] < wall_x                         # camera is not behind the wall
    assert float(np.dot(pose["direction"], [1, 0, 0])) < 0.9
    assert not pose["hidden"] or pose["frac"] > mpv.HIDDEN_FRAC


def test_view_keeps_outward_when_it_is_clear():
    # a ring drawn on a flat sheet, the mesh centre far below: outward = +z, clear
    n = 20
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.stack([3 * np.cos(t), 3 * np.sin(t), np.zeros(n)], axis=1)
    sheet = _poly_arrays(pv.Plane(center=(0, 0, 0), direction=(0, 0, 1),
                                  i_size=40, j_size=40, i_resolution=20, j_resolution=20))
    probe = mpv.SurfaceRayProbe(*sheet)
    pose = mpv.choose_defect_view(probe, [ring], np.array([0.0, 0.0, -50.0]), 100.0)
    assert pose["frac"] == pytest.approx(0.0)
    assert np.allclose(pose["direction"], [0, 0, 1], atol=1e-6)    # ties go to outward


def test_view_candidates_include_outward_and_ring_normals():
    ring = _meridian_ring()                                # plane normal = +/- y
    cands = mpv.view_candidates([ring], np.array([1.0, 0.0, 0.0]))
    assert np.allclose(cands[0], [1, 0, 0])
    assert any(np.allclose(c, [0, 1, 0], atol=1e-6) for c in cands)
    assert any(np.allclose(c, [0, -1, 0], atol=1e-6) for c in cands)
    assert all(abs(np.linalg.norm(c) - 1) < 1e-9 for c in cands)
    assert len(cands) > 20


class _FractionProbe:
    """Blocks a ring ray exactly when the ray's end point has x < thr, so the blocked
    fraction does not depend on the camera; far 'inside' probes are never blocked."""

    def __init__(self, thr):
        self.thr = thr

    def blocked(self, a, b):
        if np.linalg.norm(np.asarray(b) - np.asarray(a)) > 1000:
            return False
        return bool(b[0] < self.thr)


def _unit_ring(n=20):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([np.cos(t), np.sin(t), np.zeros(n)], axis=1)


@pytest.mark.parametrize("thr,hidden", [
    (-0.45, False),     # 5 of 20 points have x < -0.45 or so: well under half
    (0.99, True),       # nearly all blocked: hidden
])
def test_hidden_threshold(thr, hidden):
    pose = mpv.choose_defect_view(_FractionProbe(thr), [_unit_ring()],
                                  np.array([0.0, 0.0, -5.0]), 1000.0)
    assert pose["hidden"] is hidden
    assert (pose["frac"] > mpv.HIDDEN_FRAC) is hidden


def test_hidden_is_strictly_more_than_half():
    ring = _unit_ring(20)
    xs = np.sort(ring[:, 0])
    # block exactly 10 of 20 (50%): not "more than 50%"
    thr = (xs[9] + xs[10]) / 2
    assert int((ring[:, 0] < thr).sum()) == 10
    pose = mpv.choose_defect_view(_FractionProbe(thr), [ring],
                                  np.array([0.0, 0.0, -5.0]), 1000.0)
    assert pose["frac"] == pytest.approx(0.5, abs=0.1)
    # the exact 50% case must not trip the rule
    assert mpv.HIDDEN_FRAC == 0.5 and not (0.5 > mpv.HIDDEN_FRAC)


# ---------------------------------------------------------------------------
# selector: pose is cached, applied, and drives the automatic see-through
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def torus_pair():
    pts, faces = _torus()
    pair = select_cut_loop(pts, faces, 1)[0]
    return pts, faces, pair


def _selector(torus_pair, **kw):
    pts, faces, pair = torus_pair
    pl = mock.MagicMock()
    sel = mpv.TunnelSelector(pl, pts, [pair], faces=faces, genus=1, **kw)
    return sel, pl


def test_pose_is_cached_and_used_by_the_camera(torus_pair):
    sel, pl = _selector(torus_pair)
    sel.zoom_to_defect()
    first = sel.last_pose_ms
    pos = pl.camera_position[0]
    assert np.allclose(pos, sel.defect_pose()["pos"])
    sel.last_pose_ms = None
    sel.zoom_to_defect()                                  # cached: no new search
    assert sel.last_pose_ms is None
    assert first is not None and first < 3000


def test_hidden_defect_turns_see_through_on_with_a_note(torus_pair):
    sel, pl = _selector(torus_pair)
    hidden = dict(sel.defect_pose(), hidden=True, frac=0.9)
    sel._poses[0] = hidden
    sel.show_defect()
    assert sel.see_through and sel._auto_see
    assert mpv.HIDDEN_NOTE in sel.notes()
    assert "hidden behind other parts of the ear" in mpv.HIDDEN_NOTE


def test_visible_defect_leaves_see_through_alone(torus_pair):
    sel, pl = _selector(torus_pair)
    sel._poses[0] = dict(sel.defect_pose(), hidden=False, frac=0.1)
    sel.show_defect()
    assert not sel.see_through
    assert mpv.HIDDEN_NOTE not in sel.notes()


def test_user_choice_is_not_overridden(torus_pair):
    sel, pl = _selector(torus_pair)
    sel._poses[0] = dict(sel.defect_pose(), hidden=False)
    sel.set_see_through(True)                             # the user's own choice
    sel.show_defect()
    assert sel.see_through and not sel._auto_see          # not switched back off
    assert mpv.HIDDEN_NOTE not in sel.notes()


def test_auto_see_through_is_undone_for_a_visible_defect(torus_pair):
    sel, pl = _selector(torus_pair)
    sel._poses[0] = dict(sel.defect_pose(), hidden=True)
    sel.show_defect()
    assert sel.see_through
    sel._poses[0] = dict(sel._poses[0], hidden=False)
    sel.show_defect()
    assert not sel.see_through


def test_view_search_failure_falls_back_to_outward(torus_pair):
    sel, pl = _selector(torus_pair)
    with mock.patch.object(mpv, "choose_defect_view", side_effect=RuntimeError("boom")):
        pose = sel.defect_pose()
    assert pose["hidden"] is False
    pos, focal, _up, _d = mpv.defect_camera_pose(
        [sel.pts[lp] for lp in sel.handles[0]["loops"]], sel.center, sel.diag)
    assert np.allclose(pose["pos"], pos)


def test_same_pose_for_before_after_toggle(torus_pair):
    """The Before/After toggle never moves the camera (pose chosen once)."""
    sel, pl = _selector(torus_pair, previews=True)
    sel.zoom_to_defect()
    pl.reset_mock()
    sel.previews.run()
    sel.view = "after"
    sel._apply_view()
    assert not any("camera_position" in str(c) for c in pl.mock_calls)


# ---------------------------------------------------------------------------
# changed-face detection
# ---------------------------------------------------------------------------
def test_changed_faces_none_for_identical_mesh(torus_pair):
    pts, faces, _ = torus_pair
    det = mpv.ChangedFaceDetector(pts, faces)
    assert not det.mask(pts, faces).any()
    assert not det.mask(pts, faces[::-1]).any()           # order does not matter


def test_changed_faces_flags_a_new_face(torus_pair):
    pts, faces, _ = torus_pair
    new_pts = np.vstack([pts, pts[faces[0]].mean(0) + 5.0])
    new_faces = np.vstack([faces, [faces[0][0], faces[0][1], len(pts)]])
    mask = mpv.ChangedFaceDetector(pts, faces).mask(new_pts, new_faces)
    assert mask[-1] and mask[:-1].sum() == 0


def test_changed_faces_after_a_real_cut_are_the_patch(torus_pair):
    from tunnel_loop_extractor import cut_and_patch
    pts, faces, pair = torus_pair
    rp, rf = cut_and_patch(pts, faces, pair["cut"])
    mask = mpv.ChangedFaceDetector(pts, faces).mask(rp, rf)
    n = int(mask.sum())
    # (the synthetic torus is so coarse that the cap remesh at ~1.5 mm re-tessellates
    # most of it; on a real head the patch is a few hundred faces of ~75k)
    assert 0 < n < len(rf)
    assert (~mask).sum() > 0.1 * len(rf)                  # untouched faces stay unflagged
    cen = np.asarray(rp)[np.asarray(rf)[mask]].mean(1)
    d = np.linalg.norm(cen[:, None, :] - pts[pair["cut"]][None, :, :], axis=2).min(1)
    ring_span = np.linalg.norm(pts[pair["cut"]].max(0) - pts[pair["cut"]].min(0))
    assert d.min() < ring_span                            # the flagged region is at the cut


def test_preview_cache_stores_the_mask(torus_pair):
    pts, faces, pair = torus_pair
    cache = mpv.PreviewCache(pts, faces, {(0, 0): pair["cut"]})
    cache.run()
    assert cache.state((0, 0))[0] == "ready"
    mask = cache.changed_mask((0, 0))
    assert mask is not None and mask.any()
    assert len(mask) == len(cache.result((0, 0))[1])


def test_after_view_colours_the_new_surface_and_shows_the_legend(torus_pair, tmp_path):
    sel, pl = _selector(torus_pair, previews=True)
    # enable Apply; Undo appends to cutpatch_history.json beside the mesh, so keep it in tmp
    sel.mesh_path = str(tmp_path / "dummy.ply")
    confirm_all(sel)
    sel.request_apply()                                  # inline job runner
    assert sel.mode == "after" and sel.result_mask is not None
    assert sel.new_surface_legend() == mpv.GREEN_LEGEND
    mesh = sel._surface_cache["fix"]
    assert mpv.CHANGED_ARRAY in mesh.cell_data and mesh.cell_data[mpv.CHANGED_ARRAY].any()
    kws = [c.kwargs for c in pl.add_mesh.call_args_list if c.kwargs.get("name") == "result_surface"]
    assert kws and kws[-1].get("scalars") == mpv.CHANGED_ARRAY
    assert mpv.COLOR_NEW_SURFACE in kws[-1]["cmap"]
    sel.view = "before"
    assert sel.new_surface_legend() is None
    sel.view = "after"
    sel.undo()
    assert sel.result_mask is None and sel.new_surface_legend() is None


def test_label_leaders_are_neutral_grey(torus_pair):
    sel, pl = _selector(torus_pair)
    leaders = [c for c in pl.add_mesh.call_args_list
               if str(c.kwargs.get("name", "")).find("_leader_") >= 0]
    assert leaders
    assert all(c.kwargs["color"] == mpv.COLOR_LEADER and c.kwargs["line_width"] == 1
               for c in leaders)
    assert mpv.COLOR_LEADER not in (mpv.COLOR_CUT_ACTIVE, mpv.COLOR_IGNORE_ACTIVE)
