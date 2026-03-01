import os
import sys
import subprocess
import json

def run_test(project_dir, numcalc_exe):
    print(f"\n--- Testing Project: {os.path.basename(project_dir)} ---")
    
    # 1. Determine Test Index based on Resolution
    # Look for project.json in the project root (parent of 'Exports')
    # project_dir is typically ".../Exports/Left_Project"
    base_dir = os.path.dirname(os.path.dirname(project_dir))
    project_json = os.path.join(base_dir, "project.json")
    
    test_idx = "140" # Default Standard (21kHz)
    
    if os.path.exists(project_json):
        try:
            with open(project_json, 'r') as f:
                data = json.load(f)
                if data.get("project_resolution") == "lowres":
                    test_idx = "107" # Lowres (16.05kHz)
                    print("   [Mode] Lowres Detected (Target Index: 107)")
                else:
                    print("   [Mode] Standard Detected (Target Index: 140)")
        except:
            print("   [!] Could not read project.json, defaulting to Standard.")
    
    # 2. Target Directory
    source_dir = os.path.join(project_dir, "NumCalc", "source_1")
    if not os.path.exists(source_dir):
        print(f"[ERROR] Source folder not found: {source_dir}")
        return False
        
    # 3. Command
    cmd = [numcalc_exe, "-istart", test_idx, "-iend", test_idx]
    
    print(f"   [>] Executing: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, cwd=source_dir, capture_output=False)
        if result.returncode == 0:
            print("   [SUCCESS] Test run completed.")
            return True
        else:
            print(f"   [FAIL] NumCalc crashed (Code {result.returncode})")
            return False
            
    except Exception as e:
        print(f"   [ERROR] Execution failed: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: run_numcalc_test.py <project_folder_path> <numcalc_exe_path>")
        sys.exit(1)
        
    project_path = sys.argv[1]
    nc_exe = sys.argv[2]
    run_test(project_path, nc_exe)