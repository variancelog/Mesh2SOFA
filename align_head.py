import sys
import os
import subprocess

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

class _PlotterLockFilter(QtCore.QObject):
    """Swallows mouse/keyboard events to lock plotter interaction.

    Used instead of setEnabled(False) to avoid invalidating the VTK OpenGL
    context handle, which causes wglMakeCurrent errors from VTK's render timer.
    """
    _BLOCKED = frozenset([
        QtCore.QEvent.Type.MouseButtonPress,
        QtCore.QEvent.Type.MouseButtonRelease,
        QtCore.QEvent.Type.MouseButtonDblClick,
        QtCore.QEvent.Type.MouseMove,
        QtCore.QEvent.Type.Wheel,
        QtCore.QEvent.Type.KeyPress,
        QtCore.QEvent.Type.KeyRelease,
    ])

    def eventFilter(self, obj, event):
        return event.type() in self._BLOCKED


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
        self._try_reload_points()

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

        self.btn_clear = QtWidgets.QPushButton("Clear All Points")
        self.btn_clear.setObjectName("UndoBtn")
        self.btn_clear.clicked.connect(self.clear_all_points)
        p1_layout.addWidget(self.btn_clear)

        self.btn_confirm = QtWidgets.QPushButton("Fine Tune Alignment")
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.clicked.connect(self.start_phase_2)
        p1_layout.addWidget(self.btn_confirm)

        panel_layout.addWidget(self.phase1_widget)

        # Phase 2 UI (Hidden initially)
        self.phase2_widget = QtWidgets.QWidget()
        self.phase2_widget.hide()
        p2_layout = QtWidgets.QVBoxLayout(self.phase2_widget)

        lbl_p2_title = QtWidgets.QLabel("Phase 2: Fine Tuning")
        lbl_p2_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        p2_layout.addWidget(lbl_p2_title)
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

        self.btn_save = QtWidgets.QPushButton("Save & Inspect")
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

    # ------------------------------------------------------------------
    # Point persistence helpers
    # ------------------------------------------------------------------

    def _info_path(self):
        return self.output_mesh_path.replace(".ply", "_info.json")

    def _try_reload_points(self):
        """Load previously picked points from the info JSON (if it exists)."""
        info_path = self._info_path()
        if not os.path.exists(info_path):
            return
        try:
            with open(info_path) as f:
                data = json.load(f)
            picked = data.get("picked_points", [])
            if len(picked) != 3:
                return
            for pt in picked:
                self._add_point(tuple(pt))
        except Exception:
            pass  # Malformed / legacy JSON — just start fresh

    def _add_point(self, point):
        """Add a picked point: draw sphere, update list and instruction label."""
        self.points.append(point)
        idx = len(self.points) - 1
        self.plotter.add_mesh(
            pv.Sphere(radius=0.8, center=point),
            color='green', name=f"confirmed_{idx}", reset_camera=False,
        )
        self.point_list.addItem(f"{self.targets[idx]}:\n {np.round(point, 2)}")

        if len(self.points) < 3:
            self.lbl_instruction.setText(f"Pick {self.targets[len(self.points)]}")
        else:
            self.lbl_instruction.setText("All points captured!")
            self.lbl_instruction.setStyleSheet(f"color: {SUCCESS_COLOR};")
            self.btn_confirm.setEnabled(True)
            self.plotter.remove_actor("cursor_marker")

        self.plotter.render()

    # ------------------------------------------------------------------
    # Mouse / keyboard interaction
    # ------------------------------------------------------------------

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
            self._add_point(self.current_cursor_point)

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

    def clear_all_points(self):
        if self.phase != 1 or not self.points: return
        for i in range(len(self.points)):
            self.plotter.remove_actor(f"confirmed_{i}")
        self.points.clear()
        self.point_list.clear()
        self.lbl_instruction.setText(f"Pick {self.targets[0]}")
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

        # Lock camera movement by installing an event filter that swallows all
        # mouse/keyboard events.  setEnabled(False) was used previously but it
        # invalidates VTK's OpenGL HWND, causing repeated wglMakeCurrent errors
        # from VTK's internal render timer.  The event filter keeps the widget
        # (and its OpenGL context) alive while still blocking user interaction.
        self._lock_filter = _PlotterLockFilter(self.plotter)
        self.plotter.installEventFilter(self._lock_filter)

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

            # Save Info JSON — include raw picked points so they can be reloaded
            info_path = self._info_path()
            alignment_data = {
                "ear_width": self.ear_width,
                "unit": "meters" if self.ear_width < 1.0 else "mm",
                "picked_points": [list(p) for p in self.points],
            }
            with open(info_path, 'w') as f:
                json.dump(alignment_data, f, indent=4)

            self._inspect_and_confirm(self.output_mesh_path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to save mesh: {e}")

    # ------------------------------------------------------------------
    # Mesh quality inspection & popup
    # ------------------------------------------------------------------

    def _launch_problem_viewer(self, mesh_path):
        """Launch mesh_problem_viewer.py as a separate process (own OpenGL context)."""
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        viewer = os.path.join(scripts_dir, "mesh_problem_viewer.py")
        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW on Windows
        subprocess.Popen([sys.executable, viewer, mesh_path], creationflags=creationflags)

    def _inspect_and_confirm(self, saved_path):
        try:
            import mesh_inspector
        except ImportError:
            QtWidgets.QMessageBox.information(self, "Success",
                f"Mesh aligned and saved.\n\n(mesh_inspector.py not found — quality check skipped)\n\n{saved_path}")
            self.close()
            return

        report = mesh_inspector.inspect_mesh(saved_path)

        if report["severity"] == "ok":
            QtWidgets.QMessageBox.information(self, "Success",
                f"Mesh aligned and saved.\nNo quality issues found.\n\n{saved_path}")
            self.close()
            return

        # Build summary text
        summary = mesh_inspector.format_report(report)
        is_critical = report["severity"] == "critical"

        # Tunnels (genus > 0) cannot be auto-repaired — only manual sculpting fixes them.
        # Suppress the Repair button when ALL criticals are tunnel issues.
        tunnel_issues = [c for c in report.get("critical", []) if "tunnel" in c["issue"].lower() or "genus" in c["issue"].lower()]
        tunnel_only_critical = is_critical and len(tunnel_issues) == len(report.get("critical", []))

        title = "Mesh Quality Issues Detected"
        if tunnel_only_critical:
            block_msg = "Topological tunnel(s) detected — fix by sculpting in Blender or an external tool, then re-align."
        else:
            block_msg = "Step blocked until issues are resolved or repaired."
        body = f"{summary}\n\n{block_msg if is_critical else 'Minor issues only — you may continue or attempt a repair.'}"

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(520)
        layout = QtWidgets.QVBoxLayout(dlg)

        lbl = QtWidgets.QLabel(body)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-family: monospace; font-size: 12px;")
        layout.addWidget(lbl)

        btn_row = QtWidgets.QHBoxLayout()
        layout.addLayout(btn_row)

        # "Inspect Issues" — closes popup + rotation window, opens viewer in a separate process
        btn_3d = QtWidgets.QPushButton("Inspect Issues")
        def do_inspect():
            dlg.accept()
            self._launch_problem_viewer(saved_path)
            self.close()
        btn_3d.clicked.connect(do_inspect)
        btn_row.addWidget(btn_3d)

        # Hide Repair when tunnels are the only criticals (repair can't fix topology)
        btn_repair = None
        if not tunnel_only_critical:
            btn_repair = QtWidgets.QPushButton("Attempt Repair")
            btn_repair.setStyleSheet("background-color: #3B8ED0; color: white;")
            btn_row.addWidget(btn_repair)

        if is_critical:
            # "Ignore Issues" — keep the saved file, close everything, let the user proceed
            btn_ignore = QtWidgets.QPushButton("Ignore Issues")
            btn_ignore.setStyleSheet("background-color: #c0392b; color: white;")
            btn_ignore.clicked.connect(dlg.accept)
            btn_row.addWidget(btn_ignore)
        else:
            btn_cont = QtWidgets.QPushButton("Continue Anyway")
            btn_cont.setStyleSheet("background-color: #2cc985; color: black;")
            btn_cont.clicked.connect(dlg.accept)
            btn_row.addWidget(btn_cont)

        if btn_repair is not None:
            def do_repair():
                repaired_path = saved_path + "_repaired.ply"
                try:
                    result = mesh_inspector.repair_mesh(saved_path, repaired_path)
                    if result["success"]:
                        os.replace(repaired_path, saved_path)
                        after = mesh_inspector.format_report(result["after"])
                        QtWidgets.QMessageBox.information(dlg, "Repair Succeeded",
                            f"Repair complete — critical issues resolved.\n\n{after}")
                        dlg.accept()
                    else:
                        if os.path.exists(repaired_path):
                            os.remove(repaired_path)
                        after = mesh_inspector.format_report(result["after"])
                        QtWidgets.QMessageBox.warning(dlg, "Repair Incomplete",
                            f"Some critical issues remain after repair:\n\n{after}")
                        lbl.setText(f"{after}\n\nCritical issues remain. Use Ignore Issues to proceed or fix the source mesh.")
                except Exception as e:
                    QtWidgets.QMessageBox.critical(dlg, "Repair Error", str(e))

            btn_repair.clicked.connect(do_repair)

        result = dlg.exec()
        if result == QtWidgets.QDialog.Accepted:
            self.close()
        # If rejected (window X button closed), keep the app open


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
