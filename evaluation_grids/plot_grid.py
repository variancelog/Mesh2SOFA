import os
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

def visualize_grid(nodes_file="Nodes.txt", save_path=None, show=True):
    # Derive a grid name from the containing folder (falls back to its parent
    # directory when nodes_file sits directly in the working directory).
    grid_name = os.path.basename(os.path.dirname(os.path.abspath(nodes_file)))

    # --- 1. Load Nodes ---
    nodes = {}
    with open(nodes_file, 'r') as f:
        lines = f.readlines()
        # Skip header (count)
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) >= 4:
                # Format: ID x y z
                nid = int(parts[0])
                nodes[nid] = [float(parts[1]), float(parts[2]), float(parts[3])]

    # --- 2. Prepare Data for Plotting ---
    node_ids = sorted(nodes.keys())
    coords = np.array([nodes[nid] for nid in node_ids])
    x, y, z = coords[:, 0], coords[:, 1], coords[:, 2]

    # Recover the grid radius from the node coordinates (all lie on the sphere).
    radius = float(np.median(np.linalg.norm(coords, axis=1)))

    # --- 3. Plot ---
    fig = plt.figure(figsize=(9, 8))
    # Set the window title so it (and the default save filename) uses grid_name
    # rather than the generic "Figure 1".
    try:
        fig.canvas.manager.set_window_title(grid_name)
    except AttributeError:
        pass
    ax = fig.add_subplot(111, projection='3d')

    # Plot the grid as circular nodes (coloured by height for depth cueing)
    ax.scatter(x, y, z, c=z, cmap='viridis', marker='o', s=18,
               depthshade=True, edgecolors='k', linewidths=0.3)

    # Make axes equal aspect ratio (looks like a sphere, not an oval)
    max_range = np.array([x.max()-x.min(), y.max()-y.min(), z.max()-z.min()]).max() / 2.0
    mid_x = (x.max()+x.min()) * 0.5
    mid_y = (y.max()+y.min()) * 0.5
    mid_z = (z.max()+z.min()) * 0.5
    ax.set_xlim(mid_x - max_range, mid_x + max_range)
    ax.set_ylim(mid_y - max_range, mid_y + max_range)
    ax.set_zlim(mid_z - max_range, mid_z + max_range)
    # Force a cubic rendering box so the sphere is not stretched into an oval
    ax.set_box_aspect([1, 1, 1])

    ax.set_title(f"{grid_name} Grid (Radius: {radius:g}m; Nodes: {len(node_ids)})")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_zlabel("Z (m)")

    # Tight layout for both on-screen display and saved files.
    fig.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150)
        print(f"   Saved {save_path}")
    if show:
        plt.show()
    plt.close(fig)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Plot a Mesh2HRTF evaluation grid as circular nodes.")
    parser.add_argument(
        "path", nargs="?", default="Nodes.txt",
        help="Path to a Nodes.txt file, or a grid folder containing one "
             "(e.g. N-Design_50). Defaults to ./Nodes.txt")
    parser.add_argument(
        "--all", action="store_true",
        help="Render every subfolder that contains a Nodes.txt and save "
             "<folder>/<folder>.png into each, without opening windows. "
             "Scans the directory given by 'path' if it is a folder, else "
             "the current directory.")
    args = parser.parse_args()

    if args.all:
        # Pick the base directory to scan for grid subfolders.
        base = args.path if os.path.isdir(args.path) else "."
        subfolders = sorted(
            e.path for e in os.scandir(base)
            if e.is_dir() and os.path.isfile(os.path.join(e.path, "Nodes.txt")))

        if not subfolders:
            parser.error(f"No subfolders containing Nodes.txt found in: "
                         f"{os.path.abspath(base)}")

        print(f"Rendering {len(subfolders)} grid(s) under {os.path.abspath(base)}:")
        for folder in subfolders:
            name = os.path.basename(os.path.normpath(folder))
            visualize_grid(
                os.path.join(folder, "Nodes.txt"),
                save_path=os.path.join(folder, f"{name}.png"),
                show=False)
        print("Done.")
    else:
        # Allow passing either the file or its containing folder.
        nodes_path = args.path
        if os.path.isdir(nodes_path):
            nodes_path = os.path.join(nodes_path, "Nodes.txt")

        if not os.path.isfile(nodes_path):
            parser.error(f"Nodes file not found: {nodes_path}")

        visualize_grid(nodes_path)