import argparse
import os
import sys
import shutil
import json

def ensure_mesh2hrtf_import(m2h_path):
    """Safely import mesh2hrtf from a user-specified path."""
    try:
        import mesh2hrtf as m2h
        print("[+] Mesh2HRTF library imported successfully.")
        return m2h
    except ImportError:
        print(f"[i] Library not found. Adding path: {m2h_path}")
        # Standard Mesh2HRTF directory structure
        sys.path.append(m2h_path)
        sys.path.append(os.path.dirname(m2h_path))
        
        try:
            import mesh2hrtf as m2h
            print("[+] Mesh2HRTF library imported successfully after path update.")
            return m2h
        except ImportError as e:
            print(f"[FATAL] Could not import mesh2hrtf. Check your path.\nError: {e}")
            sys.exit(1)

def get_freq_steps(proj_path, min_freq, max_freq):
    """Parse parameters.json to map frequency range to step indices."""
    params_path = os.path.join(proj_path, "parameters.json")
    if not os.path.exists(params_path):
        print(f"[!] Warning: {params_path} not found.")
        return [], {}, {}

    try:
        with open(params_path, 'r') as f:
            data = json.load(f)
            freqs = data.get("frequencies", [])
    except Exception as e:
        print(f"[ERROR] Could not read frequencies from {params_path}: {e}")
        return [], {}, {}

    valid_steps = []
    valid_freqs = []
    
    for i, freq in enumerate(freqs):
        if min_freq <= freq <= max_freq:
            step = i + 1  # Mesh2HRTF uses 1-based indexing for steps in VTK naming
            valid_steps.append(step)
            valid_freqs.append(freq)
            
    if not valid_steps:
        return [], {}, {}

    # Original Ascending Mapping (for verification)
    original_map = {}
    for step, freq in zip(valid_steps, valid_freqs):
        original_map[step] = freq

    # Mesh2HRTF vtk_export function outputs data in REVERSE frequency order 
    # relative to the range. So the lowest step index actually has the highest freq data.
    # We map ascending steps to descending frequencies to correct this.
    reversed_freqs = list(reversed(valid_freqs))
    
    applied_map = {}
    for step, freq in zip(valid_steps, reversed_freqs):
        applied_map[step] = freq
            
    return valid_steps, applied_map, original_map

def main():
    parser = argparse.ArgumentParser(description="Export Mesh2HRTF simulation results to VTK format.")
    parser.add_argument("--left", required=True, help="Path to Left Project folder")
    parser.add_argument("--right", required=True, help="Path to Right Project folder")
    parser.add_argument("--m2h_path", required=True, help="Root path to Mesh2HRTF")
    parser.add_argument("--output", required=True, help="Main project Output folder")
    parser.add_argument("--min_freq", type=float, required=True, help="Minimum frequency (Hz)")
    parser.add_argument("--max_freq", type=float, required=True, help="Maximum frequency (Hz)")
    args = parser.parse_args()

    # 1. Map frequencies to steps
    print(f"--> Analyzing frequency range: {args.min_freq}Hz to {args.max_freq}Hz")
    # We use the Left project as the reference for mapping (they should be identical)
    steps_to_export, step_to_freq_map, original_map = get_freq_steps(args.left, args.min_freq, args.max_freq)
    
    if not steps_to_export:
        print("[FATAL] No frequency steps found in the requested range.")
        sys.exit(1)
        
    print(f"--> Found {len(steps_to_export)} steps to export.")

    # 2. Import Mesh2HRTF
    m2h = ensure_mesh2hrtf_import(args.m2h_path)
    
    # Identify the correct function name (export_vtk is standard in newer versions)
    vtk_func = getattr(m2h, 'vtk_export', getattr(m2h, 'export_vtk', None))
    if not vtk_func:
        print("[FATAL] Could not find 'vtk_export' or 'export_vtk' in mesh2hrtf package.")
        sys.exit(1)

    # 3. Process Both Ears
    for proj_path, side in [(args.left, "Left"), (args.right, "Right")]:
        print(f"\n=== Processing {side} Project: {os.path.basename(proj_path)} ===")
        
        # Ensure we have the correct mapping for this side
        side_steps, side_map, side_original_map = get_freq_steps(proj_path, args.min_freq, args.max_freq)
        if not side_steps:
            print(f"[!] Warning: No steps found for {side} side in range.")
            continue

        try:
            # vtk_export usually takes frequency_steps as a list of integers: [min_step, max_step]
            step_range = [min(side_steps), max(side_steps)]
            vtk_func(folder=proj_path, mode='pressure', dB=True, frequency_steps=step_range)
            print(f"[+] VTK export command successful for {side} ear.")
        except Exception as e:
            print(f"[ERROR] VTK export failed for {side} ear: {e}")
            continue

        # 4. Move/Copy files to Output/VTK and rename
        src_vtk_dir = os.path.join(proj_path, "Output2HRTF", "vtk")
        if not os.path.exists(src_vtk_dir):
            src_vtk_dir = os.path.join(proj_path, "NumCalc", "source_1", "vtk")

        if os.path.exists(src_vtk_dir):
            dst_vtk_dir = os.path.join(args.output, "VTK", side)
            print(f"   --> Copying & Renaming VTK files to: {dst_vtk_dir}")
            
            if os.path.exists(dst_vtk_dir):
                shutil.rmtree(dst_vtk_dir)
            
            os.makedirs(os.path.dirname(dst_vtk_dir), exist_ok=True)
            shutil.copytree(src_vtk_dir, dst_vtk_dir)
            
            # Save mapping for verification
            mapping_report = {
                "requested_range_hz": {"min": args.min_freq, "max": args.max_freq},
                "mesh2hrtf_step_range": {"min_step": min(side_steps), "max_step": max(side_steps)},
                "total_files_expected": len(side_steps),
                "original_ascending_mapping": side_original_map,
                "applied_reversed_mapping_for_renaming": side_map
            }
            
            report_path = os.path.join(dst_vtk_dir, "step_to_freq_map.json")
            try:
                with open(report_path, 'w') as f:
                    json.dump(mapping_report, f, indent=4)
                print(f"   [+] Verification report saved: {os.path.basename(report_path)}")
            except Exception as e:
                print(f"   [!] Could not save verification report: {e}")

            # Recursive renaming
            rename_count = 0
            for root, dirs, files in os.walk(dst_vtk_dir):
                for filename in files:
                    if filename.endswith(".vtk") and "frequency_step_" in filename:
                        try:
                            # Extract step number from "frequency_step_N.vtk"
                            step_part = filename.replace("frequency_step_", "").replace(".vtk", "")
                            step_idx = int(step_part)
                            
                            if step_idx in side_map:
                                freq = side_map[step_idx]
                                new_name = f"{int(freq):05d}Hz.vtk"
                                os.rename(os.path.join(root, filename), os.path.join(root, new_name))
                                rename_count += 1
                        except Exception as e:
                            print(f"      [!] Error renaming {filename}: {e}")
            
            print(f"   [+] {side} VTK files ready ({rename_count} files renamed).")
        else:
            print(f"   [!] Warning: Could not find VTK output directory in {proj_path}")

    print("\n[SUCCESS] VTK export workflow complete.")

if __name__ == "__main__":
    main()
