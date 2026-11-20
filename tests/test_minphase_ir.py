import os
import sys

import numpy as np
import pytest
from scipy.io import wavfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
import generate_extras as ge  # noqa: E402

FS = 48000
FREQS = ge.generate_fractional_octave_frequencies(20, 20000, fraction=48)
NFFT = 65536


def _mag_db(h):
    return 20 * np.log10(np.abs(np.fft.rfft(h.astype(float), NFFT)) + 1e-30)


def _band_mean(db, lo, hi):
    g = np.fft.rfftfreq(NFFT, 1 / FS)
    return db[(g >= lo) & (g <= hi)].mean()


def test_flat_is_scaled_delta():
    z = np.zeros_like(FREQS)
    ir = ge.design_minphase_stereo_ir(FREQS, z, z, FS)
    assert ir.shape == (4096, 2) and ir.dtype == np.float32
    assert np.allclose(ir[:, 0], ir[:, 1])
    assert np.argmax(np.abs(ir[:, 0])) == 0
    assert abs(ir[0, 0]) == pytest.approx(10 ** (-0.5 / 20), abs=1e-3)
    assert np.abs(ir[1:, 0]).max() < 1e-3


def test_channels_aligned_at_300hz():
    z = np.zeros_like(FREQS)
    ir = ge.design_minphase_stereo_ir(FREQS, z + 6.0, z, FS)
    ml = _band_mean(_mag_db(ir[:, 0]), 212, 424)
    mr = _band_mean(_mag_db(ir[:, 1]), 212, 424)
    assert abs(ml - mr) < 0.05


def test_peak_level_and_shape():
    db_l = np.zeros_like(FREQS)
    db_r = 8.0 * np.exp(-0.5 * (np.log2(FREQS / 4000.0) / 0.3) ** 2)
    ir, info = ge.design_minphase_stereo_ir(FREQS, db_l, db_r, FS, return_info=True)
    peak = max(_mag_db(ir[:, 0]).max(), _mag_db(ir[:, 1]).max())
    assert peak == pytest.approx(-0.5, abs=0.05)
    assert np.abs(ir).max() < 1 and ir.dtype == np.float32 and ir.shape == (4096, 2)
    assert info["gain_db"] < 0


def test_reproduces_input_and_is_minimum_phase():
    db = 5 * np.sin(np.log2(FREQS / 20) * 1.3) + 3 * np.exp(-0.5 * (np.log2(FREQS / 8000) / 0.4) ** 2)
    ir = ge.design_minphase_stereo_ir(FREQS, db, db, FS)
    g = np.fft.rfftfreq(NFFT, 1 / FS)
    got = _mag_db(ir[:, 0])
    want = np.interp(np.log(np.clip(g, 20, 20000)), np.log(FREQS), db)
    m = (g >= 30) & (g <= 19000)
    d = got[m] - want[m]
    d -= d.mean()  # absolute level is set by the headroom rule
    assert np.abs(d).max() < 0.3
    h = ir[:, 0].astype(float)
    assert (h[:410] ** 2).sum() / (h ** 2).sum() >= 0.9
    assert np.argmax(np.abs(h)) < 5


def test_wav_roundtrip(tmp_path):
    ir = ge.design_minphase_stereo_ir(FREQS, np.zeros_like(FREQS), np.zeros_like(FREQS), FS)
    p = tmp_path / "x.wav"
    ge.save_wav_stereo(str(p), ir, FS)
    rate, data = wavfile.read(str(p))
    assert rate == 48000 and data.dtype == np.float32 and data.shape == (4096, 2)
    assert np.allclose(data, ir)


def test_main_smoke(tmp_path, monkeypatch):
    sf = pytest.importorskip("sofar")
    try:
        s = sf.Sofa("SimpleFreeFieldHRIR")
        n = 12
        rng = np.random.default_rng(0)
        s.Data_IR = rng.standard_normal((n, 2, 256)) * 0.01
        s.Data_IR[:, :, 0] = 1.0
        s.Data_SamplingRate = FS
        az = np.linspace(0, 330, n)
        s.SourcePosition = np.stack([az, np.zeros(n), np.ones(n)], axis=1)
        path = tmp_path / "t_48000Hz.sofa"
        sf.write_sofa(str(path), s, compression=0)
    except Exception as e:  # building a SOFA is best-effort
        pytest.skip(f"cannot build synthetic SOFA: {e}")
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(path), "--output_dir", str(tmp_path)])
    ge.main()
    wavs = list(tmp_path.glob("* MinPhase.wav"))
    assert len(wavs) == 1
    rate, data = wavfile.read(str(wavs[0]))
    assert rate == 48000 and data.shape == (4096, 2)
