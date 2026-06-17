import pymeshlab
import json
import os
import sys


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
        "components": 0,
        "non_manifold_edges": 0,
        "non_manifold_verts": 0,
        "si_faces": 0,
        "dup_verts": 0,
        "null_faces": 0,
        "unreferenced_verts": 0,
        "genus": 0,
        "volume": None,
    }

    # Track outside the try block so genus classification can use it
    is_two_manifold = True

    # --- Topological measures ---
    try:
        topo = ms.get_topological_measures()
        counts["holes"] = int(topo.get("number_of_holes", 0))
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

        if not is_two_manifold:
            # Try to get per-element counts for a more informative report
            try:
                ms.apply_filter('select_non_manifold_edges')
                counts["non_manifold_edges"] = int(ms.current_mesh().selected_edge_number())
            except Exception:
                counts["non_manifold_edges"] = -1  # unknown count but present

            try:
                ms.apply_filter('select_non_manifold_vertices')
                counts["non_manifold_verts"] = int(ms.current_mesh().selected_vertex_number())
            except Exception:
                counts["non_manifold_verts"] = -1
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
    if counts["non_manifold_edges"] != 0:
        n = counts["non_manifold_edges"]
        label = "unknown count of" if n == -1 else str(n)
        critical.append({"issue": f"{label} non-manifold edge(s)", "count": n})
    if counts["non_manifold_verts"] != 0:
        n = counts["non_manifold_verts"]
        label = "unknown count of" if n == -1 else str(n)
        critical.append({"issue": f"{label} non-manifold vertex/vertices", "count": n})
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
    if counts["genus"] > 0 and counts["holes"] == 0 and is_two_manifold:
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


def repair_mesh(in_path, out_path):
    """
    Run a PyMeshLab repair chain on in_path, saving to out_path.
    Returns {"before": report, "after": report, "success": bool}.
    Never overwrites in_path.
    """
    before = inspect_mesh(in_path)

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

    after = inspect_mesh(out_path)
    success = after["severity"] != "critical"
    return {"before": before, "after": after, "success": success}


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

        mesh_check[side.lower()] = {
            "severity": sev,
            "counts": result["after"]["counts"],
        }

    check_path = os.path.join(mesh_dir, "mesh_check.json")
    with open(check_path, "w") as f:
        json.dump(mesh_check, f, indent=4)

    if any_critical:
        log("[MESH_CHECK] Critical issues remain after repair. Step 3 is still blocked.")
        sys.exit(1)
    else:
        log("[MESH_CHECK] Repair complete. All critical issues resolved.")


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

    else:
        parser.print_help()
        sys.exit(1)
