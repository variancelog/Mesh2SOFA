"""Tests for the skin/hair boundary-condition physics and hair-region classifier
in blender_scripts/mesh2SOFA_blender_addon.py.

The add-on imports bpy/bmesh at module level, so stub modules are injected into
sys.modules before loading it by file path. Only the bpy-free functions are
exercised here; the Blender side is checked by a headless export.
"""
import importlib.util
import math
import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON_PATH = os.path.join(_REPO_ROOT, "blender_scripts", "mesh2SOFA_blender_addon.py")


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


addon = _load_addon()


# --- skin absorption (Fig. 1 check values) ----------------------------------

@pytest.mark.parametrize("f, expected, tol", [
    (30, 0.0159, 2e-4),
    (100, 0.0461, 2e-4),
    (1000, 0.0565, 3e-4),
    (5000, 0.0565, 3e-4),
    (20000, 0.0560, 2e-4),
])
def test_skin_absorption_matches_fig1(f, expected, tol):
    assert addon.skin_absorption(f) == pytest.approx(expected, abs=tol)


def test_skin_absorption_zero_at_dc_and_plateau():
    assert addon.skin_absorption(0) == 0.0
    plateau = [addon.skin_absorption(f) for f in range(500, 20001, 500)]
    assert min(plateau) > 0.053 and max(plateau) < 0.0570


def test_skin_admittance_1k():
    y = addon.admittance_from_absorption(addon.skin_absorption(1000))
    assert y == pytest.approx(0.01454, abs=5e-5)


# --- hair ---------------------------------------------------------------------

@pytest.mark.parametrize("f, expected, tol", [
    (2000, 0.062, 1e-3),
    (4000, 0.144, 1e-3),
    (8000, 0.486, 2e-3),
])
def test_hair_admittance_m11(f, expected, tol):
    y = addon.admittance_from_absorption(addon.hair_absorption(f, 11.0))
    assert y == pytest.approx(expected, abs=tol)


def test_hair_admittance_saturates_at_9091():
    assert addon.admittance_from_absorption(addon.hair_absorption(9100, 11.0)) == 1.0
    assert addon.admittance_from_absorption(addon.hair_absorption(22050, 11.0)) == 1.0
    assert addon.admittance_from_absorption(addon.hair_absorption(9050, 11.0)) < 1.0


def test_hair_clipping_floor_and_cap():
    # floor: at low f the linear model is below skin -> skin value is used
    for f in (50, 100, 200, 400):
        assert addon.hair_absorption(f, 11.0) == addon.skin_absorption(f)
    assert addon.hair_absorption(0, 11.0) == 0.0
    # cap: never above 1
    for f in range(0, 22051, 50):
        assert addon.hair_absorption(f, 30.0) <= 1.0
        assert addon.hair_absorption(f, 1.0) >= addon.skin_absorption(f)


# --- CSV validity under Mesh2HRTF's parser rules ------------------------------

def _parse_like_mesh2hrtf(text):
    """Mirror of mesh2input._read_material_data."""
    boundary, freqs, real, imag = None, [], [], []
    for line in text.splitlines():
        if not len(line) or line[0] == '#':
            continue
        if line in ['ADMI', 'IMPE', 'VELO', 'PRES']:
            boundary = line
            continue
        parts = line.split(',')
        assert len(parts) == 3, line
        freqs.append(float(parts[0]))
        real.append(float(parts[1]))
        imag.append(float(parts[2]))
    return boundary, freqs, real, imag


@pytest.mark.parametrize("kind, slope", [("skin", None), ("hair", 11.0), ("hair", 4.5)])
def test_csv_valid_for_mesh2hrtf(kind, slope):
    text = addon.material_csv_text(kind, slope)
    boundary, freqs, real, imag = _parse_like_mesh2hrtf(text)
    assert boundary == "ADMI"
    assert freqs[0] == 0 and freqs[-1] == 22050
    assert len(freqs) == 22050 // 50 + 1
    assert all(b > a for a, b in zip(freqs, freqs[1:]))
    assert all(v == 0.0 for v in imag)
    assert all(0.0 <= v <= 1.0 for v in real)
    assert "Brinkmann" in text
    if slope is not None:
        assert f"m={slope:g}" in text


def test_write_material_files(tmp_path):
    folder = tmp_path / "Materials"
    paths = addon.write_material_files(str(folder), 11.0)
    assert sorted(os.path.basename(p) for p in paths) == ["Hair.csv", "Skin.csv"]
    # hair off -> stale Hair.csv removed, Skin.csv rewritten
    paths = addon.write_material_files(str(folder), None)
    assert [os.path.basename(p) for p in paths] == ["Skin.csv"]
    assert not (folder / "Hair.csv").exists()
    raw = (folder / "Skin.csv").read_bytes()
    assert b"\r" not in raw  # keyword line must be exactly 'ADMI'


# --- NC.inp verification ------------------------------------------------------

_NC_SKIN_HAIR = """BOUNDARY
# Left ear velocity source
ELEM 0 TO 0 VELO 0.1 -1 0.0 -1
# Material: Skin
ELEM 1 TO 900 ADMI 1.0 1 1.0 2
# Material: Hair
ELEM 901 TO 1200 ADMI 1.0 3 1.0 4
RETU
"""
_NC_RIGID = """BOUNDARY
# Left ear velocity source
ELEM 0 TO 0 VELO 0.1 -1 0.0 -1
RETU
"""


def test_verify_nc_inp_boundary():
    v = addon.verify_nc_inp_boundary
    assert v(_NC_SKIN_HAIR, {"Skin", "Hair"}) is None
    assert v(_NC_RIGID, set()) is None
    assert v(_NC_SKIN_HAIR, {"Skin"}) is not None
    assert v(_NC_SKIN_HAIR, set()) is not None
    assert v(_NC_RIGID, {"Skin"}) is not None


def test_m2h_default_material_conflicts(tmp_path):
    data = tmp_path / "Mesh2Input" / "Materials" / "Data"
    data.mkdir(parents=True)
    (data / "ExampleAdmittance.csv").write_text("ADMI\n")
    assert addon.m2h_default_material_conflicts(str(tmp_path))[0] == []
    (data / "Skin.csv").write_text("ADMI\n")
    assert addon.m2h_default_material_conflicts(str(tmp_path))[0] == ["Skin.csv"]


def test_boundary_label():
    assert addon.boundary_label('RIGID', None) == "rigid (legacy)"
    assert addon.boundary_label('SKIN', None) == "skin absorption"
    assert addon.boundary_label('SKIN', 11.0) == "skin absorption + hair (m=11)"


# --- hair mask ----------------------------------------------------------------

def _sphere_points(r=90.0, n=4000):
    """Fibonacci-sphere point set (x=front, y=left, z=up)."""
    pts = []
    golden = math.pi * (3 - math.sqrt(5))
    for i in range(n):
        z = 1 - 2 * (i + 0.5) / n
        rad = math.sqrt(1 - z * z)
        th = golden * i
        pts.append((r * rad * math.cos(th), r * rad * math.sin(th), r * z))
    return pts


def test_compute_hair_mask_sphere():
    r = 90.0
    probes = {
        "top": (0.0, 0.0, r),
        "back": (-r * 0.9, 0.0, r * 0.3),
        "face_front": (r, 0.0, 0.0),
        "below_ears": (0.0, r * 0.8, -r * 0.6),
        "chin": (r * 0.7, 0.0, -r * 0.7),
        # would be hair by rule (b) (back, z >= -0.2 zmax) but lies in the ear cylinder
        "ear_cyl_left": (-25.0, math.sqrt(r * r - 25.0 ** 2 - 10.0 ** 2), 10.0),
        "ear_cyl_right": (-25.0, -math.sqrt(r * r - 25.0 ** 2 - 10.0 ** 2), 10.0),
    }
    pts = _sphere_points(r) + list(probes.values())
    mask = addon.compute_hair_mask(pts, 40.0)
    assert len(mask) == len(pts)
    res = dict(zip(probes, mask[-len(probes):]))
    assert res["top"] and res["back"]
    assert not res["face_front"]
    assert not res["below_ears"]
    assert not res["chin"]
    assert not res["ear_cyl_left"] and not res["ear_cyl_right"]
    # sanity: a meaningful hair fraction, not everything / nothing
    frac = sum(mask) / len(mask)
    assert 0.2 < frac < 0.7
    # every hair point outside the cylinder or re-added in front of the line
    for (x, _y, z), h in zip(pts, mask):
        if h and math.hypot(x, z) <= 40.0:
            assert x >= 0.0 and z >= 0.1 * r


def test_compute_hair_mask_empty():
    assert addon.compute_hair_mask([], 40.0) == []
