import numpy as np
from scipy.spatial import ConvexHull
import os
import sys

# ================= CONFIGURATION =================
# T-Design Degree
# t=21 -> ~240 points (Standard)
# t=30 -> ~480 points (High Res)
# t=50 -> ~1300 points (Ultra High Res)
DEGREE_T = 29

# Radius in meters (Standard is often 1.0m or 1.5m)
RADIUS = 1.5

# ID OFFSET (CRITICAL)
# Evaluation grid IDs must NOT overlap with the head mesh IDs.
# Head meshes usually have 10k-50k nodes. 
# Starting at 200,000 is the standard safety practice.
ID_OFFSET = 550000 

# Output Folder
OUTPUT_DIR = f"N-Design_{DEGREE_T}"
# =================================================

def generate_and_save_grid():
    # --- 1. Import Library ---
    try:
        import spaudiopy as spa
    except ImportError:
        print("Error: 'spaudiopy' library is missing.")
        print("Run: pip install spaudiopy")
        return

    print(f"1. Generating Spherical Design (t={DEGREE_T})...")
    
    # --- 2. Generate Points ---
    try:
        # Attempt to load standard t-design (supports t=1 to 21)
        if DEGREE_T <= 21:
            points = spa.grids.load_t_design(DEGREE_T)
            print("   -> Loaded standard T-Design.")
        else:
            # Fallback to N-design for higher degrees (supports t=1 to 124)
            # These are "equivalent but more modern" designs suitable for high res.
            points = spa.grids.load_n_design(DEGREE_T)
            print("   -> Loaded N-Design (High Resolution).")
            
    except ValueError:
        print(f"Error: Could not load design for t={DEGREE_T}.")
        print("For degrees > 21, ensure spaudiopy is up to date or try t=30, 50, 70.")
        return
    except AttributeError:
        print("Error: Your version of 'spaudiopy' might be too old to support 'load_n_design'.")
        print("Try updating with: pip install --upgrade spaudiopy")
        return

    # Scale to radius
    points = points * RADIUS
    num_nodes = len(points)
    print(f"   -> Created {num_nodes} nodes.")

    # --- 3. Generate Triangles (Convex Hull) ---
    hull = ConvexHull(points)
    elements_raw = hull.simplices
    num_elements = len(elements_raw)
    print(f"   -> Generated {num_elements} triangles.")

    # --- 4. Fix Normals (Ensure Outward Pointing) ---
    # BEM requires triangles to follow the 'Right Hand Rule' facing outward.
    corrected_elements = []
    
    for tri in elements_raw:
        A = points[tri[0]]
        B = points[tri[1]]
        C = points[tri[2]]
        
        # Calculate normal
        face_normal = np.cross(B - A, C - A)
        centroid = (A + B + C) / 3.0
        
        # If normal points opposite to centroid vector, flip it
        if np.dot(face_normal, centroid) < 0:
            corrected_elements.append([tri[0], tri[2], tri[1]])
        else:
            corrected_elements.append([tri[0], tri[1], tri[2]])
    
    elements = np.array(corrected_elements)

    # --- 5. Prepare Output Directory ---
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        print(f"   -> Created folder: {OUTPUT_DIR}")

    # --- 6. Write Nodes.txt ---
    # Format: 
    # Line 1: [Total Count]
    # Line N: [ID] [x] [y] [z]
    nodes_path = os.path.join(OUTPUT_DIR, "Nodes.txt")
    print(f"2. Writing {nodes_path}...")
    
    with open(nodes_path, "w") as f:
        # Write Header
        f.write(f"{num_nodes}\n")
        
        # Write Data
        for i, p in enumerate(points):
            # Apply ID Offset
            node_id = i + 1 + ID_OFFSET
            f.write(f"{node_id} {p[0]:.16f} {p[1]:.16f} {p[2]:.16f}\n")

    # --- 7. Write Elements.txt ---
    # Format:
    # Line 1: [Total Count]
    # Line N: [ElemID] [Node1] [Node2] [Node3] [MatID] [DomainID] [Used]
    # * MatID=2 (Standard for Microphones/Sensors)
    # * DomainID=0 (Standard Fluid Domain)
    # * Used=1 (Active)
    elems_path = os.path.join(OUTPUT_DIR, "Elements.txt")
    print(f"3. Writing {elems_path}...")
    
    with open(elems_path, "w") as f:
        # Write Header
        f.write(f"{num_elements}\n")
        
        # Write Data
        for i, tri in enumerate(elements):
            # Apply Offset to Element ID
            elem_id = i + 1 + ID_OFFSET
            
            # Apply Offset to Node Indices matches Nodes.txt
            n1 = tri[0] + 1 + ID_OFFSET
            n2 = tri[1] + 1 + ID_OFFSET
            n3 = tri[2] + 1 + ID_OFFSET
            
            # Write 7-column format
            f.write(f"{elem_id} {n1} {n2} {n3} 2 0 1\n")

    print("\nSUCCESS! Grid generation complete.")
    print(f"Copy the folder '{OUTPUT_DIR}' to: C:\\Mesh2HRTF\\mesh2hrtf\\Mesh2Input\\EvaluationGrids\\")

if __name__ == "__main__":
    generate_and_save_grid()