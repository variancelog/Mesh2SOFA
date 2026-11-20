"""Stage 5d: head-wrapping loop guard (pure parts + selector wiring)."""
from unittest import mock

import numpy as np

import mesh_problem_viewer as mpv
from test_tunnel_stage3 import make_selector, torus, pair  # noqa: F401 (fixtures)


def _square_loop(side, n=8):
    """n points on a circle-like closed loop; length ~ 2*pi*r."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.c_[side * np.cos(t), side * np.sin(t), np.zeros(n)]


def test_long_ring_flags_threshold():
    small, big = _square_loop(1.0), _square_loop(30.0)
    pts = np.vstack([small, big])
    loops = [np.arange(8), np.arange(8, 16)]
    diag = 100.0                      # 25 % = 25 mm; small ~6 mm, big ~ 180 mm
    assert mpv.long_ring_flags(pts, loops, diag) == [False, True]
    assert mpv.long_ring_flags(pts, loops, 0) == [False, False]


def test_cards_carry_the_warning():
    cards = mpv.option_cards(["TUNNEL", "HANDLE"], [], 0, None, [False, True])
    assert "warning" not in cards[0] and cards[0]["status"] is None
    assert cards[1]["warning"] == mpv.LONG_RING_WARNING
    assert "unusually long" in cards[1]["status"]
    assert cards[1]["enabled"] is True          # a warning, not a block


def test_warning_joins_preview_status():
    cards = mpv.option_cards(["TUNNEL", "HANDLE"], [], 0,
                             [("ready", None), ("ready", None)], [True, False])
    assert cards[0]["status"].endswith(mpv.LONG_RING_WARNING)
    assert "\n" in cards[0]["status"]
    assert cards[0]["preview_state"] == "warn"


def test_long_ring_defect_can_still_be_confirmed(tmp_path, torus, pair):
    """A long ring only warns (no recommendation path exists any more): the user
    confirms it like any other defect."""
    with mock.patch.object(mpv, "long_ring_flags", lambda *a, **k: [True, False]):
        sel, _ = make_selector(tmp_path, torus, pair, runner=False, previews=False,
                               long_guard=True)
    assert not sel.can_apply()
    sel.confirm_active()
    assert sel.can_apply()


def test_selector_flags_long_rings(tmp_path, torus, pair):
    with mock.patch.object(mpv, "long_ring_flags", lambda *a, **k: [True, False]):
        sel, _ = make_selector(tmp_path, torus, pair, runner=False, previews=False,
                               long_guard=True)
    assert sel.handles[0]["long"] == [True, False]
    assert sel.cards()[0]["warning"] == mpv.LONG_RING_WARNING


def test_selector_flags_match_loop_lengths(tmp_path, torus, pair):
    """Real (unpatched) wiring: flags are loop length > 25 % of the bbox diagonal
    (a torus ring can legitimately be long relative to its small body)."""
    sel, _ = make_selector(tmp_path, torus, pair, runner=False, previews=False,
                           long_guard=True)
    h = sel.handles[0]
    expect = [mpv.loop_length_mm(sel.pts, lp) > 0.25 * sel.diag for lp in h["loops"]]
    assert h["long"] == expect
    assert [("warning" in c) for c in sel.cards()[:len(expect)]] == expect
