import json
import os
import sys
import subprocess

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0


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


def run_test(project_dir, numcalc_exe):
    print(f"\n--- Testing Project: {os.path.basename(project_dir)} ---")

    # 1. Test the highest frequency in the export (index is 1-based in NumCalc)
    try:
        idx, freq = read_test_index(project_dir)
    except Exception as e:
        print(f"[ERROR] Cannot read the export's parameters.json ({e}). "
              "Re-export the project from Blender.")
        return False
    test_idx = str(idx)
    print(f"   [i] Target Index: {test_idx} ({freq} Hz)")

    # 2. Target Directory
    source_dir = os.path.join(project_dir, "NumCalc", "source_1")
    if not os.path.exists(source_dir):
        print(f"[ERROR] Source folder not found: {source_dir}")
        return False
        
    # 3. Command
    cmd = [numcalc_exe, "-istart", test_idx, "-iend", test_idx]
    
    print(f"   [>] Executing: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, cwd=source_dir, capture_output=False, creationflags=CREATE_NO_WINDOW)
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