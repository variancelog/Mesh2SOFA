"""step_summary (the Step dialog's live line) is a pure function in the GUI module."""
import importlib.util
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def step_summary():
    spec = importlib.util.spec_from_file_location(
        "gui_step", os.path.join(REPO, "_project_manager_gui.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.step_summary


def test_examples(step_summary):
    assert step_summary("standard", 100) == (
        "Standard @ 100 Hz: 180 frequencies up to 18 kHz, 512-sample SOFAs")
    assert step_summary("hires", 150) == (
        "HiRes @ 150 Hz: 133 frequencies up to 19.95 kHz, 256-sample SOFAs")
    assert step_summary("lowres", 100) == (
        "Lowres @ 100 Hz: 160 frequencies up to 16 kHz, 512-sample SOFAs")
