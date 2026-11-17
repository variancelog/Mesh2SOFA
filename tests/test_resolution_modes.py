"""Tests for the project resolution modes: the core table, its mirror in the
Blender add-on, and run_numcalc_test's parameters.json-derived test index."""
import json
import os

import pytest

from project_store import RESOLUTION_MODES
import run_numcalc_test
from test_boundary_materials import addon


def test_addon_table_matches_core():
    core = {k: (v["max_hz"], v["step_hz"]) for k, v in RESOLUTION_MODES.items()}
    assert addon.RESOLUTION_MODES == core


@pytest.mark.parametrize("name", sorted(RESOLUTION_MODES))
def test_step_divides_max(name):
    mode = RESOLUTION_MODES[name]
    assert mode["max_hz"] % mode["step_hz"] == 0


def test_only_hires_doubles_length():
    assert {k for k, v in RESOLUTION_MODES.items() if v["double_length"]} == {"hires"}


def _write_params(project_dir, params):
    with open(os.path.join(project_dir, "parameters.json"), "w") as fh:
        json.dump(params, fh)


def test_test_index_from_num_frequencies(tmp_path):
    freqs = [100 * i for i in range(1, 201)]
    _write_params(tmp_path, {"numFrequencies": 200, "frequencies": freqs})
    assert run_numcalc_test.read_test_index(str(tmp_path)) == (200, 20000)


def test_test_index_falls_back_to_frequencies_length(tmp_path):
    freqs = [150 * i for i in range(1, 121)]
    _write_params(tmp_path, {"frequencies": freqs})
    assert run_numcalc_test.read_test_index(str(tmp_path)) == (120, 18000)


def test_missing_parameters_json_fails(tmp_path, capsys):
    assert run_numcalc_test.run_test(str(tmp_path), "NumCalc.exe") is False
    assert "parameters.json" in capsys.readouterr().out


def test_unreadable_parameters_json_fails(tmp_path):
    (tmp_path / "parameters.json").write_text("{not json")
    assert run_numcalc_test.run_test(str(tmp_path), "NumCalc.exe") is False
