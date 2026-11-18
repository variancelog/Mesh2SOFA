"""Tests for core/run_numcalc_test.py: NumCalc log parser, test-index lookup,
and the parallel orchestrator (driven by a fake NumCalc Python script)."""
import json
import os
import subprocess
import sys
import textwrap

import pytest

import run_numcalc_test as rnt

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "core", "run_numcalc_test.py")

# Trimmed real NumCalc NC120-120.out (Standard project, 18 kHz step), split at
# the points where the phase changes.
LOG_HEAD = """
Start time: 16/11/2026 23:45:47


>> G E N E R A L   P A R A M E T E R S <<

Number of frequency steps ................... = 120
Total number of elements .................... = 52820
Number of boundary elements ................. = 48716

methodSolver_  = 0: CGS iterative solver
"""
LOG_ASSEMBLE = """
>> S T E P   N U M B E R   A N D   F R E Q U E N C Y <<

Step 120, Frequency = 18000 Hz

Direct multilevel fast multipole BEM

Number of equations = 48716

Informations about clusters:
   Level    Num. of clusters    Maximum radius   Minimum radius
     0          229                0.0538141         0.0121781
Nonzeros of the near field matrix ..................... = 150748518
"""
LOG_SOLVE = """Sum of nonzeros of the FMBEM matrices ................. = 748061862
"""
LOG_POST = """
CGS solver: number of iterations = 67, relative error = 3.90717e-10

Time Statistic For The Current Step (In Second)

Assembling the equation system         : 441
"""
LOG_DONE = """Solving the equation system            : 326
Post processing                        : 752
Total                                  : 1519

>> T I M E   S T A T I S T I C   (In   Second) <<

The whole job                          : 1521

End time: 17/11/2026 0:11:8
"""
CHUNKS = [LOG_HEAD, LOG_ASSEMBLE, LOG_SOLVE, LOG_POST, LOG_DONE]


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

@pytest.mark.parametrize("n_chunks, phase", [
    (0, "no log yet"), (1, "reading input"), (2, "assembling"),
    (3, "solving"), (4, "post-processing"), (5, "done")])
def test_parse_phases(n_chunks, phase):
    info = rnt.parse_nc_log("".join(CHUNKS[:n_chunks]))
    assert info["phase"] == phase


def test_parse_empty_and_whitespace():
    assert rnt.parse_nc_log("")["phase"] == "no log yet"
    assert rnt.parse_nc_log("\n\n  \n")["phase"] == "no log yet"


def test_parse_numbers_full_log():
    info = rnt.parse_nc_log("".join(CHUNKS))
    assert info["step"] == 120
    assert info["freq_hz"] == 18000
    assert info["equations"] == 48716
    assert info["nonzeros"] == 748061862
    assert info["iterations"] == 67
    assert info["rel_error"] == pytest.approx(3.90717e-10)
    assert info["whole_job_s"] == 1521


def test_parse_other_solver_name():
    text = LOG_HEAD + LOG_ASSEMBLE + LOG_SOLVE + \
        "GMRES solver: number of iterations = 12, relative error = 1e-8\n"
    info = rnt.parse_nc_log(text)
    assert info["phase"] == "post-processing"
    assert info["iterations"] == 12 and info["rel_error"] == pytest.approx(1e-8)


def test_ear_tag():
    assert rnt.ear_tag(os.path.join("x", "Exports", "Left_Project")) == "L"
    assert rnt.ear_tag(os.path.join("x", "Right_Project") + os.sep) == "R"
    assert rnt.ear_tag(os.path.join("x", "Custom")) == "Custom"


# --------------------------------------------------------------------------
# read_test_index
# --------------------------------------------------------------------------

def test_read_test_index(tmp_path):
    (tmp_path / "parameters.json").write_text(
        json.dumps({"numFrequencies": 3, "frequencies": [100, 200, 300]}))
    assert rnt.read_test_index(str(tmp_path)) == (3, 300)


def test_read_test_index_missing(tmp_path):
    with pytest.raises(OSError):
        rnt.read_test_index(str(tmp_path))


def test_read_test_index_no_frequencies(tmp_path):
    (tmp_path / "parameters.json").write_text(json.dumps({"frequencies": []}))
    with pytest.raises(ValueError):
        rnt.read_test_index(str(tmp_path))


# --------------------------------------------------------------------------
# orchestrator with a fake NumCalc
# --------------------------------------------------------------------------

FAKE_NUMCALC = textwrap.dedent('''
    import json, os, sys, time
    args = sys.argv[1:]
    idx = args[args.index("-istart") + 1]
    rc = int(args[args.index("--rc") + 1]) if "--rc" in args else 0
    if os.environ.get("FAKE_NC_FAIL_IN") and os.environ["FAKE_NC_FAIL_IN"] in os.getcwd():
        rc = 3
    delay = float(args[args.index("--delay") + 1]) if "--delay" in args else 0.2
    prewait = float(args[args.index("--prewait") + 1]) if "--prewait" in args else 0
    chunks = json.loads(os.environ["FAKE_NC_CHUNKS"])
    print("fake NumCalc says hello", flush=True)
    if os.environ.get("FAKE_NC_SPAM"):
        # undecodable-in-cp1252 bytes, then ~2 MB so a dead pipe reader blocks us
        nl = bytes([10])
        sys.stdout.buffer.write(b"weird " + bytes([0x81, 0x8d]) + b" byte" + nl)
        for _ in range(20000):
            sys.stdout.buffer.write(b"x" * 100 + nl)
        sys.stdout.buffer.flush()
    time.sleep(prewait)
    with open("NC%s-%s.out" % (idx, idx), "w") as fh:
        for c in chunks:
            fh.write(c)
            fh.flush()
            time.sleep(delay)
    print("fake NumCalc exiting", flush=True)
    sys.exit(rc)
''')


@pytest.fixture
def exports(tmp_path, monkeypatch):
    return _make_exports(tmp_path, tmp_path, monkeypatch)


def _make_exports(tmp_path, root, monkeypatch):
    fake = tmp_path / "fake_numcalc.py"
    fake.write_text(FAKE_NUMCALC)
    monkeypatch.setenv("FAKE_NC_CHUNKS", json.dumps(CHUNKS))
    projects = []
    for name in ("Left_Project", "Right_Project"):
        proj = root / "Exports" / name
        (proj / "NumCalc" / "source_1").mkdir(parents=True)
        (proj / "parameters.json").write_text(
            json.dumps({"numFrequencies": 120,
                        "frequencies": [150 * i for i in range(1, 121)]}))
        projects.append(str(proj))
    return str(fake), projects


def _builder(fake, rc_for=None, delay=0.2, prewait=0):
    def build(ear):
        cmd = [sys.executable, "-u", fake, "-istart", str(ear.index),
               "-iend", str(ear.index), "--delay", str(delay),
               "--prewait", str(prewait)]
        if rc_for and ear.tag in rc_for:
            cmd += ["--rc", str(rc_for[ear.tag])]
        return cmd
    return build


def _run(projects, build, **kw):
    lines = []
    kw.setdefault("poll_s", 0.05)
    kw.setdefault("heartbeat_s", 0.3)
    ears = rnt.run_ears(projects, build, out=lines.append, **kw)
    return ears, lines


def test_parallel_ears_overlap_and_pass(exports):
    fake, projects = exports
    ears, lines = _run(projects, _builder(fake), avail_mem_fn=lambda: 32 * 1024 ** 3)
    left, right = ears
    assert left.passed and right.passed
    # concurrent: each started before the other finished
    assert left.start_mono < right.end_mono and right.start_mono < left.end_mono
    text = "\n".join(lines)
    for tag in ("L", "R"):
        assert f"[{tag}] fake NumCalc says hello" in text
        assert f"[{tag}] assembly done (48716 eq) -> solving" in text
        assert f"[{tag}] solve done: 67 iterations, rel. error 3.9e-10" in text
        assert f"[{tag}] NumCalc log complete" in text
    assert "elapsed" in text and "system available 32.0 GB" in text  # heartbeat
    assert "=== Stability test summary ===" in text
    assert sum(1 for l in lines if l.startswith("[L] PASS")) == 1
    assert sum(1 for l in lines if l.startswith("[R] PASS")) == 1


def test_one_ear_fails(exports):
    fake, projects = exports
    ears, lines = _run(projects, _builder(fake, rc_for={"R": 3}))
    assert ears[0].passed and not ears[1].passed
    assert ears[1].returncode == 3
    assert any(l.startswith("[R] FAIL") and "rc=3" in l for l in lines)


def test_stale_log_is_ignored(exports):
    """A complete NC log from an earlier run must not be reported as progress
    while the new NumCalc has not written its log yet."""
    fake, projects = exports
    stale = os.path.join(projects[0], "NumCalc", "source_1", "NC120-120.out")
    with open(stale, "w") as fh:
        fh.write("".join(CHUNKS))
    os.utime(stale, (1_000_000, 1_000_000))
    ears, lines = _run(projects[:1], _builder(fake, delay=0.05, prewait=0.6),
                       heartbeat_s=0.2)
    hb = [l for l in lines if "| elapsed" in l]
    assert hb and hb[0].startswith("[L] running (no log yet)")
    first_phase = next(i for i, l in enumerate(lines) if "log complete" in l)
    assert first_phase > lines.index(hb[0])
    assert ears[0].passed


def test_missing_setup_does_not_block_other_ear(exports, tmp_path):
    fake, projects = exports
    os.remove(os.path.join(projects[1], "parameters.json"))
    ears, lines = _run(projects, _builder(fake))
    assert ears[0].passed and not ears[1].passed
    assert any("[R] [ERROR]" in l and "parameters.json" in l for l in lines)


def test_watchdog_kills_and_reruns_second_ear(exports):
    fake, projects = exports
    ears, lines = _run(projects, _builder(fake, delay=0.3),
                       avail_mem_fn=lambda: 1 * 1024 ** 3,
                       min_avail_bytes=4 * 1024 ** 3, watchdog_grace_s=0)
    left, right = ears
    text = "\n".join(lines)
    assert "[R] [!] RAM watchdog" in text
    assert "[L] [!] RAM watchdog" not in text        # last ear never killed
    assert left.attempts == 1 and right.attempts == 2
    assert left.passed and right.passed
    # re-run happened after the left ear finished
    assert right.start_mono >= left.end_mono
    assert "(re-run)" in text and "RAM fallback" in text


def test_sequential_flag(exports):
    fake, projects = exports
    ears, _ = _run(projects, _builder(fake, delay=0.05), sequential=True)
    assert ears[1].start_mono >= ears[0].end_mono


def _exe_wrapper(tmp_path, fake):
    """An executable that runs the fake NumCalc, for the CLI's --numcalc."""
    if sys.platform == "win32":
        w = tmp_path / "fake_numcalc.cmd"
        w.write_text('@"%s" -u "%s" %%*' % (sys.executable, fake) + os.linesep)
    else:
        w = tmp_path / "fake_numcalc.sh"
        w.write_text(os.linesep.join(["#!/bin/sh",
                                      'exec "%s" -u "%s" "$@"' % (sys.executable, fake), ""]))
        w.chmod(0o755)
    return str(w)


def test_cli_parallel_exit_code(exports, tmp_path, monkeypatch):
    fake, projects = exports
    exe = _exe_wrapper(tmp_path, fake)
    cmd = [sys.executable, "-u", SCRIPT, "--numcalc", exe, "--heartbeat", "0.5"] + projects
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout
    assert "[L] PASS" in r.stdout and "[R] PASS" in r.stdout
    monkeypatch.setenv("FAKE_NC_FAIL_IN", "Right_Project")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert "[L] PASS" in r.stdout and "[R] FAIL" in r.stdout


def test_cli_legacy_form_setup_failure_exits_1(exports):
    _, projects = exports
    os.remove(os.path.join(projects[0], "parameters.json"))
    r = subprocess.run([sys.executable, "-u", SCRIPT, projects[0], "NumCalc.exe"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 1
    assert "parameters.json" in r.stdout and "FAIL" in r.stdout


# --------------------------------------------------------------------------
# regressions (verifier findings)
# --------------------------------------------------------------------------

def _cli(projects, exe, env_extra, timeout=60):
    env = dict(os.environ, PYTHONIOENCODING="cp1252", **env_extra)  # strict, like the GUI pipe
    cmd = [sys.executable, "-u", SCRIPT, "--numcalc", exe, "--heartbeat", "0.5"] + projects
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=env, timeout=timeout)


def test_cli_survives_undecodable_child_bytes(exports, tmp_path):
    """#1: odd bytes from NumCalc + 2 MB of output must not kill the pipe
    reader (which would block the child and hang the worker)."""
    fake, projects = exports
    r = _cli(projects, _exe_wrapper(tmp_path, fake), {"FAKE_NC_SPAM": "1"})
    out = r.stdout.decode("cp1252", "replace")
    assert r.returncode == 0, out[-2000:]
    assert "[L] PASS" in out and "[R] PASS" in out
    assert "[L] weird" in out


def test_cli_non_cp1252_project_path(tmp_path, monkeypatch):
    """#2: a Cyrillic project folder must not crash the cp1252 console."""
    root = tmp_path / "\u041f\u0440\u043e\u0435\u043a\u0442"
    fake, projects = _make_exports(tmp_path, root, monkeypatch)
    r = _cli(projects, _exe_wrapper(tmp_path, fake), {})
    out = r.stdout.decode("cp1252", "replace")
    assert r.returncode == 0, out[-2000:]
    assert "[L] PASS" in out and "[R] PASS" in out
    assert "\\u041f" in out          # backslash-escaped, not a crash


def test_fresh_stale_log_is_ignored(exports):
    """#3: a complete log written just before launch is still stale."""
    fake, projects = exports
    stale = os.path.join(projects[0], "NumCalc", "source_1", "NC120-120.out")
    with open(stale, "w") as fh:
        fh.write("".join(CHUNKS))       # mtime = now, i.e. < 1 s before launch
    ears, lines = _run(projects[:1], _builder(fake, delay=0.05, prewait=0.6),
                       heartbeat_s=0.2)
    hb = [l for l in lines if "| elapsed" in l]
    assert hb and hb[0].startswith("[L] running (no log yet)")
    done = next(i for i, l in enumerate(lines) if "log complete" in l)
    assert done > lines.index(hb[0])
    assert ears[0].passed


@pytest.mark.parametrize("rc", [0, 1])
def test_watchdog_race_victim_finished_itself(exports, monkeypatch, rc):
    """#4: if the victim finishes on its own as the watchdog fires (rc 0, or
    its log already done), it counts as a pass and is not re-run."""
    fake, projects = exports
    monkeypatch.setattr(rnt, "_kill_tree", lambda proc: proc.wait())  # kill lands too late
    ears, lines = _run(projects, _builder(fake, rc_for={"R": rc}, delay=0.05),
                       avail_mem_fn=lambda: 1 * 1024 ** 3,
                       min_avail_bytes=4 * 1024 ** 3, watchdog_grace_s=0)
    left, right = ears
    text = "\n".join(lines)
    assert "[R] [!] RAM watchdog" in text
    assert right.attempts == 1 and right.passed and left.passed
    assert "(re-run)" not in text


def test_format_cmd_quotes_spaces():
    s = rnt.format_cmd(["C:\\Program Files\\NumCalc.exe", "-istart", "120"])
    assert s.startswith('"C:\\Program Files\\NumCalc.exe"') or \
        s.startswith("'C:\\Program Files\\NumCalc.exe'")
