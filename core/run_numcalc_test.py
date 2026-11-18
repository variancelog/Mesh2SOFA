"""Step 6 pre-check: NumCalc stability test on the highest exported frequency.

Runs one NumCalc step (the highest frequency in each export's parameters.json)
for every given Mesh2HRTF project folder -- normally Exports/Left_Project and
Exports/Right_Project -- in PARALLEL (NumCalc is single-threaded), and prints
progress to stdout so the GUI log is not silent for the ~25 min a Standard step
takes:

* phase transitions parsed from NumCalc's own log, NC<i>-<i>.out in
  NumCalc/source_1 (assembling -> solving -> post-processing -> done);
* a heartbeat per running ear: phase, elapsed time, NumCalc RSS, system
  available RAM (via psutil when installed);
* a RAM watchdog: if available memory drops below a threshold while more than
  one ear runs, the most recently started ear is killed and re-run after the
  others finish (sequential fallback). The last running ear is never killed.

Usage:
    run_numcalc_test.py --numcalc <exe> <project> [<project> ...] [--sequential]
    run_numcalc_test.py <project> <exe>          (legacy form)

Exit code 0 only if every ear's NumCalc returned 0.
"""
import argparse
import json
import locale
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

try:
    import psutil
except ImportError:  # progress degrades to elapsed time only
    psutil = None

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

DEFAULT_HEARTBEAT_S = 30.0
DEFAULT_MIN_AVAIL_BYTES = 4 * 1024 ** 3
DEFAULT_POLL_S = 1.0
DEFAULT_WATCHDOG_GRACE_S = 15.0

# Phases in the order NumCalc goes through them.
PHASES = ("no log yet", "reading input", "assembling", "solving",
          "post-processing", "done")


def read_test_index(project_dir):
    """(index, freq_hz) of the highest exported frequency, read from the
    Mesh2HRTF <project_dir>/parameters.json. Raises on a missing/unreadable
    file so the test never runs against a guessed index."""
    params_path = os.path.join(project_dir, "parameters.json")
    with open(params_path, "r") as fh:
        params = json.load(fh)
    freqs = params.get("frequencies") or []
    n = params.get("numFrequencies") or len(freqs)
    if not n:
        raise ValueError(f"no frequencies in {params_path}")
    return int(n), (freqs[-1] if freqs else None)


# --------------------------------------------------------------------------
# NumCalc log parsing (pure)
# --------------------------------------------------------------------------

_RE_STEP = re.compile(r"Step\s+(\d+),\s*Frequency\s*=\s*([\d.eE+-]+)\s*Hz")
_RE_EQS = re.compile(r"Number of equations\s*=\s*(\d+)")
_RE_NONZ = re.compile(r"Sum of nonzeros of the FMBEM matrices[ .]*=\s*(\d+)")
_RE_ITER = re.compile(r"number of iterations\s*=\s*(\d+)"
                      r"(?:\s*,\s*relative error\s*=\s*([\d.eE+-]+))?", re.I)
_RE_WHOLE = re.compile(r"The whole job\s*:\s*([\d.]+)")


def parse_nc_log(text):
    """Parse the text of a NumCalc NC<i>-<i>.out log.

    Returns a dict with 'phase' (one of PHASES) and whatever numbers were
    found: step, freq_hz, equations, nonzeros, iterations, rel_error,
    whole_job_s (missing keys are None)."""
    info = {"phase": "no log yet", "step": None, "freq_hz": None,
            "equations": None, "nonzeros": None, "iterations": None,
            "rel_error": None, "whole_job_s": None}
    if not text or not text.strip():
        return info
    info["phase"] = "reading input"
    m = _RE_STEP.search(text)
    if m:
        info["step"] = int(m.group(1))
        info["freq_hz"] = float(m.group(2))
        info["phase"] = "assembling"
    m = _RE_EQS.search(text)
    if m:
        info["equations"] = int(m.group(1))
        info["phase"] = "assembling"
    m = _RE_NONZ.search(text)
    if m:
        info["nonzeros"] = int(m.group(1))
        info["phase"] = "solving"
    m = _RE_ITER.search(text)
    if m:
        info["iterations"] = int(m.group(1))
        if m.group(2):
            info["rel_error"] = float(m.group(2))
        info["phase"] = "post-processing"
    m = _RE_WHOLE.search(text)
    if m:
        info["whole_job_s"] = float(m.group(1))
        info["phase"] = "done"
    if "End time:" in text:
        info["phase"] = "done"
    return info


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def fmt_duration(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def fmt_bytes(n):
    if n is None:
        return "n/a"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{n / 1024 ** 3:.1f} GB"


_CHILD_ENCODING = locale.getpreferredencoding(False) or "utf-8"


def safe_print(line):
    """print() that never raises on characters the console can't encode
    (cp1252 GUI pipe + Cyrillic paths / odd NumCalc bytes)."""
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(enc, "backslashreplace").decode(enc), flush=True)


def format_cmd(cmd):
    cmd = [str(c) for c in cmd]
    return subprocess.list2cmdline(cmd) if sys.platform == "win32" else shlex.join(cmd)


def _log_sig(path):
    """(mtime_ns, size) of path, or None if it doesn't exist."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def ear_tag(project_dir):
    name = os.path.basename(os.path.normpath(project_dir))
    low = name.lower()
    if low.startswith("left"):
        return "L"
    if low.startswith("right"):
        return "R"
    return name


def _system_available_bytes():
    if psutil is None:
        return None
    try:
        return psutil.virtual_memory().available
    except Exception:
        return None


def _process_rss_bytes(pid):
    """RSS of pid plus its children (None if unknown)."""
    if psutil is None:
        return None
    try:
        p = psutil.Process(pid)
        total = p.memory_info().rss
        for c in p.children(recursive=True):
            try:
                total += c.memory_info().rss
            except psutil.Error:
                pass
        return total
    except Exception:
        return None


def _kill_tree(proc):
    """Best-effort kill of a Popen and its children."""
    if proc is None or proc.poll() is not None:
        return
    if psutil is not None:
        try:
            for c in psutil.Process(proc.pid).children(recursive=True):
                try:
                    c.kill()
                except psutil.Error:
                    pass
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def default_command_builder(numcalc_exe):
    def build(ear):
        return [numcalc_exe, "-istart", str(ear.index), "-iend", str(ear.index)]
    return build


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------

class Ear:
    """State of one project's (one ear's) stability test."""

    def __init__(self, project_dir):
        self.project_dir = project_dir
        self.tag = ear_tag(project_dir)
        self.source_dir = os.path.join(project_dir, "NumCalc", "source_1")
        self.index = None
        self.freq = None
        self.error = None          # setup error -> FAIL without running
        self.proc = None
        self.reader = None
        self.pre_log_sig = None    # (mtime_ns, size) of the log before launch
        self.forced_pass = False   # watchdog kill landed after the log was done
        self.start_mono = None
        self.end_mono = None
        self.first_start_mono = None
        self.returncode = None
        self.info = parse_nc_log("")
        self.log_sig = None
        self.watchdog_killed = False
        self.attempts = 0
        self.launch_seq = 0        # order of the latest launch (watchdog victim)

    @property
    def log_path(self):
        return os.path.join(self.source_dir, f"NC{self.index}-{self.index}.out")

    def prepare(self):
        try:
            self.index, self.freq = read_test_index(self.project_dir)
        except Exception as e:
            self.error = (f"cannot read the export's parameters.json ({e}). "
                          "Re-export the project from Blender.")
            return
        if not os.path.isdir(self.source_dir):
            self.error = f"source folder not found: {self.source_dir}"

    @property
    def passed(self):
        return self.error is None and (self.returncode == 0 or self.forced_pass)


def run_ears(project_dirs, build_cmd, *, sequential=False,
             heartbeat_s=DEFAULT_HEARTBEAT_S, poll_s=DEFAULT_POLL_S,
             min_avail_bytes=DEFAULT_MIN_AVAIL_BYTES,
             watchdog_grace_s=DEFAULT_WATCHDOG_GRACE_S,
             avail_mem_fn=_system_available_bytes, rss_fn=_process_rss_bytes,
             out=None):
    """Run one NumCalc test per project dir and return the list of Ear.

    build_cmd(ear) -> argv list, run with cwd=ear.source_dir.
    avail_mem_fn() -> available system bytes or None (watchdog off if None).
    out(str) prints one line (default: locked, flushed print)."""
    lock = threading.Lock()
    if out is None:
        def out(line):
            with lock:
                safe_print(line)

    ears = [Ear(p) for p in project_dirs]
    pending = []
    for ear in ears:
        ear.prepare()
        if ear.error:
            out(f"[{ear.tag}] [ERROR] {ear.error}")
        else:
            out(f"[{ear.tag}] target index {ear.index} ({ear.freq} Hz) "
                f"in {ear.source_dir}")
            pending.append(ear)

    running = []
    fallback = sequential
    t0 = time.monotonic()
    last_heartbeat = t0
    last_kill = None
    launches = 0

    def start(ear):
        nonlocal launches
        cmd = build_cmd(ear)
        ear.attempts += 1
        launches += 1
        ear.launch_seq = launches
        ear.watchdog_killed = False
        ear.info = parse_nc_log("")
        ear.log_sig = None
        ear.returncode = None
        ear.forced_pass = False
        ear.pre_log_sig = _log_sig(ear.log_path)   # None: no log before launch
        ear.start_mono = time.monotonic()
        if ear.first_start_mono is None:
            ear.first_start_mono = ear.start_mono
        retry = " (re-run)" if ear.attempts > 1 else ""
        out(f"[{ear.tag}] starting{retry}: {format_cmd(cmd)}")
        try:
            # Binary pipe, decoded per line in pump(): a decode can never raise.
            ear.proc = subprocess.Popen(
                cmd, cwd=ear.source_dir, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            ear.error = f"could not launch NumCalc: {e}"
            ear.end_mono = time.monotonic()
            out(f"[{ear.tag}] [ERROR] {ear.error}")
            return False

        def pump(proc=ear.proc, tag=ear.tag):
            # Must drain until EOF no matter what, or the child blocks on a
            # full pipe and the test hangs forever.
            reported = False
            while True:
                try:
                    raw = proc.stdout.readline()
                except Exception:
                    break
                if not raw:
                    break
                try:
                    line = raw.decode(_CHILD_ENCODING, "backslashreplace").rstrip()
                    if line:
                        out(f"[{tag}] {line}")
                except Exception as e:
                    if not reported:
                        reported = True
                        try:
                            out(f"[{tag}] [!] could not echo NumCalc output "
                                f"({type(e).__name__}); still draining")
                        except Exception:
                            pass
            try:
                proc.stdout.close()
            except Exception:
                pass

        ear.reader = threading.Thread(target=pump, daemon=True)
        ear.reader.start()
        running.append(ear)
        return True

    def elapsed(ear):
        return time.monotonic() - ear.start_mono

    def refresh_log(ear):
        """Re-parse the NC log if it changed; print phase transitions."""
        sig = _log_sig(ear.log_path)
        if sig is None:
            return
        # A log that existed before launch is stale until NumCalc changes it.
        if sig == ear.pre_log_sig:
            return
        if sig == ear.log_sig:
            return
        ear.log_sig = sig
        try:
            with open(ear.log_path, "r", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return
        new = parse_nc_log(text)
        old_i = PHASES.index(ear.info["phase"])
        new_i = PHASES.index(new["phase"])
        ear.info = new
        at = fmt_duration(elapsed(ear))
        for ph in PHASES[old_i + 1:new_i + 1]:
            if ph == "reading input":
                out(f"[{ear.tag}] NumCalc log started -> reading input at {at}")
            elif ph == "assembling":
                what = (f"step {new['step']} ({new['freq_hz']:g} Hz)"
                        if new["step"] is not None else "equation system")
                out(f"[{ear.tag}] assembling {what} at {at}")
            elif ph == "solving":
                eq = f" ({new['equations']} eq)" if new["equations"] else ""
                out(f"[{ear.tag}] assembly done{eq} -> solving at {at}")
            elif ph == "post-processing":
                out(f"[{ear.tag}] solve done: {_solve_summary(new)} "
                    f"-> post-processing at {at}")
            elif ph == "done":
                job = (f" (NumCalc whole job {new['whole_job_s']:g} s)"
                       if new["whole_job_s"] is not None else "")
                out(f"[{ear.tag}] NumCalc log complete at {at}{job}")

    def reap(ear):
        if ear.reader is not None:
            ear.reader.join(timeout=5)
        ear.returncode = ear.proc.returncode
        ear.end_mono = time.monotonic()
        running.remove(ear)
        refresh_log(ear)
        if ear.watchdog_killed and (ear.returncode == 0
                                    or ear.info["phase"] == "done"):
            # Race: it finished on its own just as the watchdog fired.
            ear.watchdog_killed = False
            if ear.returncode != 0:
                ear.forced_pass = True
                out(f"[{ear.tag}] watchdog kill landed after NumCalc's log was "
                    f"complete (code {ear.returncode}); counted as a pass, not re-run")
        if ear.watchdog_killed:
            pending.append(ear)
            out(f"[{ear.tag}] stopped by RAM watchdog; will re-run after the "
                "other ear(s) finish")
        else:
            state = "finished OK" if ear.passed else \
                f"FAILED (code {ear.returncode})"
            out(f"[{ear.tag}] NumCalc {state} after {fmt_duration(elapsed(ear))}")

    def heartbeat():
        avail = avail_mem_fn()
        for ear in running:
            rss = rss_fn(ear.proc.pid) if rss_fn else None
            phase = ear.info["phase"]
            if phase == "no log yet":
                phase = "running (no log yet)"
            out(f"[{ear.tag}] {phase} | elapsed {fmt_duration(elapsed(ear))} "
                f"| NumCalc RSS {fmt_bytes(rss)} | system available {fmt_bytes(avail)}")

    def watchdog():
        nonlocal fallback, last_kill
        if len(running) < 2 or min_avail_bytes is None:
            return
        now = time.monotonic()
        if last_kill is not None and now - last_kill < watchdog_grace_s:
            return
        avail = avail_mem_fn()
        if avail is None or avail >= min_avail_bytes:
            return
        victim = max(running, key=lambda e: e.launch_seq)
        if victim.proc.poll() is not None:
            return   # already exited by itself; reap() handles it
        out(f"[{victim.tag}] [!] RAM watchdog: system available {fmt_bytes(avail)} "
            f"< {fmt_bytes(min_avail_bytes)} with {len(running)} ears running. "
            f"Killing [{victim.tag}] (started last) to free memory; unless it "
            "had already finished, it will re-run on its own afterwards "
            "(sequential fallback).")
        victim.watchdog_killed = True
        _kill_tree(victim.proc)
        fallback = True
        last_kill = now

    try:
        while pending or running:
            if fallback:
                if not running and pending:
                    start(pending.pop(0))
            else:
                while pending:
                    start(pending.pop(0))
            for ear in list(running):
                refresh_log(ear)
                if ear.proc.poll() is not None:
                    reap(ear)
            watchdog()
            now = time.monotonic()
            if running and heartbeat_s and now - last_heartbeat >= heartbeat_s:
                heartbeat()
                last_heartbeat = now
            if running:
                time.sleep(poll_s)
    finally:
        for ear in running:
            _kill_tree(ear.proc)

    print_summary(ears, out, time.monotonic() - t0)
    return ears


def _solve_summary(info):
    parts = []
    if info["iterations"] is not None:
        parts.append(f"{info['iterations']} iterations")
    if info["rel_error"] is not None:
        parts.append(f"rel. error {info['rel_error']:.2g}")
    return ", ".join(parts) or "solver finished"


def print_summary(ears, out, total_s):
    out("=== Stability test summary ===")
    for ear in ears:
        verdict = "PASS" if ear.passed else "FAIL"
        if ear.error:
            out(f"[{ear.tag}] {verdict}  {ear.error}")
            continue
        wall = (fmt_duration(ear.end_mono - ear.first_start_mono)
                if ear.end_mono and ear.first_start_mono else "n/a")
        bits = [f"rc={ear.returncode}"]
        if ear.info["iterations"] is not None:
            bits.append(_solve_summary(ear.info))
        bits.append(f"phase reached: {ear.info['phase']}")
        bits.append(f"wall {wall}")
        if ear.attempts > 1:
            bits.append(f"{ear.attempts} attempts (RAM fallback)")
        out(f"[{ear.tag}] {verdict}  " + "  ".join(bits))
    out(f"Total wall time: {fmt_duration(total_s)}")


def run_test(project_dir, numcalc_exe, **kwargs):
    """Single-project convenience wrapper. True if the test passed."""
    ears = run_ears([project_dir], default_command_builder(numcalc_exe), **kwargs)
    return all(e.passed for e in ears)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("projects", nargs="+", help="Mesh2HRTF project folder(s)")
    ap.add_argument("--numcalc", help="path to the NumCalc executable")
    ap.add_argument("--sequential", action="store_true",
                    help="run the projects one at a time")
    ap.add_argument("--heartbeat", type=float, default=DEFAULT_HEARTBEAT_S,
                    help="seconds between progress lines (default 30)")
    ap.add_argument("--min-free-gb", type=float,
                    default=DEFAULT_MIN_AVAIL_BYTES / 1024 ** 3,
                    help="RAM watchdog threshold in GB (default 4)")
    args = ap.parse_args(argv)
    if args.numcalc is None:
        if len(args.projects) != 2:
            ap.error("--numcalc is required (legacy form: <project> <numcalc_exe>)")
        args.numcalc = args.projects.pop()   # legacy <project> <exe>
    return args


def _install_signal_handlers():
    def handler(signum, frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


def main(argv=None):
    # The GUI reads us over a cp1252 pipe; never die on an unencodable char.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except Exception:
            pass
    args = parse_args(sys.argv[1:] if argv is None else argv)
    _install_signal_handlers()
    try:
        ears = run_ears(args.projects, default_command_builder(args.numcalc),
                        sequential=args.sequential, heartbeat_s=args.heartbeat,
                        min_avail_bytes=args.min_free_gb * 1024 ** 3)
    except KeyboardInterrupt:
        # run_ears' finally already killed the running NumCalc processes.
        print("[!] Interrupted - NumCalc test processes killed.", flush=True)
        return 1
    return 0 if all(e.passed for e in ears) else 1


if __name__ == "__main__":
    sys.exit(main())
