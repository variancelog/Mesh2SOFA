import argparse
import os
import sys
import numpy as np
import sofar as sf
import scipy.fft as fft
from scipy.spatial import ConvexHull
import matplotlib.pyplot as plt
import csv
import re

""" Note: To learn more about how this script computes Diffuse Field HRTF 
    (DFHRTF) from the SOFA files, please read `readme_dfhrtf_calculation.md 
    in the repo """


# ================= HELPER FUNCTIONS =================

def spherical_to_cartesian(r, az, el):
    az_rad = np.radians(az)
    el_rad = np.radians(el)
    x = r * np.cos(el_rad) * np.cos(az_rad)
    y = r * np.cos(el_rad) * np.sin(az_rad)
    z = r * np.sin(el_rad)
    return np.column_stack((x, y, z))

def calculate_geometric_weights(source_pos, front_bias=0.0):
    """Calculates solid angle weights for spherical integration."""
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
    except:
        weights = np.ones(len(source_pos)) / len(source_pos)

    if front_bias > 0.0:
        x_coords = cart_coords[:, 0]
        bias_weights = ((1.0 + x_coords) / 2.0) ** front_bias
        weights = weights * bias_weights
        if np.sum(weights) > 0:
            weights /= np.sum(weights)

    return weights

def generate_fractional_octave_frequencies(start_freq, end_freq, fraction=6):
    """Generates log-spaced frequencies."""
    freqs = []
    f = start_freq
    while f <= end_freq:
        freqs.append(f)
        f *= 2**(1/fraction)
    return np.array(freqs)

def apply_spectral_tilt(freqs, magnitude_db, slope_per_octave, ref_freq=1000.0):
    """Applies a dB/octave tilt pivoting at ref_freq."""
    if slope_per_octave == 0:
        return magnitude_db
    num_octaves = np.log2(freqs / ref_freq)
    tilt_curve = num_octaves * slope_per_octave
    return magnitude_db + tilt_curve

def apply_smoothing(freqs, mags, fraction=24):
    """Applies 1/fraction octave moving-average smoothing."""
    smoothed = np.zeros_like(mags)
    for i, f in enumerate(freqs):
        f_lower = f / (2**(1/(2*fraction)))
        f_upper = f * (2**(1/(2*fraction)))
        mask = (freqs >= f_lower) & (freqs <= f_upper)
        if np.any(mask):
            smoothed[i] = np.mean(mags[mask])
        else:
            smoothed[i] = mags[i]
    return smoothed

def save_txt_mono(filename, freqs, mag):
    """Saves a single channel as tab-delimited text without a header row."""
    try:
        with open(filename, 'w', newline='') as txtfile:
            for f, m in zip(freqs, mag):
                txtfile.write(f"{f:.6f}\t{m:.3f}\n")
        print(f"   [+] Saved TXT: {os.path.basename(filename)}")
    except IOError as e:
        print(f"[ERROR] Saving TXT: {e}")

def save_csv_mono(filename, freqs, mag):
    """Saves a single channel (Frequency, Magnitude)."""
    try:
        with open(filename, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Frequency (Hz)', 'Magnitude (dB)'])
            for f, m in zip(freqs, mag):
                writer.writerow([f"{f:.2f}", f"{m:.2f}"])
        print(f"   [+] Saved CSV: {os.path.basename(filename)}")
    except IOError as e:
        print(f"[ERROR] Saving CSV: {e}")

def save_csv_stereo(filename, freqs, mag_l, mag_r):
    """Saves both channels (Frequency, Left, Right)."""
    try:
        with open(filename, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Frequency (Hz)', 'Left (dB)', 'Right (dB)'])
            for f, l, r in zip(freqs, mag_l, mag_r):
                writer.writerow([f"{f:.2f}", f"{l:.2f}", f"{r:.2f}"])
        print(f"   [+] Saved CSV: {os.path.basename(filename)}")
    except IOError as e:
        print(f"[ERROR] Saving CSV: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to HRIR_48000Hz.sofa")
    parser.add_argument("--output_dir", required=True, help="Output folder")
    parser.add_argument("--tilt", type=float, default=0.0, help="Spectral Tilt (dB/oct)")
    parser.add_argument("--front_bias", type=float, default=0.0, help="Frontal Spatial Bias")
    parser.add_argument("--squigify", action="store_true", help="Enable Squiglink optimized DFHRTF outputs")
    parser.add_argument("--sim_meas", action="store_true", help="Tag output as Simulated instead of Measured")
    parser.add_argument("--prefix", type=str, default="", help="Optional file prefix")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[ERROR] Input file missing: {args.input}")
        sys.exit(1)

    print(f"--- Generating Extras ---")
    print(f"Input: {os.path.basename(args.input)}")
    print(f"Tilt:  {args.tilt} dB/oct")
    print(f"Front Bias: {args.front_bias}")

    # 1. Load Data
    sofa = sf.read_sofa(args.input)
    hrirs = sofa.Data_IR
    fs = float(sofa.Data_SamplingRate)
    
    # 2. Compute Diffuse Field Response
    print("   -> Calculating Diffuse Field Average...")
    effective_front_bias = 0.0 if args.squigify else args.front_bias
    weights = calculate_geometric_weights(sofa.SourcePosition, front_bias=effective_front_bias)
    n_fft = 16384
    
    # FFT (Measurements, Receivers, Freqs)
    fft_data = np.fft.rfft(hrirs, n=n_fft, axis=2)
    power_spec = np.abs(fft_data)**2
    
    # Weighted Average across measurements (Axis 0)
    weights_expanded = weights[:, np.newaxis, np.newaxis]
    avg_power = np.sum(power_spec * weights_expanded, axis=0) # Result shape: (2, n_bins)
    
    # Magnitude dB
    avg_mag = np.sqrt(avg_power)
    avg_db = 20 * np.log10(avg_mag + 1e-12)
    
    fft_freqs = np.fft.rfftfreq(n_fft, d=1/fs)

    # 3. Interpolate
    if args.squigify:
        print("   -> Applying 1/24th Octave Smoothing & Interpolating to 48 PPO...")
        smoothed_l = apply_smoothing(fft_freqs, avg_db[0], fraction=24)
        smoothed_r = apply_smoothing(fft_freqs, avg_db[1], fraction=24)
        target_freqs = generate_fractional_octave_frequencies(20, 20000, fraction=48)
        val_l = np.interp(target_freqs, fft_freqs, smoothed_l)
        val_r = np.interp(target_freqs, fft_freqs, smoothed_r)
    else:
        print("   -> Interpolating to 1/48th Octave...")
        target_freqs = generate_fractional_octave_frequencies(20, 20000, fraction=48)
        val_l = np.interp(target_freqs, fft_freqs, avg_db[0])
        val_r = np.interp(target_freqs, fft_freqs, avg_db[1])

    # 4. Normalize (0 dB at 1 kHz)
    idx_1k = (np.abs(target_freqs - 1000.0)).argmin()
    norm_l = val_l[idx_1k]
    norm_r = val_r[idx_1k]
    
    val_l -= norm_l
    val_r -= norm_r
    print(f"   -> Normalized to 0dB at 1kHz.")

    # 5. Apply Tilt
    if args.tilt != 0:
        print(f"   -> Applying {args.tilt} dB/oct tilt...")
        val_l = apply_spectral_tilt(target_freqs, val_l, args.tilt, 1000.0)
        val_r = apply_spectral_tilt(target_freqs, val_r, args.tilt, 1000.0)

    # 6. Calculate Average
    val_avg = (val_l + val_r) / 2.0

    # --- GET INPUT FILENAME PREFIX ---
    base_name = os.path.splitext(os.path.basename(args.input))[0]
    prefix_str = f"{args.prefix} " if args.prefix else ""
    bias_str = f"_Bias{args.front_bias}" if args.front_bias > 0.0 else ""

    # 7. Generate Plot
    if not args.squigify:
        plt.figure(figsize=(10, 6))
        plt.semilogx(target_freqs, val_l, label='Left Ear', linewidth=2, alpha=0.8)
        plt.semilogx(target_freqs, val_r, label='Right Ear', linewidth=2, alpha=0.8, linestyle='--')
        
        title_str = f"Diffuse Field HRTF (Normalized)\nSpectral Tilt: {args.tilt} dB/oct"
        if args.front_bias > 0.0:
            title_str += f" | Front Bias: {args.front_bias}"
        plt.title(title_str)
        plt.xlabel("Frequency (Hz)")
        plt.ylabel("Magnitude (dB)")
        
        # Fixed Scale +/- 20 dB
        plt.ylim(-20, 20)
        
        plt.grid(True, which="both", alpha=0.3)
        plt.legend()
        plt.xlim(20, 20000)
        
        # Save Plot
        plot_name = f"{prefix_str}{base_name} Tilt {args.tilt} LR.png"
        plot_path = os.path.join(args.output_dir, plot_name)
        plt.savefig(plot_path, dpi=150)
        print(f"   [+] Saved Plot: {plot_name}")
        plt.close()

    # 8. Save Output Files
    sim_meas_str = "Simulated" if args.sim_meas else "Measured"
    
    if args.squigify:
        match = re.search(r'(P\d{4})', base_name)
        subject_id = match.group(1) if match else base_name
        
        name_left = f"{prefix_str}{args.tilt:g} {base_name} {sim_meas_str} L.txt"
        save_txt_mono(os.path.join(args.output_dir, name_left), target_freqs, val_l)
        
        name_right = f"{prefix_str}{args.tilt:g} {base_name} {sim_meas_str} R.txt"
        save_txt_mono(os.path.join(args.output_dir, name_right), target_freqs, val_r)
        
        print("\n[SUCCESS] Squigified DFHRTF files generated (2 TXTs).")
    else:
        # Left Only
        name_left = f"{prefix_str}{args.tilt:g} {base_name} {sim_meas_str} L.csv"
        save_csv_mono(os.path.join(args.output_dir, name_left), target_freqs, val_l)
        
        # Right Only
        name_right = f"{prefix_str}{args.tilt:g} {base_name} {sim_meas_str} R.csv"
        save_csv_mono(os.path.join(args.output_dir, name_right), target_freqs, val_r)
    
        # Average (Mixed Mono)
        name_avg = f"{prefix_str}{args.tilt:g} {base_name} {sim_meas_str} Avg.csv"
        save_csv_mono(os.path.join(args.output_dir, name_avg), target_freqs, val_avg)
    
        print("\n[SUCCESS] Extras generated (3 CSVs + Plot).")

if __name__ == "__main__":
    main()
