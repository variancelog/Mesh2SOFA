"""Shared plain helpers for the test suite (no fixtures here: the session-scoped
fixtures built from these live in conftest.py). Test modules must import from
here, never from each other."""
import functools
import importlib.util
import os
import sys
import types
from unittest import mock

import numpy as np
import pyvista as pv

import mesh_problem_viewer as mpv

SCALE = 10.0   # make the synthetic parts head-sized (mm) so the 1.5 mm cap remesh is sane

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON_PATH = os.path.join(_REPO_ROOT, "blender_scripts", "mesh2SOFA_blender_addon.py")


def confirm_all(sel):
    """Test helper: mark every defect of a TunnelSelector as confirmed (Apply
    needs the user's confirmation of each defect since stage 6a)."""
    for h in sel.handles:
        h["confirmed"] = True


def torus_mesh():
    t = pv.ParametricTorus(ringradius=3.0, crosssectionradius=1.0,
                           u_res=48, v_res=24).triangulate().clean()
    pts = np.asarray(t.points, dtype=np.float64) * SCALE
    faces = t.faces.reshape(-1, 4)[:, 1:].astype(np.int64)
    return pts, faces


def double_torus_mesh(res=0.2, R=3.0, r=0.6):
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


def _write_ply(path, pts, faces):
    cells = np.hstack([np.full((len(faces), 1), 3, dtype=np.int64), faces]).ravel()
    pv.PolyData(pts, cells).save(path)


def _inline_runner(sel):
    """preview_runner that runs the whole preview loop inline (what the window's
    QThread does, minus the thread)."""
    def runner(run_fn):
        run_fn(sel.on_preview_update)
    return runner


def make_selector(tmp_path, torus, pair, runner=True, long_guard=False, **kw):
    """long_guard=False switches the stage-5d long-ring warning off: the synthetic
    torus has rings longer than 25 % of its tiny bbox, which is not what the
    other tests are about (tests/test_tunnel_longring.py covers the guard)."""
    pts, faces = torus
    mesh = str(tmp_path / "aligned_head.ply")
    _write_ply(mesh, pts, faces)
    holder = {}
    kw.setdefault("previews", True)
    guard = (mock.patch.object(mpv, "long_ring_flags",
                               lambda _p, loops, _d, *a, **k: [False] * len(loops))
             if not long_guard else mock.patch.object(mpv, "PREVIEWS_ENABLED",
                                                      mpv.PREVIEWS_ENABLED))
    with guard:
        sel = mpv.TunnelSelector(
            mock.MagicMock(), pts, [pair], mesh_path=mesh, faces=faces, genus=1,
            preview_runner=(lambda fn: _inline_runner(holder["sel"])(fn)) if runner else None,
            **kw)
    holder["sel"] = sel
    return sel, mesh


def _load_addon():
    bpy = types.ModuleType("bpy")

    class _Base:
        pass

    bpy.types = types.SimpleNamespace(
        Operator=type("Operator", (_Base,), {}),
        Panel=type("Panel", (_Base,), {}),
        Scene=type("Scene", (), {}),
    )

    def _prop(*args, **kwargs):
        return ("prop", args, kwargs)

    bpy.props = types.SimpleNamespace(
        EnumProperty=_prop, BoolProperty=_prop, FloatProperty=_prop,
        StringProperty=_prop, IntProperty=_prop)
    bpy.utils = types.SimpleNamespace(
        register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.app = types.SimpleNamespace(background=True)
    bpy.path = types.SimpleNamespace(abspath=lambda p: "")
    bmesh = types.ModuleType("bmesh")

    saved = {k: sys.modules.get(k) for k in ("bpy", "bmesh")}
    sys.modules["bpy"] = bpy
    sys.modules["bmesh"] = bmesh
    try:
        spec = importlib.util.spec_from_file_location("m2s_addon_under_test", _ADDON_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.register()  # exercises the property definitions against the stubs
        mod.unregister()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod


@functools.lru_cache(maxsize=None)
def load_addon():
    """The Blender add-on module under stub bpy/bmesh, loaded once per session."""
    return _load_addon()
