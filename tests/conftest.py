"""Pytest bootstrap: put the repo's core/ folder on sys.path so the test files
can keep their flat imports (`from project_store import ...`, `from
tunnel_loop_extractor import ...`, etc.) now that the modules live in core/.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE_DIR = os.path.join(_REPO_ROOT, "core")
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

# ---------------------------------------------------------------------------
# Session-scoped synthetic meshes (built once for the whole run; the arrays are
# read-only so an accidental in-place mutation fails loudly instead of leaking
# into other tests).
# ---------------------------------------------------------------------------
import pytest  # noqa: E402

from _mesh_helpers import torus_mesh, double_torus_mesh  # noqa: E402


def _freeze(*arrays):
    for a in arrays:
        a.setflags(write=False)


@pytest.fixture(scope="session")
def torus():
    pts, faces = torus_mesh()
    _freeze(pts, faces)
    return pts, faces


@pytest.fixture(scope="session")
def pair(torus):
    from tunnel_loop_locator import select_cut_loop
    pts, faces = torus
    p = select_cut_loop(pts, faces, 1)[0]
    assert p.get("avoid") is not None, "fixture needs both options of the defect"
    return p


@pytest.fixture(scope="session")
def torus_loop(torus):
    from tunnel_loop_locator import locate_cut_loops
    pts, faces = torus
    loops = locate_cut_loops(pts, faces, 1)
    assert len(loops) == 1
    return loops[0]


@pytest.fixture(scope="session")
def dtorus():
    from tunnel_loop_extractor import mesh_genus, _topo_counts
    pts, faces = double_torus_mesh()
    assert mesh_genus(pts, faces) == 2
    assert _topo_counts(faces)[3:] == (1, 0), "fixture must be 1 closed component"
    _freeze(pts, faces)
    return pts, faces


@pytest.fixture(scope="session")
def dtorus_loops(dtorus):
    from tunnel_loop_locator import locate_cut_loops
    pts, faces = dtorus
    loops = locate_cut_loops(pts, faces, 2)
    assert len(loops) == 2, "fixture must give one severing loop per handle"
    return loops


@pytest.fixture(scope="session")
def dtorus_pairs(dtorus, dtorus_loops):
    pts, faces = dtorus
    return pts, faces, [{"cut": lp, "avoid": None} for lp in dtorus_loops]
