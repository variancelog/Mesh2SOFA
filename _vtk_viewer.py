import sys
import os
import math

# Force PySide6 backend for pyvistaqt
os.environ["QT_API"] = "pyside6"

import re
import pyvista as pv
from pyvistaqt import QtInteractor
from PySide6 import QtWidgets, QtCore, QtGui

# Appearance Settings (mimicking CustomTkinter Dark Theme)
BG_COLOR = "#1d1d1d"
PANEL_COLOR = "#2b2b2b"
TEXT_COLOR = "#ffffff"
ACCENT_COLOR = "#3B8ED0"

def natural_sort_key(s):
    """Key for natural sorting (e.g., '10' comes after '2')."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

def scan_vtk_files(root_folder):
    """Scan Left and Right subfolders for VTK files, searching recursively."""
    data = {"Left": [], "Right": []}
    for ear in ["Left", "Right"]:
        ear_path = os.path.join(root_folder, ear)
        if os.path.exists(ear_path):
            found_files = []
            for root, dirs, files in os.walk(ear_path):
                for f in files:
                    if f.lower().endswith(".vtk"):
                        found_files.append(os.path.join(root, f))
            
            # Sort naturally by filename only
            found_files.sort(key=lambda x: natural_sort_key(os.path.basename(x)))
            data[ear] = found_files
    return data

class VTKViewerApp(QtWidgets.QMainWindow):
    def __init__(self, vtk_root=None, test_mode=False):
        super().__init__()
        self.vtk_root = vtk_root
        self.test_mode = test_mode
        self.vtk_data = {"Left": [], "Right": []}
        self.all_ears = []
        
        # State variables
        self.current_ear = "Left"
        self.current_index = 0
        self.current_width = 0
        self.clim_min = -50.0
        self.clim_max = 0.0
        self.active_actors = {}
        
        # Debounce timer for rendering
        self.render_timer = QtCore.QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self.update_scene)

        if not self.test_mode:
            self.setup_ui()
            if self.vtk_root:
                self.load_vtk_root(self.vtk_root)
        else:
            if not self.vtk_root:
                raise ValueError("vtk_root required for test_mode")
            self.load_vtk_root(self.vtk_root)
            self.plotter = pv.Plotter(off_screen=True)
            self.run_test()

    def setup_ui(self):
        self.setWindowTitle("Mesh2SOFA VTK Viewer (PySide6)")
        self.resize(900, 800)
        
        # Style the main window
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {BG_COLOR}; }}
            QWidget {{ background-color: {BG_COLOR}; color: {TEXT_COLOR}; font-family: 'Segoe UI', sans-serif; }}
            QSlider {{ background: transparent; }}
            QSlider::handle:horizontal {{ background: {ACCENT_COLOR}; width: 18px; margin: -5px 0; border-radius: 9px; }}
            QSlider::groove:horizontal {{ background: {BG_COLOR}; height: 8px; border-radius: 4px; }}
            QSlider::sub-page:horizontal {{ background: {ACCENT_COLOR}; border-radius: 4px; }}
            QSlider::add-page:horizontal {{ background: {BG_COLOR}; border-radius: 4px; }}
            QCheckBox {{ spacing: 10px; background: transparent; }}
            QCheckBox::indicator {{ width: 20px; height: 20px; }}
            QLabel {{ font-size: 13px; background: transparent; }}
            QPushButton {{ background: {ACCENT_COLOR}; border-radius: 5px; padding: 8px; font-weight: bold; min-width: 80px; color: white; border: 1px solid {ACCENT_COLOR}; }}
            QPushButton:hover {{ background: #4a8ecf; border-color: #4a8ecf; }}
            QPushButton:pressed {{ background: #2a6ebf; border-color: #2a6ebf; }}
        """)

        # Central Widget
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        # 1. Rendering Area
        self.plotter = QtInteractor()
        main_layout.addWidget(self.plotter)
        
        # 2. Control Panel
        self.panel = QtWidgets.QFrame()
        self.panel.setObjectName("control_panel")
        self.panel.setStyleSheet(f"QFrame#control_panel {{ background-color: {PANEL_COLOR}; border-radius: 10px; }}")
        panel_layout = QtWidgets.QGridLayout(self.panel)
        main_layout.addWidget(self.panel)

        # --- Row 1: Load Button & Frequency & Width ---
        self.btn_open = QtWidgets.QPushButton("Open VTK Folder")
        self.btn_open.setToolTip("<p>Open a folder containing exported Mesh2SOFA VTK files to begin visualization.</p>")
        self.btn_open.clicked.connect(self.open_folder)
        panel_layout.addWidget(self.btn_open, 0, 0)

        panel_layout.addWidget(QtWidgets.QLabel("Frequency Step:"), 0, 1)
        self.slider_freq = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_freq.setRange(0, 0)
        self.slider_freq.setToolTip("<p>Select the frequency step index to visualize. Higher indices correspond to higher frequencies.</p>")
        self.slider_freq.valueChanged.connect(self.on_freq_change)
        panel_layout.addWidget(self.slider_freq, 0, 2)
        self.lbl_freq_val = QtWidgets.QLabel("0")
        panel_layout.addWidget(self.lbl_freq_val, 0, 3)

        panel_layout.addWidget(QtWidgets.QLabel("Width (+/-):"), 0, 4)
        self.slider_width = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_width.setRange(0, 10)
        self.slider_width.setToolTip("<p>Average the visualization across a range of neighboring frequency steps (+/- width) to smooth the data.</p>")
        self.slider_width.valueChanged.connect(self.on_width_change)
        panel_layout.addWidget(self.slider_width, 0, 5)
        self.lbl_width_val = QtWidgets.QLabel("0")
        panel_layout.addWidget(self.lbl_width_val, 0, 6)

        # --- Row 2: Ear Checkbox & dB Scaling ---
        self.cb_ear = QtWidgets.QCheckBox("Right Ear")
        self.cb_ear.setChecked(self.current_ear == "Right")
        self.cb_ear.setToolTip("<p>Toggle between visualizing data for the Left or Right ear.</p>")
        self.cb_ear.toggled.connect(self.on_ear_change)
        panel_layout.addWidget(self.cb_ear, 1, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignCenter)

        panel_layout.addWidget(QtWidgets.QLabel("Min dB:"), 1, 1)
        self.slider_min = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_min.setRange(-80, -10)
        self.slider_min.setValue(int(self.clim_min))
        self.slider_min.setToolTip("<p>Set the minimum dB value for the color scale mapping.</p>")
        self.slider_min.valueChanged.connect(self.on_clim_change)
        panel_layout.addWidget(self.slider_min, 1, 2)
        self.lbl_min_val = QtWidgets.QLabel(str(int(self.clim_min)))
        panel_layout.addWidget(self.lbl_min_val, 1, 3)

        panel_layout.addWidget(QtWidgets.QLabel("Max dB:"), 1, 4)
        self.slider_max = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_max.setRange(-5, 5)
        self.slider_max.setValue(int(self.clim_max))
        self.slider_max.setToolTip("<p>Set the maximum dB value for the color scale mapping.</p>")
        self.slider_max.valueChanged.connect(self.on_clim_change)
        panel_layout.addWidget(self.slider_max, 1, 5)
        self.lbl_max_val = QtWidgets.QLabel(str(int(self.clim_max)))
        panel_layout.addWidget(self.lbl_max_val, 1, 6)

        # File Info Label
        self.lbl_info = QtWidgets.QLabel("<p>No VTK files loaded. Click 'Open VTK Folder' to load your Mesh2SOFA project's `\\Output\\VTK` Folder.</p>")
        self.lbl_info.setStyleSheet("font-weight: bold; color: #aaa;")
        main_layout.addWidget(self.lbl_info)

        # Initialize Scene
        self.update_scene()
        self.show()

    def open_folder(self):
        vtk_root = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Mesh2SOFA VTK Folder", self.vtk_root if self.vtk_root else "")
        if vtk_root:
            self.load_vtk_root(vtk_root)

    def load_vtk_root(self, vtk_root):
        try:
            new_data = scan_vtk_files(vtk_root)
            new_ears = [ear for ear, files in new_data.items() if files]
            
            if not new_ears:
                QtWidgets.QMessageBox.warning(self, "Empty Folder", "No VTK files found in 'Left' or 'Right' subfolders.")
                return

            self.vtk_root = vtk_root
            self.vtk_data = new_data
            self.all_ears = new_ears
            
            # Reset state
            self.current_ear = self.all_ears[0]
            self.current_index = 0
            
            # Update Sliders
            max_steps = max(len(files) for files in self.vtk_data.values())
            self.slider_freq.setRange(0, max_steps - 1)
            self.slider_freq.setValue(0)
            self.cb_ear.setChecked(self.current_ear == "Right")
            
            # Clear plotter and reload
            self.plotter.clear()
            self.plotter.enable_eye_dome_lighting()
            self.plotter.enable_depth_peeling(number_of_peels=4, occlusion_ratio=0.0)
            self.plotter.enable_anti_aliasing()
            self.active_actors = {}
            self.update_scene()
            self.plotter.reset_camera()
            self.plotter.render()
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Could not load VTK files: {e}")

    def update_scene(self):
        """Aggregates scalar data from multiple VTK files into a single opaque mesh."""
        if not self.all_ears or self.current_ear not in self.vtk_data:
            return

        files = self.vtk_data[self.current_ear]
        num_files = len(files)
        
        if num_files == 0:
            return

        # Bound current_index
        self.current_index = max(0, min(self.current_index, num_files - 1))
        
        # Calculate target range
        min_target = max(0, self.current_index - self.current_width)
        max_target = min(num_files - 1, self.current_index + self.current_width)
        target_indices = list(range(min_target, max_target + 1))
        
        # 1. Loading and Aggregation
        try:
            # Always load the base mesh from the current center index
            base_file = files[self.current_index]
            mesh = pv.read(base_file)
            
            scalars_key = None
            is_cell_data = False
            
            # Check both Point and Cell data
            for data_source, is_cell in [(mesh.point_data, False), (mesh.cell_data, True)]:
                array_names = data_source.keys()
                # Find first array matching known patterns
                for name in array_names:
                    if "pressure_db" in name.lower() or "20log(pressure" in name.lower():
                        scalars_key = name
                        is_cell_data = is_cell
                        break
                if scalars_key:
                    break
            
            # Fallback to first available array if no pattern match
            if not scalars_key:
                if mesh.point_data.keys():
                    scalars_key = list(mesh.point_data.keys())[0]
                    is_cell_data = False
                elif mesh.cell_data.keys():
                    scalars_key = list(mesh.cell_data.keys())[0]
                    is_cell_data = True

            if scalars_key and len(target_indices) > 1:
                # Initialize accumulator
                data_attr = mesh.cell_data if is_cell_data else mesh.point_data
                aggregated_data = data_attr[scalars_key].copy().astype(float)
                
                # Add data from other files in range
                for idx in target_indices:
                    if idx == self.current_index:
                        continue
                    other_mesh = pv.read(files[idx])
                    other_data_attr = other_mesh.cell_data if is_cell_data else other_mesh.point_data
                    if scalars_key in other_data_attr:
                        aggregated_data += other_data_attr[scalars_key]
                
                # Average the values
                aggregated_data /= len(target_indices)
                data_attr[scalars_key] = aggregated_data

            # Clear and Add single mesh
            self.plotter.clear()
            self.plotter.add_mesh(
                mesh,
                scalars=scalars_key,
                cmap="Spectral_r",
                clim=[self.clim_min, self.clim_max],
                opacity=1.0,
                name="main_mesh",
                show_scalar_bar=True,
                reset_camera=False,
                lighting=True,
                specular=0,
                smooth_shading=True
            )
            
        except Exception as e:
            print(f"Error in update_scene: {e}")
            if self.test_mode: raise e

        # Update UI Labels
        if not self.test_mode:
            current_file = os.path.basename(files[self.current_index])
            # Labels (freq, width, min, max) are updated immediately in event handlers
            
            info_text = (
                f"Ear: {self.current_ear} | "
                f"File: {current_file} ({self.current_index + 1}/{num_files}) | "
                f"Avg. Range: {min_target + 1}-{max_target + 1}"
            )
            self.lbl_info.setText(info_text)
            self.plotter.render()

    def on_freq_change(self, value):
        self.current_index = value
        if not self.test_mode:
            self.lbl_freq_val.setText(str(self.current_index))
        self.render_timer.start(100)

    def on_width_change(self, value):
        self.current_width = value
        if not self.test_mode:
            self.lbl_width_val.setText(str(self.current_width))
        self.render_timer.start(100)

    def on_clim_change(self, _):
        self.clim_min = float(self.slider_min.value())
        self.clim_max = float(self.slider_max.value())
        if not self.test_mode:
            self.lbl_min_val.setText(str(int(self.clim_min)))
            self.lbl_max_val.setText(str(int(self.clim_max)))
        self.render_timer.start(100)

    def on_ear_change(self, is_checked):
        new_ear = "Right" if is_checked else "Left"
        if new_ear in self.all_ears:
            self.current_ear = new_ear
            self.render_timer.start(100)

    def run_test(self):
        print(f"Test Mode: Scanning all files in {self.vtk_root}...")
        for ear in self.all_ears:
            self.current_ear = ear
            num_files = len(self.vtk_data[ear])
            print(f"  Testing {ear} ear ({num_files} files)...")
            for i in range(num_files):
                self.current_index = i
                self.update_scene()
        print("Test Mode: All files loaded successfully.")

def main():
    # 1. Handle CLI Arguments
    vtk_root = None
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        vtk_root = sys.argv[1]
    
    test_mode = "--test-mode" in sys.argv

    # 2. GUI setup
    app = QtWidgets.QApplication(sys.argv)
    
    try:
        viewer = VTKViewerApp(vtk_root, test_mode=test_mode)
        if not test_mode:
            sys.exit(app.exec())
        else:
            sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
