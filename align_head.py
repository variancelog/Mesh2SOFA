import pyvista as pv
import numpy as np
import argparse
import os
import json
import vtk

# ====== Version 6: Reverted to Window Close Logic ======
# Removed complex Enter key handling.
# Preserved Undo (Backspace) and visual improvements.

def align_mesh(input_mesh_path, output_mesh_path):
    print(f"--- Loading {input_mesh_path} ---")
    
    try:
        mesh = pv.read(input_mesh_path)
    except Exception as e:
        print(f"Error loading mesh: {e}")
        return

    # ================= PHASE 1: RAY-CAST SELECTION =================
    print("--- PHASE 1: PRECISE ALIGNMENT ---")
    print("controls:")
    print("   [Mouse Move] : Move Cursor")
    print("   [P]          : Capture Point")
    print("   [Backspace]  : Undo Last Point")
    print("   [Close Window] : Confirm & Process (when 3 points set)")
    
    state = {
        'step': 0,              
        'points': [],           
        'targets': ["LEFT EAR (Canal Entrance)", "RIGHT EAR (Canal Entrance)", "NOSE BRIDGE (Nasion)"],
        'current_cursor_point': None, 
        'text_actor': None,
        'status_actor': None
    }
    
    pl = pv.Plotter()
    pl.enable_parallel_projection()
    pl.add_mesh(mesh, color='lightblue', show_edges=False, pickable=True, name="target_mesh")
    
    picker = vtk.vtkCellPicker()
    picker.SetTolerance(0.005)

    def update_ui(instruction, status, status_color='black'):
        if state['text_actor']:
            pl.remove_actor(state['text_actor'])
        if state['status_actor']:
            pl.remove_actor(state['status_actor'])
        
        state['text_actor'] = pl.add_text(instruction, position='upper_left', font_size=12, color='black')
        full_status = f"{status}\n[Backspace]=Undo  |  [Close Window]=Process"
        state['status_actor'] = pl.add_text(full_status, position='lower_left', font_size=10, color=status_color)

    update_ui(f"Step 1: Pick {state['targets'][0]} (Press 'P')", "Hover mouse to locate point...")

    # --- MOUSE MOVE ---
    def on_mouse_move(iren, event):
        if state['step'] >= 3: 
            pl.remove_actor("cursor_marker")
            return
        
        x, y = iren.GetEventPosition()
        picker.Pick(x, y, 0, pl.renderer)
        cell_id = picker.GetCellId()
        
        if cell_id != -1:
            point = picker.GetPickPosition()
            state['current_cursor_point'] = point
            pl.remove_actor("cursor_marker")
            pl.add_mesh(pv.Sphere(radius=0.8, center=point), color='red', opacity=0.85, pickable=False, name="cursor_marker")
            pl.render()
        else:
            state['current_cursor_point'] = None
            pl.remove_actor("cursor_marker")
            pl.render()

    # --- CAPTURE POINT ---
    def on_p_press():
        if state['step'] >= 3: return
        
        point = state['current_cursor_point']
        if point is not None:
            state['points'].append(point)
            pl.add_mesh(pv.Sphere(radius=0.8, center=point), color='green', name=f"confirmed_{state['step']}")
            print(f"   -> Captured: {state['targets'][state['step']]} at {np.round(point, 2)}")

            state['step'] += 1
            if state['step'] < 3:
                update_ui(f"Step {state['step']+1}: Pick {state['targets'][state['step']]} (Press 'P')", "Point saved.")
            else:
                update_ui("ALL POINTS CAPTURED.", "Close Window to process.", 'green')
                pl.remove_actor("cursor_marker")
                print("   -> Phase 1 Complete. Close Window to proceed.")
        else:
            print("   [!] Hover over the mesh first.")

    # --- UNDO ---
    def on_undo_press():
        if state['step'] > 0:
            state['points'].pop()
            pl.remove_actor(f"confirmed_{state['step']-1}")
            state['step'] -= 1
            print(f"   [<] Undo. Re-select: {state['targets'][state['step']]}")
            update_ui(f"Step {state['step']+1}: Pick {state['targets'][state['step']]} (Press 'P')", "Last point removed.", 'red')
            pl.render()

    # Bindings
    pl.iren.add_observer("MouseMoveEvent", on_mouse_move)
    pl.add_key_event("p", on_p_press)
    pl.add_key_event("P", on_p_press)
    pl.add_key_event("BackSpace", on_undo_press)
    pl.add_key_event("Delete", on_undo_press) 
    
    pl.show()

    # Check if we exited properly
    if len(state['points']) != 3:
        print("Alignment aborted (Window closed without confirming).")
        return

    # ================= CALCULATION =================
    print("   -> Calculating initial alignment...")
    L = np.array(state['points'][0])
    R = np.array(state['points'][1])
    N = np.array(state['points'][2])
    
    ear_width = np.linalg.norm(L - R)
    centroid = (L + R) / 2.0
    
    vec_y = (L - R) / ear_width
    vec_temp_fwd = N - centroid
    vec_z = np.cross(vec_temp_fwd, vec_y)
    vec_z /= np.linalg.norm(vec_z)
    vec_x = np.cross(vec_y, vec_z)
    vec_x /= np.linalg.norm(vec_x)

    rotation = np.eye(4)
    rotation[0, :3] = vec_x
    rotation[1, :3] = vec_y
    rotation[2, :3] = vec_z
    
    translation = np.eye(4)
    translation[:3, 3] = -centroid
    
    mesh.transform(rotation @ translation, inplace=True)

    # ================= PHASE 2: FINE TUNING =================
    print("\n--- PHASE 2: FINE TUNE ROTATION ---")
    print("1. Use the slider to tilt the head Up/Down (Pitch).")
    print("2. Close Window to Save & Finish.")

    pl2 = pv.Plotter()
    pl2.add_mesh(mesh, color='lightblue', show_edges=False)
    pl2.enable_parallel_projection()
    pl2.view_xz() 
    
    rot_state = {'current': 0.0}

    def rotate_pitch(value):
        delta = value - rot_state['current']
        mesh.rotate_y(delta, inplace=True)
        rot_state['current'] = value

    pl2.add_slider_widget(
        rotate_pitch, 
        [-45, 45], 
        title="Pitch Angle (Degrees)",
        value=0,
        pointa=(0.3, 0.9), pointb=(0.7, 0.9),
        style="modern"
    )

    pl2.add_text("Fine Tune: Rotate Y-Axis", position='upper_left', font_size=12, color='black')
    pl2.add_text("Close Window to SAVE.", position='lower_left', font_size=12, color='black')
    
    pl2.add_axes()
    pl2.show_grid() 
    pl2.show()

    # ================= SAVE =================
    print(f"   -> Saving final mesh...")
    mesh.save(output_mesh_path)
    
    info_path = output_mesh_path.replace(".ply", "_info.json")
    alignment_data = {
        "left_ear": [0.0, ear_width/2.0, 0.0],
        "right_ear": [0.0, -ear_width/2.0, 0.0],
        "unit": "meters" if ear_width < 1.0 else "mm"
    }
    
    with open(info_path, 'w') as f:
        json.dump(alignment_data, f, indent=4)
        
    print(f"DONE! Saved mesh to: {output_mesh_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Interactively align a head mesh.")
    parser.add_argument("input", help="Path to input mesh")
    parser.add_argument("output", help="Path to output mesh")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input):
        print(f"Error: Input file '{args.input}' not found.")
    else:
        align_mesh(args.input, args.output)