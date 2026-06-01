import sys
import os

# Force PySide6 backend for pyvistaqt
os.environ["QT_API"] = "pyside6"

import json
import re
import argparse
import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor
from PySide6 import QtWidgets, QtCore, QtGui
import vtk

# Appearance Settings (mimicking CustomTkinter Dark Theme)
BG_COLOR = "#1d1d1d"
PANEL_COLOR = "#2b2b2b"
TEXT_COLOR = "#ffffff"
ACCENT_COLOR = "#3B8ED0"
SUCCESS_COLOR = "#2cc985"
ERROR_COLOR = "#c0392b"

class AlignHeadApp(QtWidgets.QMainWindow):
    def __init__(self, input_mesh_path, output_mesh_path):
        super().__init__()
        self.input_mesh_path = input_mesh_path
        self.output_mesh_path = output_mesh_path
        
        # Load mesh
        try:
            self.mesh = pv.read(self.input_mesh_path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Could not load mesh: {e}")
            sys.exit(1)

        # State variables
        self.points = []
        self.targets = ["LEFT EAR (Canal Entrance)", "RIGHT EAR (Canal Entrance)", "NOSE BRIDGE (Nasion)"]
        self.current_cursor_point = None
        self.pitch_angle = 0.0
        self.ear_width = 0.0
        self.phase = 1 # 1: Picking, 2: Fine-tuning

        self.setup_ui()
        self.setup_plotter()

    def setup_ui(self):
        self.setWindowTitle("Mesh2SOFA Head Alignment (PySide6)")
        self.resize(1200, 900)
        
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {BG_COLOR}; }}
            QWidget {{ background-color: {BG_COLOR}; color: {TEXT_COLOR}; font-family: 'Segoe UI', sans-serif; }}
            QFrame#ControlPanel {{ background-color: {PANEL_COLOR}; border-radius: 10px; }}
            QPushButton {{ background-color: {ACCENT_COLOR}; border-radius: 5px; padding: 8px; font-weight: bold; min-width: 100px; }}
            QPushButton:disabled {{ background-color: #444; color: #888; }}
            QPushButton#UndoBtn {{ background-color: {ERROR_COLOR}; }}
            QPushButton#SaveBtn {{ background-color: {SUCCESS_COLOR}; color: black; }}
            QLabel#InstructionLabel {{ font-size: 16px; font-weight: bold; color: {ACCENT_COLOR}; }}
            QSlider::handle:horizontal {{ background: {ACCENT_COLOR}; width: 18px; margin: -5px 0; border-radius: 9px; }}
            QSlider::groove:horizontal {{ background: {BG_COLOR}; height: 8px; border-radius: 4px; }}
            QSlider::sub-page:horizontal {{ background: {ACCENT_COLOR}; border-radius: 4px; }}
            QSlider::add-page:horizontal {{ background: {BG_COLOR}; border-radius: 4px; }}
        """)

        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        self.layout = QtWidgets.QHBoxLayout(central_widget)

        # --- Left: Plotter Area ---
        self.plotter_container = QtWidgets.QWidget()
        plotter_layout = QtWidgets.QVBoxLayout(self.plotter_container)
        self.plotter = QtInteractor()
        plotter_layout.addWidget(self.plotter)
        self.layout.addWidget(self.plotter_container, stretch=3)

        # --- Right: Control Panel ---
        self.panel = QtWidgets.QFrame()
        self.panel.setObjectName("ControlPanel")
        self.panel.setFixedWidth(300)
        panel_layout = QtWidgets.QVBoxLayout(self.panel)
        self.layout.addWidget(self.panel, stretch=1)

        # Phase 1 UI
        self.phase1_widget = QtWidgets.QWidget()
        p1_layout = QtWidgets.QVBoxLayout(self.phase1_widget)
        
        self.lbl_instruction = QtWidgets.QLabel(f"Pick {self.targets[0]}")
        self.lbl_instruction.setObjectName("InstructionLabel")
        self.lbl_instruction.setWordWrap(True)
        p1_layout.addWidget(self.lbl_instruction)
        
        p1_layout.addWidget(QtWidgets.QLabel("\nControls:\n[P] Capture Point\n[Backspace] Undo"))
        
        self.lbl_status = QtWidgets.QLabel("Hover mouse and press P")
        self.lbl_status.setStyleSheet("color: #aaa;")
        p1_layout.addWidget(self.lbl_status)
        
        self.point_list = QtWidgets.QListWidget()
        self.point_list.setStyleSheet("background-color: #1a1a1a; border: none;")
        p1_layout.addWidget(self.point_list)
        
        self.btn_undo = QtWidgets.QPushButton("Undo Last Point")
        self.btn_undo.setObjectName("UndoBtn")
        self.btn_undo.clicked.connect(self.undo_point)
        p1_layout.addWidget(self.btn_undo)
        
        self.btn_confirm = QtWidgets.QPushButton("Confirm & Next Phase")
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.clicked.connect(self.start_phase_2)
        p1_layout.addWidget(self.btn_confirm)
        
        panel_layout.addWidget(self.phase1_widget)

        # Phase 2 UI (Hidden initially)
        self.phase2_widget = QtWidgets.QWidget()
        self.phase2_widget.hide()
        p2_layout = QtWidgets.QVBoxLayout(self.phase2_widget)
        
        p2_layout.addWidget(QtWidgets.QLabel("Phase 2: Fine Tuning", font=("Segoe UI", 16, QtGui.QFont.Bold)))
        p2_layout.addWidget(QtWidgets.QLabel("\nPitch Adjustment (Up/Down):"))
        
        self.slider_pitch = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_pitch.setRange(-45, 45)
        self.slider_pitch.setValue(0)
        self.slider_pitch.valueChanged.connect(self.on_pitch_change)
        p2_layout.addWidget(self.slider_pitch)
        
        self.lbl_pitch_val = QtWidgets.QLabel("0°")
        self.lbl_pitch_val.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        p2_layout.addWidget(self.lbl_pitch_val)
        
        p2_layout.addStretch()
        
        self.btn_save = QtWidgets.QPushButton("SAVE & FINISH")
        self.btn_save.setObjectName("SaveBtn")
        self.btn_save.clicked.connect(self.save_and_exit)
        p2_layout.addWidget(self.btn_save)
        
        panel_layout.addWidget(self.phase2_widget)

    def setup_plotter(self):
        self.plotter.enable_parallel_projection()
        self.plotter.add_mesh(self.mesh, color='lightblue', show_edges=False, pickable=True, name="target_mesh")
        
        self.picker = vtk.vtkCellPicker()
        self.picker.SetTolerance(0.005)
        
        self.plotter.iren.add_observer("MouseMoveEvent", self.on_mouse_move)
        self.plotter.add_key_event("p", self.capture_point)
        self.plotter.add_key_event("P", self.capture_point)
        self.plotter.add_key_event("BackSpace", self.undo_point)

    def on_mouse_move(self, iren, event):
        if self.phase != 1 or len(self.points) >= 3:
            self.plotter.remove_actor("cursor_marker")
            return
        
        x, y = iren.GetEventPosition()
        self.picker.Pick(x, y, 0, self.plotter.renderer)
        cell_id = self.picker.GetCellId()
        
        if cell_id != -1:
            point = self.picker.GetPickPosition()
            self.current_cursor_point = point
            self.plotter.add_mesh(pv.Sphere(radius=0.8, center=point), color='red', opacity=0.85, pickable=False, name="cursor_marker", reset_camera=False)
            self.plotter.render()
        else:
            self.current_cursor_point = None
            self.plotter.remove_actor("cursor_marker")
            self.plotter.render()

    def capture_point(self):
        if self.phase != 1 or len(self.points) >= 3: return
        
        if self.current_cursor_point is not None:
            point = self.current_cursor_point
            self.points.append(point)
            idx = len(self.points) - 1
            self.plotter.add_mesh(pv.Sphere(radius=0.8, center=point), color='green', name=f"confirmed_{idx}", reset_camera=False)
            
            self.point_list.addItem(f"{self.targets[idx]}:\n {np.round(point, 2)}")
            
            if len(self.points) < 3:
                self.lbl_instruction.setText(f"Pick {self.targets[len(self.points)]}")
            else:
                self.lbl_instruction.setText("All points captured!")
                self.lbl_instruction.setStyleSheet(f"color: {SUCCESS_COLOR};")
                self.btn_confirm.setEnabled(True)
                self.plotter.remove_actor("cursor_marker")
            
            self.plotter.render()

    def undo_point(self):
        if self.phase != 1 or not self.points: return
        
        idx = len(self.points) - 1
        self.points.pop()
        self.plotter.remove_actor(f"confirmed_{idx}")
        
        self.point_list.takeItem(idx)
        self.lbl_instruction.setText(f"Pick {self.targets[len(self.points)]}")
        self.lbl_instruction.setStyleSheet(f"color: {ACCENT_COLOR};")
        self.btn_confirm.setEnabled(False)
        self.plotter.render()

    def start_phase_2(self):
        self.phase = 2
        self.phase1_widget.hide()
        self.phase2_widget.show()
        
        # Calculate Initial Alignment
        L = np.array(self.points[0])
        R = np.array(self.points[1])
        N = np.array(self.points[2])
        
        self.ear_width = np.linalg.norm(L - R)
        centroid = (L + R) / 2.0
        
        vec_y = (L - R) / self.ear_width
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
        
        self.mesh.transform(rotation @ translation, inplace=True)
        
        # Reset View for Phase 2
        self.plotter.view_xz()
        self.plotter.add_axes()
        self.plotter.show_grid()
        
        # Lock camera movement by disabling the Qt widget.
        # This reliably prevents all mouse interaction (rotation, zoom, pan) 
        # while still allowing programmatic rendering via the slider.
        self.plotter.setEnabled(False)
        
        self.plotter.render()

    def on_pitch_change(self, value):
        delta = value - self.pitch_angle
        self.mesh.rotate_y(delta, inplace=True)
        self.pitch_angle = value
        self.lbl_pitch_val.setText(f"{value}°")
        self.plotter.render()

    def save_and_exit(self):
        # Save Mesh
        try:
            self.mesh.save(self.output_mesh_path)
            
            # Save Info JSON
            info_path = self.output_mesh_path.replace(".ply", "_info.json")
            alignment_data = {
                "left_ear": [0.0, self.ear_width/2.0, 0.0],
                "right_ear": [0.0, -self.ear_width/2.0, 0.0],
                "unit": "meters" if self.ear_width < 1.0 else "mm"
            }
            with open(info_path, 'w') as f:
                json.dump(alignment_data, f, indent=4)
                
            QtWidgets.QMessageBox.information(self, "Success", f"Mesh aligned and saved successfully!\n\nLocation: {self.output_mesh_path}")
            self.close()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to save mesh: {e}")

def main():
    parser = argparse.ArgumentParser(description="Interactively align a head mesh (PySide6).")
    parser.add_argument("input", help="Path to input mesh")
    parser.add_argument("output", help="Path to output mesh")
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    
    if not os.path.exists(args.input):
        print(f"Error: Input file '{args.input}' not found.")
        sys.exit(1)

    window = AlignHeadApp(args.input, args.output)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
