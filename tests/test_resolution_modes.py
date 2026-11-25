"""Tests for the project resolution modes and step size: the core frequency
plan, its mirror in the Blender add-on, and the normalize_step parity."""
import itertools

import pytest

from project_store import (RESOLUTION_MODES, STEP_SIZES, frequency_plan,
                           normalize_step)
from _mesh_helpers import load_addon

addon = load_addon()

PAIRS = list(itertools.product(sorted(RESOLUTION_MODES), STEP_SIZES))

# (mode, step) -> (max_hz, num_freqs, double_length)
EXPECTED = {
    ("lowres", 150):   (16050, 107, False),
    ("lowres", 100):   (16000, 160, True),
    ("standard", 150): (18000, 120, False),
    ("standard", 100): (18000, 180, True),
    ("hires", 150):    (19950, 133, False),
    ("hires", 100):    (20000, 200, True),
}


@pytest.mark.parametrize("mode,step", PAIRS)
def test_core_matches_addon(mode, step):
    plan = frequency_plan(mode, step)
    assert addon.frequency_range(mode, step) == (plan["max_hz"], plan["step_hz"])


@pytest.mark.parametrize("mode,step", PAIRS)
def test_plan_on_grid_and_below_nominal(mode, step):
    plan = frequency_plan(mode, step)
    assert plan["max_hz"] % step == 0
    assert plan["max_hz"] <= RESOLUTION_MODES[mode]["max_hz"]
    assert plan["double_length"] == (step == 100)


@pytest.mark.parametrize("mode,step", PAIRS)
def test_expected_table(mode, step):
    plan = frequency_plan(mode, step)
    assert (plan["max_hz"], plan["num_freqs"], plan["double_length"]) == EXPECTED[(mode, step)]


def test_addon_nominal_max_matches_core():
    assert addon.NOMINAL_MAX_HZ == {k: v["max_hz"] for k, v in RESOLUTION_MODES.items()}
    assert addon.STEP_SIZES == STEP_SIZES


@pytest.mark.parametrize("value", [150, 100, 100.0, "100", 125, None, True, "abc"])
def test_normalize_step_parity(value):
    assert normalize_step(value) == addon.normalize_step(value)
