"""
inspect_flow.py -- the decision logic of the one-click Step 2 ("Inspect & Fix").

Pure Python: no tkinter, no subprocesses, no file access of its own. The GUI
hands in the parsed aligned_check.json and a small set of callbacks (`io`) and
this module decides what happens next and which plain sentences to show.

    next_action(check, repaired_this_click) -> "done" | "repair" | "viewer" | "stuck"
    describe(check)                         -> list of plain sentences
    InspectFlow(io).start()                 -> drives inspect -> repair -> viewer

`check` is the dict aligned_check.json holds: {"aligned": {"severity": ...,
"counts": {...}}}. Missing keys and unknown keys are tolerated.
"""

DONE, REPAIR, VIEWER, STUCK = "done", "repair", "viewer", "stuck"


def _info(check):
    if not isinstance(check, dict):
        return {}
    info = check.get("aligned")
    return info if isinstance(info, dict) else {}


def _counts(check):
    c = _info(check).get("counts")
    return c if isinstance(c, dict) else {}


def _n(counts, key):
    """A count as int. Missing, unparsable AND negative values read as 0: a
    negative count is a sentinel (PyMeshLab reports -1 for "could not measure"),
    never a real number of problems, so it must not make a sentence."""
    v = counts.get(key)
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def is_critical(check):
    return _info(check).get("severity") == "critical"


def other_criticals(counts):
    """True if anything other than genus>0 makes the mesh critical (mirrors the
    critical rules of mesh_inspector.inspect_mesh)."""
    vol = counts.get("volume")
    try:
        neg_volume = vol is not None and float(vol) < 0
    except (TypeError, ValueError):
        neg_volume = False
    return (_n(counts, "holes") > 0 or _n(counts, "boundary_edges") > 0
            or _n(counts, "non_manifold_edges") > 0
            or _n(counts, "non_manifold_verts") > 0
            or _n(counts, "si_faces") > 0 or _n(counts, "components") > 1
            or neg_volume)


def tunnel_only(check):
    """True if genus>0 is the ONLY critical problem: the mesh is watertight,
    2-manifold, one piece, no self-intersections. Only then may the tunnel
    fixer open (it needs a closed surface)."""
    counts = _counts(check)
    return is_critical(check) and _n(counts, "genus") > 0 and not other_criticals(counts)


def next_action(check, repaired_this_click):
    """What the Step 2 button should do next.

    "done"   - not critical;
    "repair" - non-tunnel criticals and auto-repair has not run yet this click;
    "viewer" - tunnel-only critical: open the tunnel fixer;
    "stuck"  - non-tunnel criticals remain after auto-repair (or the check is
               missing/unreadable): ask the user. The tunnel fixer is never
               offered while the mesh is not watertight / manifold."""
    if not _info(check):
        return STUCK
    if not is_critical(check):
        return DONE
    if tunnel_only(check):
        return VIEWER
    return STUCK if repaired_this_click else REPAIR


def _plural(n, one, many):
    return one if n == 1 else many.format(n=n)


def describe(check):
    """Plain sentences for the problems in `check` (empty when there are none).
    Counts dumps belong in the log, not here. Minor issues get one summary line."""
    if not _info(check):
        return ["The mesh could not be inspected."]
    c = _counts(check)
    out = []
    genus = _n(c, "genus")
    if genus > 0 and not other_criticals(c):
        out.append(_plural(genus, "1 see-through hole artifact found.",
                           "{n} see-through hole artifacts found."))
    holes = _n(c, "holes")
    if holes > 0:
        out.append(_plural(holes, "The mesh has 1 small gap in the surface.",
                           "The mesh has {n} small gaps in the surface."))
    elif _n(c, "boundary_edges") > 0:
        out.append("The mesh surface is not closed (it has open edges).")
    nm = _n(c, "non_manifold_edges") + _n(c, "non_manifold_verts")
    if nm > 0:
        out.append(_plural(nm, "1 spot of the surface is pinched or folded.",
                           "{n} spots of the surface are pinched or folded."))
    comps = _n(c, "components")
    if comps > 1:
        out.append(f"The mesh is in {comps} separate pieces.")
        if _n(c, "large_pieces") > 1:
            out.append("Repair won't delete a large piece automatically; remove the "
                       "unwanted piece in Blender and import again.")
    si = _n(c, "si_faces")
    if si > 0:
        out.append(_plural(si, "1 face crosses through another face.",
                           "{n} faces cross through each other."))
    vol = c.get("volume")
    try:
        if vol is not None and float(vol) < 0:
            out.append("The mesh is inside out (its surface faces inward).")
    except (TypeError, ValueError):
        pass
    if is_critical(check) and not out:
        out.append("The mesh has a problem the checker could not measure.")
    if any(_n(c, k) > 0 for k in ("dup_verts", "null_faces", "unreferenced_verts",
                                  "tiny_faces")):
        out.append("There are also a few minor clean-up items (they don't block "
                   "the next step).")
    return out


NEEDED, DONE_STATE = "needed", "done"


def _import_clean(import_report):
    """True if import_report.json (written by mesh_inspector.import_mesh) says
    the mesh was clean AFTER import: not critical and genus 0. Uses the
    recorded after-severity; falls back to the counts only if severity is
    absent. Anything missing, unreadable or unclear is not clean."""
    if not isinstance(import_report, dict):
        return False
    after = import_report.get("after")
    if not isinstance(after, dict):
        return False
    counts = after.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    try:
        genus_bad = int(counts.get("genus") or 0) > 0
    except (TypeError, ValueError):
        return False
    if genus_bad:
        return False
    severity = after.get("severity")
    if severity in ("ok", "minor"):
        return True
    if severity is not None:        # "critical" or something unknown
        return False
    # No severity recorded: judge from the counts. Negative = unknown = not clean.
    if not counts:
        return False
    for k, v in counts.items():
        if k == "volume":
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
            return False
    return not other_criticals(counts)


def _report_is_for(import_report, raw_name):
    """True if the import report names the mesh that is the project's current
    raw mesh (basename compare). A stale report from an earlier import, or one
    with no filename, is not trusted."""
    import os
    if not isinstance(import_report, dict) or not raw_name:
        return False
    name = import_report.get("filename")
    if not isinstance(name, str) or not name:
        return False
    return (os.path.normcase(os.path.basename(name))
            == os.path.normcase(os.path.basename(str(raw_name))))


def step2_state(import_report, aligned_check, raw_name):
    """Does Step 2 (Inspect & Fix) still need doing? "needed" or "done".
    Process & Grade is enabled only in the "done" state.

      aligned_check present, not critical -> done
      aligned_check present, critical     -> needed
      no aligned_check (never inspected, or cleared by re-align / Restore):
          done only if the import report shows a clean mesh after import
          (alignment only rotates/translates, so it stays valid); otherwise needed.
      missing / unreadable import report  -> needed

    raw_name is the project's current raw mesh (file name or path): an import
    report whose "filename" differs (stale, from an earlier import) or is missing
    is not trusted, so it counts as needed.

    The first two arguments are the parsed JSON dicts, or None when the file is absent or
    unreadable. An aligned_check without a recognised severity counts as absent."""
    info = _info(aligned_check)
    sev = info.get("severity")
    if sev == "critical":
        return NEEDED
    if sev in ("ok", "minor"):
        return DONE_STATE
    if _report_is_for(import_report, raw_name) and _import_clean(import_report):
        return DONE_STATE
    return NEEDED


def counts_log_line(check):
    """One technical line for the log (the counts that are not zero)."""
    c = _counts(check)
    return ", ".join(f"{k}={v}" for k, v in c.items() if v not in (0, None, False))


class InspectFlow:
    """One click of Step 2. `io` supplies (all plain callables):

      run(kind, then)      run the "inspect" or "repair" worker, then call then(rc)
      read_check()         parsed aligned_check.json or None
      clear_check()        delete aligned_check.json (a crashed inspect must not
                           leave a stale verdict)
      log(text)
      open_viewer(then)    open the tunnel fixer; then(kept: bool) when it closes
      show_stuck(sentences, retry)   dialog with "Try repair again" (calls retry) / Close
      info(title, text)    plain popup
      offer_next(sentences, fix_next)  popup with "Fix the next defect" / "Later"
      warn(title, text)    warning popup
      refresh()            restyle the workflow buttons
    """

    def __init__(self, io):
        self.io = io
        self.repaired = False   # auto-repair ran during this click
        self.changed = False    # a repair or kept fix changed the mesh this click

    # -- entry points -----------------------------------------------------
    def start(self):
        self.repaired = False
        self.changed = False
        self.io.log("--> Inspecting aligned mesh...")
        self.io.clear_check()
        self.io.run("inspect", lambda rc: self._decide())

    def retry(self):
        """"Try repair again" in the stuck dialog (a new click: one more repair)."""
        self.repaired = False
        self._repair()

    # -- internals --------------------------------------------------------
    def _repair(self):
        self.repaired = True
        self.io.log("--> Repairing the mesh automatically...")
        self.io.run("repair", self._after_repair)

    def _after_repair(self, rc):
        if rc == 0:
            self.changed = True
        self._decide()

    def _decide(self):
        check = self.io.read_check()
        self.io.refresh()
        if check is None:
            self.io.log("[!] The inspection did not finish; see the log above.")
            return
        action = next_action(check, self.repaired)
        sentences = describe(check)
        line = counts_log_line(check)
        if line and action != DONE:
            self.io.log(f"    (details: {line})")
        if action == DONE:
            self.io.log("[OK] Mesh is ready.")
            if self.changed:
                self.io.info("Mesh is ready",
                             "The mesh was repaired and is ready for Step 3 "
                             "(Process & Grade).")
        elif action == REPAIR:
            self._repair()
        elif action == VIEWER:
            self.io.log("--> " + " ".join(sentences) + " Opening the hole fixer...")
            self.io.open_viewer(self._after_viewer)
        else:
            self.io.log("[!] " + " ".join(sentences))
            self.io.show_stuck(sentences, self.retry)

    def _after_viewer(self, kept):
        if not kept:
            self.io.log("Tunnel fixer closed - mesh unchanged.")
            self.io.refresh()
            return
        self.changed = True
        self.io.log("--> Hole fix applied. Re-checking the mesh...")
        self.io.run("repair", self._after_fix)

    def _after_fix(self, rc):
        check = self.io.read_check()
        self.io.refresh()
        action = next_action(check, True)
        sentences = describe(check)
        if action == DONE:
            self.io.log("[OK] Mesh is ready.")
            self.io.info("All clear", "The see-through hole is fixed and the mesh "
                                      "re-checked: no problems remain.\n\n"
                                      "You may proceed to Step 3 (Process & Grade).")
        elif action == VIEWER:
            self.io.log("--> " + " ".join(sentences))
            self.io.offer_next(sentences, lambda: self.io.open_viewer(self._after_viewer))
        else:
            self.io.log("[!] " + " ".join(sentences))
            self.io.warn("Problems remain",
                         "The fix was saved, but the mesh still has problems:\n\n"
                         + "\n".join(sentences)
                         + "\n\nClick '2. Inspect & Fix Mesh' again to retry.")
