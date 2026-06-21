import pymeshlab
import json
import os
import sys
import subprocess

import numpy as np

from project_store import ProjectStore, MESH_ALIGNED, MESH_GRADED

# =============================================================================
# INSPECTION & REPAIR OVERVIEW
#
# INITIAL INSPECTION  (inspect_mesh)
#   All checks use pymeshlab on the original mesh — nothing is modified.
#
#   1. pymeshlab get_topological_measures()
#        holes, boundary_edges, connected components, non-manifold edges,
#        non-manifold vertices, unreferenced vertices, genus
#   2. pymeshlab get_geometric_measures()
#        mesh volume — a negative value means normals point inward
#   3. pymeshlab (throwaway copy) self-intersection filter
#        counts self-intersecting faces
#   4. pymeshlab (throwaway copy) meshing_remove_duplicate_vertices
#        counts duplicate vertices by diffing vertex count before vs. after
#   5. pymeshlab (throwaway copy) meshing_remove_null_faces
#        counts degenerate/zero-area faces by diffing face count before vs. after
#   6. numpy inline scan — tiny_faces
#        counts faces whose shortest edge is below MERGE_DETECT_THRESH (unit-scaled).
#        These sliver/degenerate faces are raw-scan artifacts that pymeshfix/pymeshlab
#        cannot remove; only Blender's bmesh.ops.remove_doubles collapses them.
#
#   Steps 3-5 apply the repair filter on a temporary MeshSet purely to count
#   defects; the original mesh file is never written during inspection.
#
# AUTO-REPAIR  (repair_mesh)
#   Primary engine — pymeshfix:
#     Extracts vertex/face matrices via pymeshlab, hands them to
#     pymeshfix.MeshFix.repair(), then saves the result with pymeshlab.
#     Fixes: self-intersections, non-manifold edges/vertices, open boundaries.
#     Preserves genus — topological tunnels (genus > 0) survive and must be
#     removed separately by the cut+cap step in mesh_problem_viewer.py.
#
#   Fallback engine — pymeshlab filter chain (used if pymeshfix errors):
#     1. meshing_remove_duplicate_vertices
#     2. meshing_remove_unreferenced_vertices
#     3. meshing_remove_null_faces
#     4. meshing_repair_non_manifold_edges
#     5. meshing_repair_non_manifold_vertices
#     6. meshing_close_holes (maxholesize=30)
#     7. meshing_re_orient_faces_coherently
#     NOTE: does NOT fix self-intersections.
#
# FRONT-DOOR IMPORT  (import_mesh)
#   Runs at Browse-time before alignment.  Flow for each imported mesh:
#
#   1. inspect_mesh (full check, including tiny_faces count).
#   2. If tiny_faces > 0:
#        _run_blender_dissolve → blender_scripts/bmesh_cleanup.py (headless):
#          a. bmesh.ops.remove_doubles(dist = MERGE_FIX_THRESH × unit-scale)
#             Collapses vertices that are closer than `dist`, eliminating
#             sliver triangles whose edges are all sub-threshold.
#          b. bmesh.ops.triangulate — re-triangulates any n-gons that
#             remove_doubles may produce by collapsing an edge of a quad.
#        Blender is a hard-require: CalledProcessError propagates unchanged.
#   3. pymeshfix repair (primary) / pymeshlab filter chain (fallback).
#        Fixes self-intersections, non-manifold edges/vertices, open holes.
#        Genus (topological tunnels) is preserved — tunnels cannot be removed
#        until the mesh is aligned (Step 2 cut & cap in mesh_problem_viewer.py).
#   4. Re-inspect the cleaned mesh → write import_report.json.
#
#   Genus/tunnel warnings at import are warn-only (exit 0).  They are flagged
#   critical only in the Step 2 Inspect & Fix stage.
# =============================================================================

# ================= CONFIGURATION =================
MERGE_DETECT_THRESH = .295
MERGE_FIX_THRESH = .305
# ================================================= 


def _bbox_unit_scale(ms):
    """Return 1.0 if the mesh is in millimetres, 0.001 if in metres.

    Distinguishes mm vs metres purely from the bounding-box diagonal:
    - mm meshes (head-only or head+torso+body): diag >= ~200
    - metre meshes (even a full 1.8 m body): diag <= ~2
    A cutoff of 10 sits safely in the gap between those ranges.

    Verified: FABIAN_22k_HATO0.stl (head+torso) diag ~1029 mm → classifies mm.
    A naive cutoff of 1.0 would misclassify a metre full-body scan (diag ~1.8 m).
    """
    vm = ms.current_mesh().vertex_matrix()
    diag = float(np.linalg.norm(vm.max(axis=0) - vm.min(axis=0)))
    return 1.0 if diag >= 10.0 else 0.001


def _get_si_filter_name(ms):
    """Try newer PyMeshLab self-intersection filter name, fall back to older API."""
    for name in ('compute_selection_by_self_intersections_per_face',
                 'select_self_intersecting_faces'):
        try:
            ms.apply_filter(name)
            return name
        except Exception:
            continue
    return None


def inspect_mesh(path, *, max_freq_hz=None):
    """
    Inspect a mesh for issues that would cause NumCalc (BEM) to crash.
    Returns a report dict with severity, critical/minor issue lists, counts, summary.
    """
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(path))

    counts = {
        "holes": 0,
        "boundary_edges": 0,
        "components": 0,
        "non_manifold_edges": 0,
        "non_manifold_verts": 0,
        "si_faces": 0,
        "dup_verts": 0,
        "null_faces": 0,
        "unreferenced_verts": 0,
        "genus": 0,
        "volume": None,
        "tiny_faces": 0,
    }

    # --- tiny_faces note ---
    # Detection threshold: MERGE_DETECT_THRESH. Merge threshold: MERGE_FIX_THRESH.
    # blender_scripts/bmesh_cleanup.py uses remove_doubles → triangulate.
    # Triangulate added to avoid a known failure mode: n-gons after merging.

    # Track outside the try block so genus classification can use it
    is_two_manifold = True

    # --- Topological measures ---
    try:
        topo = ms.get_topological_measures()
        counts["holes"] = int(topo.get("number_of_holes", 0))
        counts["boundary_edges"] = int(topo.get("boundary_edges", 0))
        counts["components"] = int(topo.get("number_of_connected_components", 0))
        counts["unreferenced_verts"] = int(topo.get("number_unreferenced_vertices", 0))
        is_two_manifold = bool(topo.get("is_mesh_two_manifold", True))

        # Genus — try from get_ dict first, fall back to compute_ (older PyMeshLab name)
        genus_val = topo.get("genus", None)
        if genus_val is None:
            try:
                topo2 = ms.compute_topological_measures()
                genus_val = topo2.get("genus", None)
            except Exception:
                pass
        if genus_val is not None:
            counts["genus"] = int(genus_val)

        # Per-element non-manifold counts are already provided by the topological
        # measures dict (the old select_non_manifold_* filters don't exist in
        # current PyMeshLab and threw, leaving a useless -1 "unknown count").
        counts["non_manifold_edges"] = int(topo.get("non_two_manifold_edges", 0))
        counts["non_manifold_verts"] = int(topo.get("non_two_manifold_vertices", 0))
    except Exception as e:
        pass

    # --- Geometric measures (volume sign → normal orientation) ---
    try:
        geo = ms.get_geometric_measures()
        v = geo.get("mesh_volume", None)
        if v is not None:
            counts["volume"] = float(v)
    except Exception:
        pass

    # --- Self-intersections ---
    try:
        ms2 = pymeshlab.MeshSet()
        ms2.load_new_mesh(str(path))
        si_name = _get_si_filter_name(ms2)
        if si_name:
            counts["si_faces"] = int(ms2.current_mesh().selected_face_number())
    except Exception:
        pass

    # --- Duplicate vertices (minor) ---
    try:
        ms3 = pymeshlab.MeshSet()
        ms3.load_new_mesh(str(path))
        n_before = ms3.current_mesh().vertex_number()
        ms3.meshing_remove_duplicate_vertices()
        n_after = ms3.current_mesh().vertex_number()
        counts["dup_verts"] = max(0, n_before - n_after)
    except Exception:
        pass

    # --- Null / degenerate faces (minor) ---
    try:
        ms4 = pymeshlab.MeshSet()
        ms4.load_new_mesh(str(path))
        n_before = ms4.current_mesh().face_number()
        ms4.meshing_remove_null_faces()
        n_after = ms4.current_mesh().face_number()
        counts["null_faces"] = max(0, n_before - n_after)
    except Exception:
        pass

    # --- Tiny / sliver faces (minor) ---
    # Counts faces whose shortest edge is below MERGE_DETECT_THRESH (unit-scaled).
    # These are raw-scan artifacts that pymeshfix/pymeshlab cannot remove;
    # only Blender's bmesh.ops.remove_doubles collapses them reliably.
    try:
        unit_scale = _bbox_unit_scale(ms)
        tiny_threshold = MERGE_DETECT_THRESH * unit_scale  # Detection threshold slightly weaker than fix threshold
        vm = ms.current_mesh().vertex_matrix()
        fm = ms.current_mesh().face_matrix()
        if len(fm) > 0:
            v0 = vm[fm[:, 0]]
            v1 = vm[fm[:, 1]]
            v2 = vm[fm[:, 2]]
            min_edge = np.minimum(
                np.minimum(np.linalg.norm(v1 - v0, axis=1),
                           np.linalg.norm(v2 - v1, axis=1)),
                np.linalg.norm(v0 - v2, axis=1),
            )
            counts["tiny_faces"] = int(np.sum(min_edge < tiny_threshold))
    except Exception:
        pass

    # --- Wavelength check (info only) ---
    wavelength_warning = None
    if max_freq_hz and counts.get("volume") is not None:
        try:
            geo2 = ms.get_geometric_measures()
            avg_edge = geo2.get("avg_edge_length", None)
            if avg_edge:
                speed_of_sound = 343.0
                wavelength_min = speed_of_sound / max_freq_hz
                elems_per_wavelength = wavelength_min / avg_edge
                if elems_per_wavelength < 6:
                    wavelength_warning = (
                        f"avg edge {avg_edge:.4f} → only {elems_per_wavelength:.1f} "
                        f"elements/wavelength at {max_freq_hz} Hz (need ≥6)"
                    )
        except Exception:
            pass

    # --- Classify severity ---
    critical = []
    minor = []

    if counts["holes"] > 0:
        critical.append({"issue": f"{counts['holes']} holes (open boundary / non-watertight)", "count": counts["holes"]})
    elif counts["boundary_edges"] > 0:
        # Some PyMeshLab builds don't report number_of_holes; boundary_edges still
        # flags an open (non-watertight) surface, e.g. a crack or partial hole.
        critical.append({"issue": f"{counts['boundary_edges']} boundary edge(s) (open surface / non-watertight)", "count": counts["boundary_edges"]})
    if counts["non_manifold_edges"] > 0:
        critical.append({"issue": f"{counts['non_manifold_edges']} non-manifold edge(s)", "count": counts["non_manifold_edges"]})
    if counts["non_manifold_verts"] > 0:
        critical.append({"issue": f"{counts['non_manifold_verts']} non-manifold vertex/vertices", "count": counts["non_manifold_verts"]})
    if counts["components"] > 1:
        critical.append({"issue": f"{counts['components']} disconnected components", "count": counts["components"]})
    if counts["si_faces"] > 0:
        critical.append({"issue": f"{counts['si_faces']} self-intersecting face(s)", "count": counts["si_faces"]})
    if counts["volume"] is not None and counts["volume"] < 0:
        critical.append({"issue": "negative mesh volume (normals point inward)", "count": 1})
    # Tunnel/handle detection — genus only meaningful on a watertight, 2-manifold mesh.
    # On a mesh with boundary edges the genus value is dominated by open edges, not real
    # handles, so we suppress it to avoid false alarms (those meshes are already flagged
    # critical for holes/non-manifold above).
    if counts["genus"] > 0 and counts["holes"] == 0 and counts["boundary_edges"] == 0 and is_two_manifold:
        critical.append({
            "issue": (
                f"{counts['genus']} topological tunnel/handle(s) (genus={counts['genus']}) - "
                "scanning artifact; fix by sculpting in Blender"
            ),
            "count": counts["genus"],
        })

    if counts["dup_verts"] > 0:
        minor.append({"issue": f"{counts['dup_verts']} duplicate vertex/vertices", "count": counts["dup_verts"]})
    if counts["null_faces"] > 0:
        minor.append({"issue": f"{counts['null_faces']} null/degenerate face(s)", "count": counts["null_faces"]})
    if counts["unreferenced_verts"] > 0:
        minor.append({"issue": f"{counts['unreferenced_verts']} unreferenced vertex/vertices", "count": counts["unreferenced_verts"]})
    if counts["tiny_faces"] > 0:
        minor.append({
            "issue": (
                f"{counts['tiny_faces']} tiny/sliver face(s) (min edge < {MERGE_DETECT_THRESH} mm) — "
                "Blender merge-by-distance (remove_doubles) required to fix"
            ),
            "count": counts["tiny_faces"],
        })
    if wavelength_warning:
        minor.append({"issue": f"Mesh density low: {wavelength_warning}", "count": 0})

    if critical:
        severity = "critical"
    elif minor:
        severity = "minor"
    else:
        severity = "ok"

    report = {
        "severity": severity,
        "critical": critical,
        "minor": minor,
        "counts": counts,
        "summary": format_report({"severity": severity, "critical": critical, "minor": minor}),
    }
    return report


def format_report(report):
    """Return a human-readable text block from an inspect_mesh report dict.

    Uses ASCII markers only: the GUI captures this via a subprocess pipe that
    decodes with the Windows console codepage (cp1252), which cannot encode
    glyphs like the check/cross/warning symbols. ASCII keeps it crash-proof
    across every console and the GUI log.
    """
    lines = [f"[MESH QUALITY] severity={report['severity']}"]
    for item in report.get("critical", []):
        lines.append(f"  [X] CRITICAL: {item['issue']}")
    for item in report.get("minor", []):
        lines.append(f"  [!] minor: {item['issue']}")
    if not report.get("critical") and not report.get("minor"):
        lines.append("  [OK] No issues found.")
    return "\n".join(lines)


def _is_tunnel_issue(issue_text):
    """True if a critical issue describes a topological tunnel/handle (genus>0).

    Tunnels are not fixable by geometric repair (only a cut+cap removes them), so
    a cleaned-but-genus>0 mesh is still a *successful* geometric repair — it just
    needs the tunnel-loop step next. Mirrors the wording used in align_head.py.
    """
    t = issue_text.lower()
    return "tunnel" in t or "genus" in t


def _repair_pymeshfix(in_path, out_path):
    """Primary repair: pymeshfix rebuilds a watertight 2-manifold, removing
    self-intersections, non-manifold elements and boundaries while preserving
    genus (tunnels). Raises if pymeshfix is unavailable or errors."""
    import pymeshfix

    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(in_path))
    cm = ms.current_mesh()
    mf = pymeshfix.MeshFix(cm.vertex_matrix(), cm.face_matrix())
    mf.repair()

    out = pymeshlab.MeshSet()
    out.add_mesh(pymeshlab.Mesh(mf.points, mf.faces))
    out.save_current_mesh(str(out_path))


def _repair_pymeshlab(in_path, out_path):
    """Fallback repair: the legacy PyMeshLab filter chain. Note this chain does
    NOT remove self-intersections, so it can leave SI criticals behind."""
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(in_path))

    repair_steps = [
        ('meshing_remove_duplicate_vertices', {}),
        ('meshing_remove_unreferenced_vertices', {}),
        ('meshing_remove_null_faces', {}),
        ('meshing_repair_non_manifold_edges', {}),
        ('meshing_repair_non_manifold_vertices', {}),
        ('meshing_close_holes', {'maxholesize': 30}),
        ('meshing_re_orient_faces_coherently', {}),
    ]

    for filter_name, kwargs in repair_steps:
        try:
            ms.apply_filter(filter_name, **kwargs)
        except Exception:
            # Some PyMeshLab versions use different names; skip silently
            pass

    ms.save_current_mesh(str(out_path))


def repair_mesh(in_path, out_path):
    """
    Repair in_path, saving to out_path. Uses pymeshfix as the primary engine
    (removes self-intersections + non-manifold + boundaries, preserves genus),
    falling back to the legacy PyMeshLab chain if pymeshfix is unavailable/fails.
    Returns {"before": report, "after": report, "success": bool, "engine": str}.
    Never overwrites in_path.

    success = no critical issues remain OTHER than topological tunnels (genus>0),
    which are removed in the separate tunnel-loop / cut+cap step.
    """
    before = inspect_mesh(in_path)

    engine = "pymeshfix"
    try:
        _repair_pymeshfix(in_path, out_path)
    except Exception:
        engine = "pymeshlab"
        _repair_pymeshlab(in_path, out_path)

    after = inspect_mesh(out_path)
    non_tunnel_criticals = [c for c in after.get("critical", []) if not _is_tunnel_issue(c["issue"])]
    success = len(non_tunnel_criticals) == 0
    return {"before": before, "after": after, "success": success, "engine": engine}


_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0


def _run_blender_dissolve(in_path, out_path, blender_exe, dist):
    """Run bmesh_cleanup.py headlessly in Blender to collapse sliver/tiny faces via
    merge-by-distance (bmesh.ops.remove_doubles + bmesh.ops.triangulate).

    Blocking call. Raises subprocess.CalledProcessError on failure (hard-require:
    callers must not silently skip — sliver collapse only works in Blender).

    Args:
        in_path:     Input mesh path (PLY).
        out_path:    Output mesh path (PLY, written on success).
        blender_exe: Absolute path to the Blender executable.
        dist:        Merge distance in mesh units (already unit-scaled by caller,
                     equals MERGE_FIX_THRESH × unit_scale).
    """
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    cleanup_script = os.path.join(scripts_dir, "blender_scripts", "bmesh_cleanup.py")

    cmd = [
        blender_exe,
        "--background",
        "--python", cleanup_script,
        "--",
        str(in_path),
        str(out_path),
        str(dist),
    ]
    subprocess.run(cmd, check=True, creationflags=_CREATE_NO_WINDOW)


def import_mesh(dest_path, blender_exe):
    """Front-door mesh import: inspect → Blender sliver collapse (if needed) →
    pymeshfix repair → re-inspect. Overwrites dest_path in place on success.

    Writes import_report.json to the mesh directory. Prints ASCII progress lines
    so the GUI log stream stays informative. Returns the report dict.

    success semantics: Blender merge-by-distance failure raises (hard-require). A
    surviving genus (topological tunnel) is warn-only — the import still succeeds
    because tunnels can only be fixed post-align via the cut & cap step.
    """
    def log(msg):
        print(msg, flush=True)

    mesh_dir = os.path.dirname(os.path.abspath(str(dest_path)))
    filename = os.path.basename(str(dest_path))

    log(f"--> Import: inspecting {filename}...")
    before = inspect_mesh(dest_path)
    log(format_report(before))

    # Derive unit scale for the merge distance from the raw mesh bbox diagonal.
    ms_tmp = pymeshlab.MeshSet()
    ms_tmp.load_new_mesh(str(dest_path))
    unit_scale = _bbox_unit_scale(ms_tmp)
    dissolve_dist = MERGE_FIX_THRESH * unit_scale

    blender_ran = False

    if before["counts"].get("tiny_faces", 0) > 0:
        log(f"--> {before['counts']['tiny_faces']} tiny/sliver face(s) detected — "
            f"running Blender merge-by-distance collapse (dist={dissolve_dist:.6f})...")

        # Convert to a temp PLY for Blender (handles non-PLY originals gracefully).
        tmp_in  = str(dest_path) + "_bm_in.ply"
        tmp_out = str(dest_path) + "_bm_out.ply"
        try:
            ms_exp = pymeshlab.MeshSet()
            ms_exp.load_new_mesh(str(dest_path))
            ms_exp.save_current_mesh(tmp_in)

            _run_blender_dissolve(tmp_in, tmp_out, blender_exe, dissolve_dist)
            blender_ran = True

            # Load result and save back to dest_path (preserving original format).
            ms_res = pymeshlab.MeshSet()
            ms_res.load_new_mesh(tmp_out)
            ms_res.save_current_mesh(str(dest_path))
            log("   [OK] Blender merge-by-distance collapse complete.")
        finally:
            for p in (tmp_in, tmp_out):
                if os.path.exists(p):
                    os.remove(p)
    else:
        log("   [i] No tiny faces detected — Blender sliver collapse skipped.")

    # pymeshfix repair (preserves genus; handles SI/non-manifold/boundaries).
    log("--> Auto-repair (pymeshfix primary)...")
    base, ext = os.path.splitext(str(dest_path))
    repaired_path = base + "_repaired" + (ext if ext else ".ply")

    result = repair_mesh(dest_path, repaired_path)
    if result["success"]:
        os.replace(repaired_path, str(dest_path))
        log(f"   [OK] Repair succeeded (engine={result['engine']}).")
    else:
        if os.path.exists(repaired_path):
            os.remove(repaired_path)
        log("   [!] Some issues remain after repair (may include tunnels — warn-only).")

    # Re-inspect the final cleaned file.
    log("--> Re-inspecting cleaned mesh...")
    after = inspect_mesh(dest_path)
    log(format_report(after))

    report = {
        "filename": filename,
        "blender_dissolve_run": blender_ran,
        "engine": result["engine"],
        "before": {
            "severity": before["severity"],
            "critical": before["critical"],
            "minor": before["minor"],
            "counts": before["counts"],
        },
        "after": {
            "severity": after["severity"],
            "critical": after["critical"],
            "minor": after["minor"],
            "counts": after["counts"],
        },
    }

    report_path = os.path.join(mesh_dir, "import_report.json")
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=4)
    log(f"[IMPORT] Complete. Report saved to {report_path}")

    return report


def repair_graded(mesh_dir):
    """
    Repair both Left_Graded.ply and Right_Graded.ply in mesh_dir.
    Overwrites originals on success. Rewrites mesh_check.json.
    Exits non-zero if criticals remain after repair.
    """
    def log(msg):
        print(msg, flush=True)

    any_critical = False
    mesh_check = {}

    for side in ("Left", "Right"):
        orig = os.path.join(mesh_dir, f"{side}_Graded.ply")
        if not os.path.exists(orig):
            log(f"[!] {side}_Graded.ply not found, skipping.")
            continue

        repaired = orig + "_repaired.ply"
        log(f"--> Repairing {side}_Graded.ply...")
        result = repair_mesh(orig, repaired)

        log(format_report(result["after"]))

        if result["success"]:
            os.replace(repaired, orig)
            log(f"   [{side}] Repair succeeded — file updated.")
            sev = "repaired"
        else:
            if os.path.exists(repaired):
                os.remove(repaired)
            log(f"   [{side}] Critical issues remain after repair.")
            sev = "critical"
            any_critical = True

        mesh_check[side.lower()] = (sev, result["after"]["counts"])

    ProjectStore.for_mesh_dir(mesh_dir).write_check(MESH_GRADED, mesh_check)

    if any_critical:
        log("[MESH_CHECK] Critical issues remain after repair. The Blender step is still blocked.")
        sys.exit(1)
    else:
        log("[MESH_CHECK] Repair complete. All critical issues resolved.")


def _write_aligned_check(mesh_dir, report):
    """Write aligned_check.json for the standalone Inspect & Fix step."""
    ProjectStore.for_mesh_dir(mesh_dir).write_check(
        MESH_ALIGNED, {"aligned": (report["severity"], report["counts"])})


def inspect_aligned(mesh_dir):
    """
    Inspect aligned_head.ply in mesh_dir, print the report, and write
    aligned_check.json. Exits non-zero if critical (used by the GUI's
    standalone "Inspect & Fix Mesh" step).
    """
    def log(msg):
        print(msg, flush=True)

    path = os.path.join(mesh_dir, "aligned_head.ply")
    if not os.path.exists(path):
        log("[!] aligned_head.ply not found.")
        sys.exit(1)

    report = inspect_mesh(path)
    log(format_report(report))
    _write_aligned_check(mesh_dir, report)
    sys.exit(0 if report["severity"] != "critical" else 1)


def repair_aligned(mesh_dir):
    """
    Repair aligned_head.ply in place (pymeshfix primary), then rewrite
    aligned_check.json from a fresh inspection. Geometry criticals (self-
    intersections, non-manifold, boundaries) are fixed; topological tunnels
    remain critical and are handled by the cut+cap / tunnel-loop step.
    Exits non-zero if any critical (incl. tunnels) remains.
    """
    def log(msg):
        print(msg, flush=True)

    orig = os.path.join(mesh_dir, "aligned_head.ply")
    if not os.path.exists(orig):
        log("[!] aligned_head.ply not found.")
        sys.exit(1)

    repaired = orig + "_repaired.ply"
    log("--> Repairing aligned_head.ply...")
    result = repair_mesh(orig, repaired)
    log(format_report(result["after"]))

    if result["success"]:
        os.replace(repaired, orig)
        log(f"   Repair succeeded (engine={result['engine']}) — file updated.")
    else:
        if os.path.exists(repaired):
            os.remove(repaired)
        log("   Critical (non-tunnel) issues remain after repair.")

    # Re-inspect the (possibly updated) file for the authoritative severity.
    report = inspect_mesh(orig)
    _write_aligned_check(mesh_dir, report)
    if report["severity"] == "critical":
        log("[MESH_CHECK] Critical issues remain (tunnels require the cut+cap step).")
        sys.exit(1)
    else:
        log("[MESH_CHECK] Aligned mesh is clean. You may proceed to grading.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mesh quality inspector / repair tool")
    sub = parser.add_subparsers(dest="cmd")

    p_inspect = sub.add_parser("inspect", help="Inspect a mesh and print a quality report")
    p_inspect.add_argument("path", help="Path to mesh file (.ply, .stl, etc.)")

    p_repair = sub.add_parser("repair", help="Repair a mesh and save to a new path")
    p_repair.add_argument("in_path", help="Input mesh path")
    p_repair.add_argument("out_path", help="Output mesh path")

    p_graded = sub.add_parser("repair_graded", help="Repair Left/Right_Graded.ply in a mesh dir")
    p_graded.add_argument("mesh_dir", help="Directory containing Left_Graded.ply / Right_Graded.ply")

    p_iali = sub.add_parser("inspect_aligned", help="Inspect aligned_head.ply in a mesh dir, write aligned_check.json")
    p_iali.add_argument("mesh_dir", help="Directory containing aligned_head.ply")

    p_rali = sub.add_parser("repair_aligned", help="Repair aligned_head.ply in place, write aligned_check.json")
    p_rali.add_argument("mesh_dir", help="Directory containing aligned_head.ply")

    p_imp = sub.add_parser("import_mesh",
                            help="Front-door import: inspect, Blender sliver collapse (merge-by-distance), repair, re-inspect")
    p_imp.add_argument("dest_path", help="Mesh file to clean in place (already copied to Meshes folder)")
    p_imp.add_argument("blender_exe", help="Absolute path to blender(.exe)")

    args = parser.parse_args()

    if args.cmd == "inspect":
        report = inspect_mesh(args.path)
        print(format_report(report))
        sys.exit(0 if report["severity"] != "critical" else 1)

    elif args.cmd == "repair":
        result = repair_mesh(args.in_path, args.out_path)
        print("BEFORE:")
        print(format_report(result["before"]))
        print("\nAFTER:")
        print(format_report(result["after"]))
        sys.exit(0 if result["success"] else 1)

    elif args.cmd == "repair_graded":
        repair_graded(args.mesh_dir)

    elif args.cmd == "inspect_aligned":
        inspect_aligned(args.mesh_dir)

    elif args.cmd == "repair_aligned":
        repair_aligned(args.mesh_dir)

    elif args.cmd == "import_mesh":
        report = import_mesh(args.dest_path, args.blender_exe)
        # Genus (tunnel) is warn-only — exit 0 even when genus > 0.
        # Non-zero only on hard failure (Blender crash propagates as exception).
        sys.exit(0)

    else:
        parser.print_help()
        sys.exit(1)
