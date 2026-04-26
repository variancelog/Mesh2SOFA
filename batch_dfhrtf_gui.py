import sys
import os
import glob
import subprocess
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QLabel, QDoubleSpinBox, QTextEdit, QFrame, QHBoxLayout, QCheckBox)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QDragEnterEvent, QDropEvent

class Worker(QThread):
    log_msg = Signal(str)

    def __init__(self, queue, tilt, sonicom, script_path):
        super().__init__()
        self.queue = queue
        self.tilt = tilt
        self.sonicom = sonicom
        self.script_path = script_path

    def run(self):
        total = len(self.queue)
        for i, (input_file, output_dir) in enumerate(self.queue):
            filename = os.path.basename(input_file)
            self.log_msg.emit(f"--- Processing ({i+1}/{total}): {filename} ---")
            
            # Ensure output directory exists
            os.makedirs(output_dir, exist_ok=True)
            
            cmd = [
                sys.executable,
                self.script_path,
                "--input", input_file,
                "--output_dir", output_dir,
                "--tilt", str(self.tilt)
            ]
            if self.sonicom:
                cmd.append("--sonicom")
            
            try:
                # Creation flags to prevent a new console window from popping up on Windows
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                
                process = subprocess.Popen(
                    cmd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.STDOUT, 
                    text=True, 
                    creationflags=creationflags
                )
                
                for line in process.stdout:
                    if line.strip():
                        self.log_msg.emit(f"  {line.strip()}")
                        
                process.wait()
                
                if process.returncode == 0:
                    self.log_msg.emit(f"[SUCCESS] Finished: {filename}\n")
                else:
                    self.log_msg.emit(f"[ERROR] Failed {filename} (Return code: {process.returncode})\n")
            except Exception as e:
                self.log_msg.emit(f"[ERROR] Failed to execute script: {e}\n")
                
        self.log_msg.emit("All batch tasks completed.")

class DropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.setStyleSheet("""
            QFrame {
                border: 2px dashed #aaa;
                border-radius: 8px;
                background-color: #f9f9f9;
            }
        """)
        layout = QVBoxLayout()
        label = QLabel("Drag & Drop a .sofa file or a folder here")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("border: none; background: transparent; color: #555; font-size: 14px; font-weight: bold;")
        layout.addWidget(label)
        self.setLayout(layout)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                QFrame {
                    border: 2px dashed #4CAF50;
                    border-radius: 8px;
                    background-color: #e8f5e9;
                }
            """)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet("""
            QFrame {
                border: 2px dashed #aaa;
                border-radius: 8px;
                background-color: #f9f9f9;
            }
        """)

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet("""
            QFrame {
                border: 2px dashed #aaa;
                border-radius: 8px;
                background-color: #f9f9f9;
            }
        """)
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        self.files_dropped.emit(paths)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DFHRTF Batch Generator")
        self.setFixedSize(500, 450)
        self.script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate_extras.py")

        # Main widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Tilt input
        tilt_layout = QHBoxLayout()
        tilt_label = QLabel("Spectral Tilt (dB/oct):")
        tilt_label.setStyleSheet("font-size: 13px;")
        self.tilt_spinbox = QDoubleSpinBox()
        self.tilt_spinbox.setRange(-20.0, 20.0)
        self.tilt_spinbox.setSingleStep(0.5)
        self.tilt_spinbox.setValue(0.0)
        self.tilt_spinbox.setDecimals(1)
        self.tilt_spinbox.setStyleSheet("font-size: 13px; padding: 2px;")
        
        tilt_layout.addWidget(tilt_label)
        tilt_layout.addWidget(self.tilt_spinbox)
        tilt_layout.addStretch()
        layout.addLayout(tilt_layout)
        
        # SONICOM Export checkbox
        self.sonicom_checkbox = QCheckBox("SONICOM Export (Squiglink)")
        self.sonicom_checkbox.setStyleSheet("font-size: 13px; margin-bottom: 5px;")
        layout.addWidget(self.sonicom_checkbox)

        # Drop zone
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self.handle_dropped_items)
        layout.addWidget(self.drop_zone, stretch=2)

        # Log console
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self.log_console, stretch=3)

        self.worker = None

    def log(self, message):
        self.log_console.append(message)
        # Scroll to bottom
        scrollbar = self.log_console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def handle_dropped_items(self, paths):
        if self.worker is not None and self.worker.isRunning():
            self.log("[WARNING] Wait for the current batch to finish before dropping new files.")
            return

        queue = []
        for path in paths:
            if os.path.isfile(path) and path.lower().endswith('.sofa'):
                output_dir = os.path.join(os.path.dirname(path), "DFHRTF")
                queue.append((path, output_dir))
            elif os.path.isdir(path):
                # Search for .sofa files
                sofa_files = glob.glob(os.path.join(path, "*.sofa"))
                if sofa_files:
                    output_dir = os.path.join(path, "DFHRTF")
                    for sf in sofa_files:
                        queue.append((sf, output_dir))
                else:
                    self.log(f"[INFO] No .sofa files found in directory: {os.path.basename(path)}")
            else:
                self.log(f"[INFO] Ignored invalid item: {os.path.basename(path)}")

        if not queue:
            self.log("[INFO] No valid .sofa files to process.")
            return

        self.log(f"\n[START] Found {len(queue)} .sofa file(s) to process.")
        tilt_value = self.tilt_spinbox.value()
        sonicom_value = self.sonicom_checkbox.isChecked()
        
        # Start worker thread
        self.worker = Worker(queue, tilt_value, sonicom_value, self.script_path)
        self.worker.log_msg.connect(self.log)
        self.worker.finished.connect(self.on_batch_finished)
        self.worker.start()

    def on_batch_finished(self):
        pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Optional: set a modern fusion style
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())