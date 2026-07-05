import argparse
import os
import sys
import numpy as np
import sofar as sf
import scipy.signal as signal
import scipy.fft as fft
from scipy.spatial import ConvexHull
import matplotlib.pyplot as plt

# ================= CONFIGURATION =================
PROCESSING_LENGTH = 512
OUTPUT_LENGTH = 256
NORM_TARGET_DB = -1.0
# =================================================

def ensure_mesh2hrtf_import(m2h_path):
    try:
        import mesh2hrtf as m2h
        print("[+] Mesh2HRTF library imported successfully.")
        return m2h
    except ImportError:
        print(f"[i] Library not found. Adding path: {m2h_path}")
        sys.path.append(m2h_path)
        sys.path.append(os.path.dirname(m2h_path))
        
        try:
            import mesh2hrtf as m2h
            print("[+] Mesh2HRTF library imported successfully after path update.")
            return m2h
        except ImportError as e:
            print(f"[FATAL] Could not import mesh2hrtf. Check your path.\nError: {e}")
            sys.exit(1)

def run_project_export(m2h, project_path):
    print(f"   -> Processing: {os.path.basename(project_path)}...")
    try:
        m2h.output2hrtf(project_path)
    except Exception as e:
        print(f"[ERROR] Export failed for {project_path}: {e}")
        sys.exit(1)

def find_sofas_in_project(project_path):
    out_dir = os.path.join(project_path, "Output2HRTF")
    if not os.path.exists(out_dir):
        out_dir = project_path
        
    candidates = [f for f in os.listdir(out_dir) if f.endswith(".sofa")]
    if not candidates:
        print(f"[ERROR] No SOFA files found in {out_dir}")
        sys.exit(1)
    
    # Return all files starting with HRIR
    return [os.path.join(out_dir, c) for c in candidates if "HRIR" in c]

def spherical_to_cartesian(r, az, el):
    az_rad = np.radians(az)
    el_rad = np.radians(el)
    x = r * np.cos(el_rad) * np.cos(az_rad)
    y = r * np.cos(el_rad) * np.sin(az_rad)
    z = r * np.sin(el_rad)
    return np.column_stack((x, y, z))

def calculate_geometric_weights(source_pos):
    print("      Calculating geometric weights...", end="", flush=True)
    az = source_pos[:, 0]
    el = source_pos[:, 1]
    cart_coords = spherical_to_cartesian(1.0, az, el)
    try:
        hull = ConvexHull(cart_coords)
        weights = np.zeros(len(cart_coords))
        for simplex in hull.simplices:
            A, B, C = cart_coords[simplex]
            cross_prod = np.cross(B - A, C - A)
            area = 0.5 * np.linalg.norm(cross_prod)
            weights[simplex] += area / 3.0
        weights /= np.sum(weights)
        print(" Done.")
        return weights
    except:
        print(" Failed (Fallback to uniform).")
        return np.ones(len(source_pos)) / len(source_pos)

def master_sofa(raw_sofa, target_fs, apply_dfeq, output_path):
    print(f"\n--- Generating: {os.path.basename(output_path)} ---")
    
    out_sofa = raw_sofa.copy()
    hrirs = out_sofa.Data_IR.copy()
    src_fs = float(out_sofa.Data_SamplingRate)
    M, R, N = hrirs.shape

    # 1. Resample
    if abs(src_fs - target_fs) > 1.0:
        print(f"   1. Resampling: {src_fs:.0f} -> {target_fs} Hz")
        new_N = int(N * target_fs / src_fs)
        hrirs = signal.resample(hrirs, new_N, axis=-1)
        N = new_N
    else:
        print(f"   1. Rate matches ({src_fs:.0f} Hz).")

    # 1.5 Temporal Alignment
    print("   1.5. Temporal Alignment...")
    abs_hrirs = np.abs(hrirs)
    global_peak_idx = np.unravel_index(np.argmax(abs_hrirs), abs_hrirs.shape)[2]
    target_peak_idx = 32  # Align peak to index 32 (~0.67ms at 48kHz)
    
    if global_peak_idx != target_peak_idx:
        shift_amt = global_peak_idx - target_peak_idx
        if shift_amt > 0:
            # Shift left
            hrirs = hrirs[:, :, shift_amt:]
        else:
            # Shift right (pad left)
            pad_left = -shift_amt
            hrirs = np.pad(hrirs, ((0,0), (0,0), (pad_left, 0)), mode='constant')
        N = hrirs.shape[2]
        print(f"      Shifted IRs by {-shift_amt} samples (peak was at {global_peak_idx}).")
    else:
        print("      Peak already aligned.")

    # 2. Pad to Processing Length
    if N < PROCESSING_LENGTH:
        pad_amt = PROCESSING_LENGTH - N
        hrirs = np.pad(hrirs, ((0,0), (0,0), (0, pad_amt)), mode='constant')
        N = PROCESSING_LENGTH
    elif N > PROCESSING_LENGTH:
        hrirs = hrirs[:, :, :PROCESSING_LENGTH]
        N = PROCESSING_LENGTH

    # 3. Apply DFEQ
    if apply_dfeq:
        print("   2. Applying Diffuse Field EQ...")
        weights = calculate_geometric_weights(out_sofa.SourcePosition)
        H_f = fft.rfft(hrirs, n=N, axis=-1)
        P_f = np.abs(H_f)**2
        weights_expanded = weights[:, np.newaxis, np.newaxis]
        df_power = np.sum(P_f * weights_expanded, axis=0) 
        df_mag = np.sqrt(df_power)
        inv_filter_mag = 1.0 / (df_mag + 1e-12)
        H_f_eq = H_f * inv_filter_mag[None, :, :]
        hrirs = fft.irfft(H_f_eq, n=N, axis=-1)
    else:
        print("   2. Skipping DFEQ.")

    # 4. Crop & Fade
    if OUTPUT_LENGTH < N:
        print(f"   3. Cropping to {OUTPUT_LENGTH} samples and windowing...")
        hrirs = hrirs[:, :, :OUTPUT_LENGTH]
        
        # Fade in
        fade_in_len = 8
        if fade_in_len > 0:
            fade_in_curve = np.hanning(2 * fade_in_len)[:fade_in_len]
            hrirs[:, :, :fade_in_len] *= fade_in_curve
            
        # Fade out
        fade_out_len = 32
        if fade_out_len > 0:
            fade_out_curve = np.hanning(2 * fade_out_len)[fade_out_len:] 
            hrirs[:, :, -fade_out_len:] *= fade_out_curve

    # 5. Normalize
    print(f"   4. Normalizing to {NORM_TARGET_DB} dB...")
    peak = np.max(np.abs(hrirs))
    target_linear = 10 ** (NORM_TARGET_DB / 20.0)
    if peak > 0:
        scale = target_linear / peak
        hrirs *= scale
        print(f"      Scaled by {scale:.4f}.")

    # 6. Save
    out_sofa.Data_IR = hrirs.astype(np.float32)
    out_sofa.Data_SamplingRate = float(target_fs)
    sf.write_sofa(output_path, out_sofa)
    print(f"   [+] Saved {os.path.basename(output_path)}")

def merge_sofas(path_l, path_r):
    print("\n=== Merging Left and Right Projects ===")
    l_sofa = sf.read_sofa(path_l)
    r_sofa = sf.read_sofa(path_r)
    
    if l_sofa.Data_SamplingRate != r_sofa.Data_SamplingRate:
        print("[FATAL] Sampling rates do not match!")
        sys.exit(1)
        
    if l_sofa.Data_IR.shape[1] == 1 and r_sofa.Data_IR.shape[1] == 1:
        # Merge IRs
        merged_ir = np.concatenate((l_sofa.Data_IR, r_sofa.Data_IR), axis=1)
        
        # Merge Receiver Positions
        merged_recv_pos = np.vstack((l_sofa.ReceiverPosition, r_sofa.ReceiverPosition))
        
        # --- FIX: Force Data_Delay to 2D before merge ---
        # numpy.atleast_2d converts shape (1,) to (1,1) which allows axis=1 concatenation
        d_l = np.atleast_2d(l_sofa.Data_Delay)
        d_r = np.atleast_2d(r_sofa.Data_Delay)
        
        # Ensure it is (1, 1) if it came out as (1, N) or similar oddity
        if d_l.shape[0] != 1: d_l = d_l.T
        if d_r.shape[0] != 1: d_r = d_r.T
            
        merged_delay = np.concatenate((d_l, d_r), axis=1)
        
        # Create new object
        merged = l_sofa.copy()
        merged.Data_IR = merged_ir
        merged.ReceiverPosition = merged_recv_pos
        merged.Data_Delay = merged_delay
        merged.GLOBAL_Title = "Merged HRTF (Mastered)"
        return merged
    else:
        print("[FATAL] Input SOFAs already have multiple receivers.")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser()
    # Orchestrator args
    parser.add_argument("--left", required=False)
    parser.add_argument("--right", required=False)
    parser.add_argument("--m2h_path", required=False)
    # Standalone args
    parser.add_argument("--input", required=False)
    
    parser.add_argument("--output", required=True)
    parser.add_argument("--only-48k", action="store_true", help="Generate only the 48kHz un-EQ'd file")
    parser.add_argument("--double-length", action="store_true", help="Double the processing and output lengths")
    args = parser.parse_args()

    if args.double_length:
        global PROCESSING_LENGTH, OUTPUT_LENGTH
        PROCESSING_LENGTH = 1024
        OUTPUT_LENGTH = 512
        print("=== Double Length Mode Enabled (1024/512) ===")

    # --- DETERMINE MODE ---
    targets = []
    if args.input:
        print("=== Standalone Mode: Mastering Single SOFA ===")
        if not os.path.exists(args.input):
            print(f"[ERROR] Input file missing: {args.input}")
            sys.exit(1)
            
        target_sofa = sf.read_sofa(args.input)
        base_name = os.path.splitext(os.path.basename(args.input))[0]
        targets.append((target_sofa, base_name))
        
    elif args.left and args.right and args.m2h_path:
        print("=== Orchestrator Mode: Generating and Merging ===")
        m2h = ensure_mesh2hrtf_import(args.m2h_path)

        print("=== Step 1: Generating Raw SOFA Data ===")
        run_project_export(m2h, args.left)
        run_project_export(m2h, args.right)

        sofas_l = find_sofas_in_project(args.left)
        sofas_r = find_sofas_in_project(args.right)
        
        pairs = []
        for sl in sofas_l:
            for sr in sofas_r:
                if os.path.basename(sl) == os.path.basename(sr):
                    pairs.append((sl, sr, os.path.basename(sl)))

        if not pairs:
            print("[ERROR] No matching left/right SOFA pairs found.")
            sys.exit(1)

        for sl, sr, name in pairs:
            print(f"   Merging Pair: {name}")
            target_sofa = merge_sofas(sl, sr)
            base_name = os.path.splitext(name)[0]
            targets.append((target_sofa, base_name))
        
    else:
        print("[ERROR] Invalid arguments. Provide either --input OR (--left, --right, --m2h_path).")
        sys.exit(1)

    # --- MASTERING EXECUTION ---
    print("\n=== Step 2: Mastering Outputs ===")
    jobs = [
        (44100, False, "44100Hz.sofa"),
        (44100, True,  "44100Hz_DFEQ.sofa"),
        (48000, False, "48000Hz.sofa"),
        (48000, True,  "48000Hz_DFEQ.sofa")
    ]
    if args.only_48k:
        jobs = [(48000, False, "48000Hz.sofa")]

    for target_sofa, base_name in targets:
        for fs, dfeq, suffix in jobs:
            out_path = os.path.join(args.output, f"{base_name}_{suffix}")
            master_sofa(target_sofa, fs, dfeq, out_path)

    print("\n[SUCCESS] All files generated.")

if __name__ == "__main__":
    main()
