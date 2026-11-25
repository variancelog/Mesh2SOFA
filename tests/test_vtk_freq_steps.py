"""get_freq_steps must map a Hz range to 1-based step indices for both the
150 Hz and 100 Hz export grids (it reads parameters.json, not the mode)."""
import json

import pytest

from generate_vtk_outputs import get_freq_steps


def _export(tmp_path, step, max_hz):
    freqs = list(range(step, max_hz + 1, step))
    (tmp_path / "parameters.json").write_text(json.dumps(
        {"frequencyStepSize": step, "maxFrequency": max_hz, "frequencies": freqs}))
    return str(tmp_path), freqs


@pytest.mark.parametrize("step,max_hz", [(150, 18000), (100, 18000)])
def test_range_and_reversed_map(tmp_path, step, max_hz):
    proj, freqs = _export(tmp_path, step, max_hz)
    steps, applied, original = get_freq_steps(proj, 1000, 16000)
    want = [f for f in freqs if 1000 <= f <= 16000]
    assert [original[s] for s in steps] == want
    assert steps == list(range(freqs.index(want[0]) + 1, freqs.index(want[-1]) + 2))
    assert [applied[s] for s in steps] == list(reversed(want))


def test_off_grid_bounds_150(tmp_path):
    proj, _ = _export(tmp_path, 150, 18000)
    steps, _, original = get_freq_steps(proj, 1000, 16000)
    assert original[steps[0]] == 1050
    assert original[steps[-1]] == 15900
