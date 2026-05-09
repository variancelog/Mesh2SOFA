import sofar as sf
import numpy as np
import sys

def analyze_onset(sofa_path, name):
    print(f"--- Analyzing {name} ---")
    try:
        sofa = sf.read_sofa(sofa_path)
        hrirs = sofa.Data_IR
        fs = float(sofa.Data_SamplingRate)
        M, R, N = hrirs.shape
        print(f"Shape (M: Measurements, R: Receivers, N: Samples): {hrirs.shape}")
        print(f"Sampling Rate: {fs} Hz")

        # Find the global peak across all measurements and receivers
        abs_hrirs = np.abs(hrirs)
        global_peak_idx = np.unravel_index(np.argmax(abs_hrirs), abs_hrirs.shape)
        
        print(f"Global peak found at measurement {global_peak_idx[0]}, receiver {global_peak_idx[1]}, sample {global_peak_idx[2]}")
        print(f"Peak value: {hrirs[global_peak_idx]}")
        
        # Calculate average onset (using a simple threshold for first arrival)
        threshold = 0.1 * np.max(abs_hrirs)
        onsets = []
        for m in range(M):
            for r in range(R):
                idx = np.where(abs_hrirs[m, r, :] > threshold)[0]
                if len(idx) > 0:
                    onsets.append(idx[0])
        
        if onsets:
            avg_onset = np.mean(onsets)
            min_onset = np.min(onsets)
            max_onset = np.max(onsets)
            print(f"Onset stats (>10% peak): Min={min_onset}, Avg={avg_onset:.1f}, Max={max_onset}")
        else:
            print("No onset found above threshold.")
            
        print("\n")
    except Exception as e:
        print(f"Error reading {sofa_path}: {e}\n")

if __name__ == '__main__':
    analyze_onset(r"c:\Mesh2SOFA\sofa_listen_raw\IRC_1002_R_44100.sofa", "LISTEN")
    analyze_onset(r"c:\Mesh2SOFA\sofa_fabian_raw\FABIAN_HRIR_measured_HATO_000.sofa", "FABIAN")