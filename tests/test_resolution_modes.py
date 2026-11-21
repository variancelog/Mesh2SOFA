"""Tests for the project resolution modes: the core table, its mirror in the
Blender add-on, and run_numcalc_test's parameters.json-derived test index."""
import pytest

from project_store import RESOLUTION_MODES
from _mesh_helpers import load_addon

addon = load_addon()


def test_addon_table_matches_core():
    core = {k: (v["max_hz"], v["step_hz"]) for k, v in RESOLUTION_MODES.items()}
    assert addon.RESOLUTION_MODES == core


@pytest.mark.parametrize("name", sorted(RESOLUTION_MODES))
def test_step_divides_max(name):
    mode = RESOLUTION_MODES[name]
    assert mode["max_hz"] % mode["step_hz"] == 0


def test_only_hires_doubles_length():
    assert {k for k, v in RESOLUTION_MODES.items() if v["double_length"]} == {"hires"}
