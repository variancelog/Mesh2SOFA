import pymeshlab
import json
import os
import argparse
import subprocess
import sys

def log(msg):
    print(msg, flush=True)

def get_percentage_class():
    if hasattr(pymeshlab, 'PercentageValue'):
        return pymeshlab.PercentageValue
    elif hasattr(pymeshlab, 'Percentage'):
        return pymeshlab.Percentage
    return None

def find_project_json(start_path):
    """Searches up the directory tree for project.json"""
    curr = start_path
    for _ in range(3): # Check up to 3 levels up
        candidate = os.path.join(curr, "project.json")
        if os.path.exists(candidate):
            return candidate
        curr = os.path.dirname(curr)
    return None

def run_processing(aligned_mesh_path, grading_bin_path):
    log(f"--- Starting Processing for: {aligned_mesh_path} ---")
    
    # 1. Load Alignment Info
    info_path = aligned_mesh_path.replace(".ply", "_info.json")
    if not os.path.exists(info_path):
        log(f"[ERROR] Could not find alignment info: {info_path}")
        sys.exit(1)

    with open(info_path, 'r') as f:
        data = json.load(f)
        
    L = data["left_ear"]
    R = data["right_ear"]
    
    # 2. Determine Resolution Settings
    project_json_path = find_project_json(os.path.dirname(aligned_mesh_path))
    resolution = "standard"
    if project_json_path:
        with open(project_json_path, 'r') as f:
            pj = json.load(f)
            resolution = pj.get("project_resolution", "standard")
    
    if resolution == "lowres":
        log("   [MODE] Lowres (Max 16kHz) selected.")
        # Coarser mesh settings
        arg_min = "0.8"
        arg_max = "15.0" 
        grad_ratio = "0.30"
        target_mm_base = 1.0 # Slightly coarser intermediate mesh
    else:
        log("   [MODE] Standard (Max 21kHz) selected.")
        arg_min = "0.5"
        arg_max = "10.0"
        grad_ratio = "0.20"
        target_mm_base = 0.65

    # 3. Isotropic Remeshing
    log("   -> Step A: High-Res Isotropic Remeshing...")
    temp_highres_stl = aligned_mesh_path.replace(".ply", "_highres.stl")
    
    try:
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(aligned_mesh_path)
        
        bbox = ms.current_mesh().bounding_box()
        diag = bbox.diagonal()
        
        width = abs(L[1] - R[1])
        if width > 5.0: # Millimeters
            target_mm = target_mm_base 
            log(f"      (Units: mm | Diag: {diag:.2f} | Target: {target_mm}mm)")
        else: # Meters
            target_mm = target_mm_base / 1000.0
            arg_min = str(float(arg_min) / 1000.0)
            arg_max = str(float(arg_max) / 1000.0)
            log(f"      (Units: m | Diag: {diag:.4f} | Target: {target_mm}m)")

        target_percent_val = (target_mm / diag) * 100
        
        PercentageClass = get_percentage_class()
        if PercentageClass:
            ms.meshing_isotropic_explicit_remeshing(iterations=3, targetlen=PercentageClass(target_percent_val))
        else:
            ms.apply_filter('meshing_isotropic_explicit_remeshing', iterations=3, targetlen=target_percent_val)
        
        ms.save_current_mesh(temp_highres_stl)
        
    except Exception as e:
        log(f"[ERROR] Remeshing failed: {e}")
        sys.exit(1)

    # 4. Run Grading Tool
    log("   -> Step B: Running Grading Binary...")
    project_dir = os.path.dirname(aligned_mesh_path)
    out_L = os.path.join(project_dir, "Left_Graded.ply")
    out_R = os.path.join(project_dir, "Right_Graded.ply")

    cmd_left = [grading_bin_path, "-x", arg_min, "-y", arg_max, "-v", "-h", grad_ratio, "-s", "left", "-i", temp_highres_stl, "-o", out_L]
    cmd_right = [grading_bin_path, "-x", arg_min, "-y", arg_max, "-v", "-g", grad_ratio, "-s", "right", "-i", temp_highres_stl, "-o", out_R]

    try:
        log(f"      Grading Left Ear...")
        subprocess.run(cmd_left, check=True)
        log(f"      Grading Right Ear...")
        subprocess.run(cmd_right, check=True)
        log("      Grading Finished.")
    except Exception as e:
        log(f"[ERROR] Grading binary failed: {e}")
        sys.exit(1)

    log("--- Processing Complete ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh", help="Path to aligned_head.ply")
    parser.add_argument("binary", help="Path to hrtf_mesh_grading binary")
    args = parser.parse_args()
    run_processing(args.mesh, args.binary)