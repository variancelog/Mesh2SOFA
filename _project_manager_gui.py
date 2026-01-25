import customtkinter as ctk
import json
import os
import shutil
from tkinter import filedialog, messagebox
import subprocess
import sys
import threading
import queue

# Appearance Settings
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# --- COLOR CONSTANTS ---
COLOR_ACTIVE = "#2CC985"
COLOR_DONE = "#3B8ED0"
COLOR_LOCKED = "gray25"
COLOR_ERROR = "#C0392B"
HOVER_ACTIVE = "#209F69"
HOVER_DONE = "#36719F"

class ProjectSettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, current_res, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Project Settings")
        self.geometry("400x250")
        
        self.lift()
        self.attributes("-topmost", True)
        self.focus()
        
        self.lbl = ctk.CTkLabel(self, text="Project Configuration", font=("Roboto Medium", 16))
        self.lbl.pack(pady=15)

        self.frame = ctk.CTkFrame(self)
        self.frame.pack(pady=10, padx=20, fill="x")

        # Resolution Switch
        self.lbl_res = ctk.CTkLabel(self.frame, text="Resolution Mode:", font=("Roboto", 12, "bold"))
        self.lbl_res.grid(row=0, column=0, padx=10, pady=15, sticky="w")
        
        self.switch_var = ctk.StringVar(value=current_res)
        
        self.switch = ctk.CTkSwitch(
            self.frame, 
            text="Lowres Mode (< 16GB RAM)", 
            variable=self.switch_var, 
            onvalue="lowres", 
            offvalue="standard"
        )
        self.switch.grid(row=0, column=1, padx=10, pady=15, sticky="e")
        
        self.lbl_desc = ctk.CTkLabel(
            self.frame, 
            text="Standard: Max Freq 21kHz (High RAM)\nLowres: Max Freq 16kHz (Lower RAM)",
            text_color="gray", font=("Roboto", 11)
        )
        self.lbl_desc.grid(row=1, column=0, columnspan=2, padx=10, pady=5)

        self.btn_save = ctk.CTkButton(self, text="Apply Settings", fg_color="green", command=self.on_confirm)
        self.btn_save.pack(pady=20)

    def on_confirm(self):
        self.callback(self.switch_var.get())
        self.destroy()

class MoveCopyDialog(ctk.CTkToplevel):
    """Dialog to ask user whether to Move or Copy a file."""
    def __init__(self, parent, filename, callback):
        super().__init__(parent)
        self.callback = callback
        self.result = None
        
        self.title("Import Mesh")
        self.geometry("400x180")
        self.resizable(False, False)
        
        # Make modal
        self.lift()
        self.attributes("-topmost", True)
        self.focus()
        self.grab_set()
        
        # UI
        lbl = ctk.CTkLabel(self, text=f"The file '{filename}' is outside the project folder.\n\nWould you like to MOVE or COPY it\nto the project 'Meshes' folder?", font=("Roboto", 13))
        lbl.pack(pady=20, padx=20)
        
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10, fill="x")
        
        # Buttons
        btn_cancel = ctk.CTkButton(btn_frame, text="Cancel", fg_color="transparent", border_width=1, text_color=("gray10", "#DCE4EE"), command=self.on_cancel)
        btn_cancel.pack(side="right", padx=10)
        
        btn_move = ctk.CTkButton(btn_frame, text="Move", fg_color="#C0392B", hover_color="#A93226", command=self.on_move)
        btn_move.pack(side="right", padx=10)
        
        btn_copy = ctk.CTkButton(btn_frame, text="Copy", fg_color="#2CC985", hover_color="#209F69", command=self.on_copy)
        btn_copy.pack(side="right", padx=10)

    def on_copy(self):
        self.result = "copy"
        self.callback(self.result)
        self.destroy()

    def on_move(self):
        self.result = "move"
        self.callback(self.result)
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()

class TiltSettingsDialog(ctk.CTkToplevel):
    """Popup window for Extras Generation (Spectral Tilt)"""
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Generate Extras")
        self.geometry("400x250")
        
        self.lift()
        self.attributes("-topmost", True)
        self.focus()
        
        self.lbl = ctk.CTkLabel(self, text="Spectral Tilt Settings", font=("Roboto Medium", 16))
        self.lbl.pack(pady=20)

        self.frame = ctk.CTkFrame(self)
        self.frame.pack(pady=10, padx=20, fill="x")

        self.lbl_tilt = ctk.CTkLabel(self.frame, text="Tilt (dB/octave):")
        self.lbl_tilt.grid(row=0, column=0, padx=10, pady=20)
        
        self.entry_tilt = ctk.CTkEntry(self.frame, placeholder_text="0.0")
        self.entry_tilt.grid(row=0, column=1, padx=10, pady=20)
        self.entry_tilt.insert(0, "0.0") # Default

        self.btn_run = ctk.CTkButton(self, text="Generate CSV & Plot", fg_color="green", command=self.on_confirm)
        self.btn_run.pack(pady=20, padx=20, fill="x")

    def on_confirm(self):
        try:
            val = float(self.entry_tilt.get())
            self.callback(val)
            self.destroy()
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid number for tilt (e.g. -0.8 or 0).")

class HRTFProjectManager(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup
        self.title("Mesh2SOFA (Mesh2HRTF Orchestrator)")
        self.geometry("900x970")
        
        # Data State
        self.project_data = {
            "base_path": "",
            "project_resolution": "standard", # Default: standard or lowres
            "mesh2hrtf_path": "C:/Mesh2HRTF/mesh2hrtf",
            "scripts_path": os.path.dirname(os.path.abspath(__file__)),       
            "grading_bin_path": os.getcwd(),
            "blender_path": "", 
            "eval_grid": "",                   
            "raw_scan": "",
            "progress": 0 
        }

        # Process Management
        self.current_process = None
        self.log_queue = queue.Queue()
        self.is_running = False

        # Layout Configuration
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0) 
        self.grid_rowconfigure(1, weight=0) 
        self.grid_rowconfigure(2, weight=1) 
        
        self.create_widgets()
        
        # Start checking the log queue
        self.check_log_queue()

    def create_widgets(self):
        # --- TITLE AREA ---
        self.frame_top = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_top.grid(row=0, column=0, pady=20, padx=20, sticky="ew")
        
        self.lbl_title = ctk.CTkLabel(self.frame_top, text="No Project Selected", font=("Roboto Medium", 24))
        self.lbl_title.pack(side="left")

        self.frame_controls = ctk.CTkFrame(self.frame_top, fg_color="transparent")
        self.frame_controls.pack(side="right")
        
        self.btn_load = ctk.CTkButton(self.frame_controls, text="Save Project", width=100, command=self.save_project_json)
        self.btn_load.pack(side="right", padx=5)        

        self.btn_refresh = ctk.CTkButton(self.frame_controls, text="↻", width=40, command=self.manual_refresh)
        self.btn_refresh.pack(side="right", padx=5)
        
        self.btn_load = ctk.CTkButton(self.frame_controls, text="Open Project", width=100, fg_color="#444", command=self.load_project_json)
        self.btn_load.pack(side="right", padx=5)

        # SETTINGS BUTTON
        self.btn_settings = ctk.CTkButton(self.frame_controls, text="⚙", width=40, fg_color="#555", command=self.open_settings)
        self.btn_settings.pack(side="right", padx=5)

        self.btn_new = ctk.CTkButton(self.frame_controls, text="New Project", width=100, fg_color="#28a745", hover_color="#218838", command=self.create_new_project)
        self.btn_new.pack(side="right", padx=5)

        # --- SECTION 1: PATHS & CONFIG ---
        self.frame_config = ctk.CTkFrame(self)
        self.frame_config.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        self.frame_config.grid_columnconfigure(1, weight=1)

        self.add_config_row(0, "Project Folder:", "entry_base", "Select project root...", browse_cmd=self.browse_base)
        self.add_config_row(1, "Mesh2HRTF Root:", "entry_m2h", "C:/Mesh2HRTF", browse_cmd=self.browse_m2h)
        
        lbl = ctk.CTkLabel(self.frame_config, text="Evaluation Grid:")
        lbl.grid(row=2, column=0, padx=10, pady=10, sticky="w")
        self.combo_grid = ctk.CTkComboBox(self.frame_config, values=["Set Mesh2HRTF Path first..."])
        self.combo_grid.grid(row=2, column=1, padx=10, pady=10, sticky="ew")
        btn_refresh_grids = ctk.CTkButton(self.frame_config, text="Scan Grids", width=80, command=self.scan_eval_grids)
        btn_refresh_grids.grid(row=2, column=2, padx=10, pady=10)

        # UPDATED: Scripts Location replaced with Blender Path
        self.add_config_row(3, "Blender Executable:", "entry_blender", "Path to blender.exe...", browse_cmd=self.browse_blender)
        self.add_config_row(4, "Grading Tool Bin:", "entry_bins", os.getcwd(), browse_cmd=self.browse_bins)

        # --- SECTION 2: WORKFLOW ACTIONS ---
        self.frame_actions = ctk.CTkFrame(self)
        self.frame_actions.grid(row=2, column=0, padx=20, pady=20, sticky="nsew")
        
        self.lbl_workflow = ctk.CTkLabel(self.frame_actions, text="Workflow Steps", font=("Roboto Medium", 18))
        self.lbl_workflow.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.entry_raw = ctk.CTkEntry(self.frame_actions, placeholder_text="Select Raw Mesh (.obj/.ply)...")
        self.entry_raw.grid(row=1, column=0, padx=10, pady=10, sticky="ew")
        self.btn_raw = ctk.CTkButton(self.frame_actions, text="Select Mesh", command=self.browse_raw)
        self.btn_raw.grid(row=1, column=1, padx=10, pady=10)

        # WORKFLOW BUTTONS
        self.btn_align = ctk.CTkButton(self.frame_actions, text="1. Align Head", command=self.run_alignment)
        self.btn_align.grid(row=2, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
        
        self.btn_process = ctk.CTkButton(self.frame_actions, text="2. Process & Grade", command=self.run_processing)
        self.btn_process.grid(row=3, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
        
        self.btn_blender = ctk.CTkButton(self.frame_actions, text="3. Open in Blender (Setup Scene)", command=self.run_blender_setup)
        self.btn_blender.grid(row=4, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

        self.btn_export = ctk.CTkButton(self.frame_actions, text="4. Export Project (Manual/Script)", state="disabled", fg_color="gray30", text_color="gray")
        self.btn_export.grid(row=5, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

        self.btn_numcalc = ctk.CTkButton(self.frame_actions, text="5. Run NumCalc Simulation", command=self.run_numcalc)
        self.btn_numcalc.grid(row=6, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

        self.btn_sofa = ctk.CTkButton(self.frame_actions, text="6. Generate Mastered SOFA Files", command=self.run_sofa_generation)
        self.btn_sofa.grid(row=7, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

        self.btn_extras = ctk.CTkButton(self.frame_actions, text="7. Generate Extras", command=self.open_tilt_dialog)
        self.btn_extras.grid(row=8, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

        # LOGGING AREA
        self.textbox = ctk.CTkTextbox(self.frame_actions, height=150)
        self.textbox.grid(row=9, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")
        
        self.btn_stop = ctk.CTkButton(self.frame_actions, text="STOP PROCESS", fg_color=COLOR_ERROR, state="disabled", command=self.kill_process)
        self.btn_stop.grid(row=10, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.frame_actions.grid_rowconfigure(9, weight=1)
        self.frame_actions.grid_columnconfigure(0, weight=1)

    def add_config_row(self, row, label_text, attr_name, placeholder, browse_cmd=None):
        lbl = ctk.CTkLabel(self.frame_config, text=label_text)
        lbl.grid(row=row, column=0, padx=10, pady=10, sticky="w")
        entry = ctk.CTkEntry(self.frame_config, placeholder_text=placeholder)
        entry.grid(row=row, column=1, padx=10, pady=10, sticky="ew")
        setattr(self, attr_name, entry)
        if browse_cmd:
            btn = ctk.CTkButton(self.frame_config, text="Browse", width=80, command=browse_cmd)
            btn.grid(row=row, column=2, padx=10, pady=10)

    # --- PROCESS & LOGGING ENGINE ---
    
    def run_external_command(self, cmd_list, cwd=None, shell=False):
        """Standard runner for single process"""
        if self.is_running: return self.log("[!] Process running...")
        
        def target():
            self.is_running = True
            self.btn_stop.configure(state="normal")
            try:
                self.current_process = subprocess.Popen(
                    cmd_list, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, universal_newlines=True, shell=shell
                )
                for line in iter(self.current_process.stdout.readline, ''):
                    self.log_queue.put(line.strip())
                self.current_process.stdout.close()
                rc = self.current_process.wait()
                if rc == 0: self.log_queue.put("[✓] Success.")
                else: self.log_queue.put(f"[X] Failed (Code {rc}).")
            except Exception as e:
                self.log_queue.put(f"[!] Error: {str(e)}")
            finally:
                self.current_process = None
                self.is_running = False
                self.log_queue.put("DONE")
        
        t = threading.Thread(target=target, daemon=True)
        t.start()

    def run_sequential_commands(self, cmd_list_of_lists):
        """Runs multiple commands sequentially in the same thread (for NumCalc Left then Right)"""
        if self.is_running: return self.log("[!] Process running...")

        def target():
            self.is_running = True
            self.btn_stop.configure(state="normal")
            
            for cmd in cmd_list_of_lists:
                if not self.is_running: break # Stop requested
                
                try:
                    # Log which script is starting (e.g. Left or Right)
                    desc = "NumCalc Step"
                    if "Left" in str(cmd): desc = "Left Ear Simulation"
                    elif "Right" in str(cmd): desc = "Right Ear Simulation"
                    
                    self.log_queue.put(f"--> Starting: {desc}...")
                    
                    self.current_process = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1, universal_newlines=True
                    )
                    
                    for line in iter(self.current_process.stdout.readline, ''):
                        self.log_queue.put(line.strip())
                    
                    self.current_process.stdout.close()
                    rc = self.current_process.wait()
                    
                    if rc != 0:
                        self.log_queue.put(f"[!] Step failed with code {rc}")
                        break
                        
                except Exception as e:
                    self.log_queue.put(f"[!] Execution Error: {e}")
                    break
            
            self.current_process = None
            self.is_running = False
            self.log_queue.put("DONE")

        t = threading.Thread(target=target, daemon=True)
        t.start()

    def check_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if msg == "DONE":
                    self.manual_refresh()
                    self.btn_stop.configure(state="disabled")
                else:
                    self.log(msg, timestamp=False)
        except queue.Empty:
            pass
        self.after(100, self.check_log_queue)

    def kill_process(self):
        self.is_running = False # Flags the loops to stop
        if self.current_process:
            self.log("[!] Attempting to stop script...")
            try: self.current_process.kill()
            except: pass
        
        # FORCE KILL NUMCALC (Windows)
        if sys.platform == 'win32':
            subprocess.run("taskkill /F /IM NumCalc.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.log("[!] Terminated NumCalc.exe background processes.")

    def log(self, message, timestamp=True):
        self.textbox.configure(state="normal")
        if timestamp:
            from datetime import datetime
            prefix = datetime.now().strftime("[%H:%M:%S] ")
            self.textbox.insert("end", f"{prefix}{message}\n")
        else:
            self.textbox.insert("end", f"{message}\n")
        self.textbox.see("end")
        self.textbox.configure(state="disabled")

    # --- SETTINGS HANDLERS ---
    def open_settings(self):
        current = self.project_data.get("project_resolution", "standard")
        ProjectSettingsDialog(self, current, self.update_settings).grab_set()

    def update_settings(self, new_res):
        self.project_data["project_resolution"] = new_res
        self.log(f"Project Resolution set to: {new_res.upper()}")
        self.save_project_json(silent=True)

    # --- PROJECT CREATION & PATHS ---
    def create_new_project(self):
        target_dir = filedialog.askdirectory(title="Select Folder for New Project")
        if not target_dir: return

        folders = ["Meshes", "Exports", "Output"]
        created_log = []
        for folder in folders:
            path = os.path.join(target_dir, folder)
            if not os.path.exists(path):
                try:
                    os.makedirs(path)
                    created_log.append(folder)
                except Exception as e:
                    self.log(f"Error creating {folder}: {e}")
        
        self.log(f"Project created at: {target_dir}")
        self.entry_base.delete(0, "end")
        self.entry_base.insert(0, target_dir)
        self.entry_raw.delete(0, "end")
        
        self.save_project_json(silent=True)
        self.update_workflow_state()

    def get_project_name(self):
        base_path = self.entry_base.get()
        if base_path and os.path.isdir(base_path):
            return os.path.basename(os.path.normpath(base_path))
        return "Project"

    def get_mesh_dir(self):
        base_path = self.entry_base.get()
        meshes_path = os.path.join(base_path, "Meshes")
        if os.path.exists(meshes_path):
            return meshes_path
        return base_path

    # --- SMART MESH2HRTF LOGIC ---
    def get_valid_m2h_input_path(self):
        raw_path = self.entry_m2h.get()
        if not raw_path or not os.path.exists(raw_path): return None
        
        candidate_1 = os.path.join(raw_path, "mesh2hrtf", "Mesh2Input")
        if os.path.exists(candidate_1): return candidate_1
        
        candidate_2 = os.path.join(raw_path, "Mesh2Input")
        if os.path.exists(candidate_2): return candidate_2
        
        return None

    def scan_eval_grids(self):
        m2h_input = self.get_valid_m2h_input_path()
        if not m2h_input: 
            return self.log("Error: Could not locate 'Mesh2Input' folder in Mesh2HRTF path.")
            
        grid_path = os.path.join(m2h_input, "EvaluationGrids", "Data")
        
        if os.path.exists(grid_path):
            folders = [f for f in os.listdir(grid_path) if os.path.isdir(os.path.join(grid_path, f))]
            self.combo_grid.configure(values=folders)
            if folders: self.combo_grid.set(folders[0])
            self.log(f"Found {len(folders)} grids in {grid_path}")
        else:
            self.log(f"Error: Could not find grids at {grid_path}")

    def get_binary_path(self, tool_name):
        root = os.path.normpath(self.entry_m2h.get())
        candidates = [
            os.path.join(root, tool_name, "bin", f"{tool_name}.exe"),
            os.path.join(root, "mesh2hrtf", tool_name, "bin", f"{tool_name}.exe"), 
            os.path.join(root, tool_name, f"{tool_name}.exe")
        ]
        for p in candidates:
            if os.path.exists(p): return p
        return None

    # --- BROWSERS ---
    def browse_base(self): self._browse_dir(self.entry_base)
    def browse_m2h(self): self._browse_dir(self.entry_m2h)
    def browse_bins(self): self._browse_dir(self.entry_bins)
    
    def _browse_dir(self, entry_widget):
        path = filedialog.askdirectory()
        if path:
            entry_widget.delete(0, "end")
            entry_widget.insert(0, path)
            self.update_workflow_state()

    def browse_blender(self):
        # Look for executable (.exe on Windows, anything on Mac/Linux)
        filetypes = [("Executables", "*.exe"), ("All Files", "*.*")] if sys.platform == "win32" else []
        path = filedialog.askopenfilename(title="Select Blender Executable", filetypes=filetypes)
        if path:
            self.entry_blender.delete(0, "end")
            self.entry_blender.insert(0, path)
            self.save_project_json()

    # --- Smart move raw mesh to Meshes folder ---
    def browse_raw(self):
        # 1. Check if project is valid first
        if not self.entry_base.get():
            return messagebox.showerror("Error", "Please define a Project Folder first.")

        # 2. Open File Dialog
        src_path = filedialog.askopenfilename(filetypes=[("3D Mesh", "*.obj *.ply *.stl")])
        if not src_path: 
            return

        # 3. Determine Paths
        project_mesh_dir = self.get_mesh_dir()
        if not os.path.exists(project_mesh_dir):
            os.makedirs(project_mesh_dir, exist_ok=True)

        src_dir = os.path.dirname(os.path.normpath(src_path))
        dest_dir = os.path.normpath(project_mesh_dir)
        filename = os.path.basename(src_path)
        dest_path = os.path.join(dest_dir, filename)

        # 4. Helper Function to Finalize Selection
        def finalize_selection(final_path):
            self.entry_raw.delete(0, "end")
            self.entry_raw.insert(0, final_path)
            self.save_project_json(silent=True) # Save immediately per requirement
            self.update_workflow_state()

        # 5. Logic: Check location
        if src_dir == dest_dir:
            # A: Already in folder -> Just use it
            finalize_selection(src_path)
        else:
            # B: Outside folder -> Ask User
            def on_dialog_result(action):
                if not action: return # User cancelled
                
                try:
                    if action == "copy":
                        self.log(f"Copying {filename} to Meshes folder...")
                        shutil.copy2(src_path, dest_path)
                    elif action == "move":
                        self.log(f"Moving {filename} to Meshes folder...")
                        shutil.move(src_path, dest_path)
                    
                    finalize_selection(dest_path)
                    
                except Exception as e:
                    self.log(f"[!] Error during {action}: {e}")
                    messagebox.showerror("File Error", str(e))

            # Launch Custom Dialog
            MoveCopyDialog(self, filename, on_dialog_result)

    # --- STATE MANAGEMENT ---
    def update_ui_from_data(self):
        d = self.project_data
        self.entry_base.delete(0, "end"); self.entry_base.insert(0, d.get("base_path", ""))
        self.entry_m2h.delete(0, "end"); self.entry_m2h.insert(0, d.get("mesh2hrtf_path", ""))
        self.entry_blender.delete(0, "end"); self.entry_blender.insert(0, d.get("blender_path", ""))
        self.entry_bins.delete(0, "end"); self.entry_bins.insert(0, d.get("grading_bin_path", ""))
        self.entry_raw.delete(0, "end"); self.entry_raw.insert(0, d.get("raw_scan", ""))
        if d.get("eval_grid"): self.combo_grid.set(d.get("eval_grid"))
        self.update_workflow_state()

    def load_project_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON Project", "*.json")])
        if not path: return
        try:
            with open(path, 'r') as f:
                data = json.load(f)
                self.project_data.update(data)
                if "project_resolution" not in self.project_data:
                    self.project_data["project_resolution"] = "standard"
                self.update_ui_from_data()
                self.log(f"Loaded project: {path} ({self.project_data['project_resolution']})")
        except Exception as e:
            self.log(f"Error loading project: {e}")

    def save_project_json(self, silent=False):
        self.project_data.update({
            "base_path": self.entry_base.get(),
            "mesh2hrtf_path": self.entry_m2h.get(),
            "scripts_path": os.path.dirname(os.path.abspath(__file__)), # AUTO-DETECTED
            "blender_path": self.entry_blender.get(),
            "grading_bin_path": self.entry_bins.get(),
            "eval_grid": self.combo_grid.get(),
            "raw_scan": self.entry_raw.get()
        })
        if not os.path.exists(self.project_data["base_path"]):
            return
            
        json_path = os.path.join(self.project_data["base_path"], "project.json")
        try:
            with open(json_path, 'w') as f:
                json.dump(self.project_data, f, indent=4)
            if not silent: self.log(f"Settings saved to {json_path}")
        except Exception as e:
            if not silent: self.log(f"Error saving: {e}")

    def manual_refresh(self):
        self.update_workflow_state()
        self.log("Status refreshed.")

    def update_workflow_state(self):
        base_path = self.entry_base.get()
        proj_name = self.get_project_name()
        res_mode = self.project_data.get("project_resolution", "standard").upper()
        self.lbl_title.configure(text=f"Project: {proj_name} [{res_mode}]")

        mesh_path = self.get_mesh_dir()
        
        step1_done = os.path.exists(os.path.join(mesh_path, "aligned_head.ply"))
        step2_done = step1_done and os.path.exists(os.path.join(mesh_path, "Left_Graded.ply"))
        
        blend_file = os.path.join(base_path, f"{proj_name}.blend")
        step3_done = step2_done and os.path.exists(blend_file)
        
        step4_done = step3_done and os.path.exists(os.path.join(base_path, "Exports", "Left_Project"))
        
        nc_log_left = os.path.join(base_path, "Exports", "Left_Project", "NumCalc", "source_1", "NC.out")
        step5_done = step4_done and os.path.exists(nc_log_left)
        
        output_dir = os.path.join(base_path, "Output")
        step6_done = step5_done and os.path.exists(output_dir) and any(f.endswith(".sofa") for f in os.listdir(output_dir))

        progress_index = 0
        if step1_done: progress_index = 1
        if step2_done: progress_index = 2
        if step3_done: progress_index = 3
        if step4_done: progress_index = 4
        if step5_done: progress_index = 5
        if step6_done: progress_index = 6
        
        self.project_data["progress"] = progress_index
        
        def set_btn(btn, state_idx):
            if state_idx < progress_index:
                btn.configure(state="normal", fg_color=COLOR_DONE, hover_color=HOVER_DONE, text_color="white")
            elif state_idx == progress_index:
                btn.configure(state="normal", fg_color=COLOR_ACTIVE, hover_color=HOVER_ACTIVE, text_color="black")
            else:
                btn.configure(state="disabled", fg_color=COLOR_LOCKED, text_color="grey")

        set_btn(self.btn_align, 0)
        set_btn(self.btn_process, 1)
        set_btn(self.btn_blender, 2)
        set_btn(self.btn_numcalc, 4) 
        set_btn(self.btn_sofa, 5)
        
        if step6_done:
            self.btn_extras.configure(state="normal", fg_color=COLOR_ACTIVE, text_color="black")
        else:
            self.btn_extras.configure(state="disabled", fg_color=COLOR_LOCKED)

    # --- WORKFLOW RUNNERS ---

    def run_alignment(self):
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(scripts_dir, "align_head.py")
        raw_mesh = self.entry_raw.get()
        if not raw_mesh or not os.path.exists(raw_mesh): return self.log("Error: Select a raw mesh first.")
        
        mesh_dir = self.get_mesh_dir()
        if not os.path.exists(mesh_dir): os.makedirs(mesh_dir) 
        
        output_mesh = os.path.join(mesh_dir, "aligned_head.ply")
        
        if not os.path.exists(script_path): return self.log(f"Error: {script_path} missing")
        
        self.log("--> Starting Alignment Step...")
        # Added -u for unbuffered output
        cmd = [sys.executable, "-u", script_path, raw_mesh, output_mesh]
        self.run_external_command(cmd)

    def run_processing(self):
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(scripts_dir, "process_and_grade.py")
        mesh_dir = self.get_mesh_dir()
        aligned_mesh = os.path.join(mesh_dir, "aligned_head.ply")
        bin_dir = os.path.normpath(self.entry_bins.get())
        grading_exe = os.path.join(bin_dir, "hrtf_mesh_grading.exe")
        
        if not os.path.exists(grading_exe): return self.log(f"Error: Grading binary missing at {grading_exe}")
        
        self.log("--> Starting Processing & Grading...")
        cmd = [sys.executable, "-u", script_path, aligned_mesh, grading_exe]
        self.run_external_command(cmd)

    def run_blender_setup(self):
        # Prioritize path in entry, then project data
        blender_exe = self.entry_blender.get()
        if not blender_exe or not os.path.exists(blender_exe):
            return self.log("[ERROR] Blender path is invalid. Please configure it above.")

        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(scripts_dir, "setup_blender_scene.py")
        ref_blend = os.path.join(scripts_dir, "3d_reference.blend")
        
        base_folder = os.path.normpath(self.entry_base.get())
        proj_name = self.get_project_name()
        proj_blend = os.path.join(base_folder, f"{proj_name}.blend")
        
        if not os.path.exists(proj_blend):
            try:
                shutil.copy(ref_blend, proj_blend)
                self.log(f"   [i] Created {proj_name}.blend")
            except Exception as e:
                return self.log(f"[ERROR] Copy failed: {e}")

        mesh_folder = self.get_mesh_dir()
        
        self.log("--> Launching Blender...")
        cmd = [blender_exe, proj_blend, "--python", script_path, "--", mesh_folder]
        subprocess.Popen(cmd) 
        self.log("[i] Blender launched separately.")

    def run_numcalc(self):
        numcalc_exe = self.get_binary_path("NumCalc")
        if not numcalc_exe: return self.log("[ERROR] NumCalc.exe not found.")
        
        res_mode = self.project_data.get("project_resolution", "standard")
        freq_label = "16 kHz" if res_mode == "lowres" else "21 kHz"

        choice = messagebox.askyesno("Simulation Options", f"Run STABILITY TEST only ({freq_label})?\n\nNo = Run Full Simulation")
        
        base_folder = os.path.normpath(self.entry_base.get())
        left_proj = os.path.join(base_folder, "Exports", "Left_Project")
        right_proj = os.path.join(base_folder, "Exports", "Right_Project")
        
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        
        if choice:
            test_script = os.path.join(scripts_dir, "run_numcalc_test.py")
            self.log(f"--> Running Stability Test ({freq_label})...")
            # Stability Test uses python Popen inside script? No, it uses run_external_command.
            # run_numcalc_test expects arguments.
            cmd_left = [sys.executable, "-u", test_script, left_proj, numcalc_exe]
            
            # For simplicity, we just run the Left Ear test in the main GUI window.
            self.run_external_command(cmd_left)
            
        else:
            self.log(f"--> Starting Full Simulation ({res_mode.upper()})...")
            
            m2h_input_path = self.get_valid_m2h_input_path()
            if not m2h_input_path: return self.log("[ERROR] Mesh2Input not found.")
            
            m2h_root_inner = os.path.dirname(m2h_input_path) 
            full_script = os.path.join(m2h_root_inner, "NumCalc", "manage_numcalc_script.py")
            
            if not os.path.exists(full_script): return self.log(f"[ERROR] Script missing: {full_script}")
            
            # --- FIX: Pass Directory on Windows, Exe on Others ---
            if sys.platform == 'win32':
                numcalc_arg = os.path.dirname(numcalc_exe)
            else:
                numcalc_arg = numcalc_exe
            
            # --- FIX: Run Full Simulation on Exports Folder ---
            # Targets the 'Exports' directory so manage_numcalc handles both ears
            exports_dir = os.path.join(base_folder, "Exports")
            
            cmd = [sys.executable, "-u", full_script, "--project_path", exports_dir, "--numcalc_path", numcalc_arg]
            
            self.run_external_command(cmd)

    def run_sofa_generation(self):
        m2h_input_root = self.get_valid_m2h_input_path()
        if not m2h_input_root: return self.log("[ERROR] Mesh2HRTF/Mesh2Input not found.")
        m2h_root = self.entry_m2h.get()

        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(scripts_dir, "generate_sofa_outputs.py")

        base_folder = os.path.normpath(self.entry_base.get())
        output_dir = os.path.join(base_folder, "Output")
        os.makedirs(output_dir, exist_ok=True)
        
        left_proj = os.path.join(base_folder, "Exports", "Left_Project")
        right_proj = os.path.join(base_folder, "Exports", "Right_Project")
        
        self.log("--> Generating SOFA Files...")
        cmd = [sys.executable, "-u", script_path, "--left", left_proj, "--right", right_proj, "--m2h_path", m2h_root, "--output", output_dir]
        self.run_external_command(cmd)

    def open_tilt_dialog(self):
        TiltSettingsDialog(self, self.run_extras_script).grab_set()

    def run_extras_script(self, tilt_value):
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(scripts_dir, "generate_extras.py")
        base_folder = os.path.normpath(self.entry_base.get())
        output_dir = os.path.join(base_folder, "Output")
        input_sofa = os.path.join(output_dir, "HRIR_48000Hz.sofa")
        
        if not os.path.exists(input_sofa):
            return self.log(f"[ERROR] Required file missing: {input_sofa}. Please run Step 6 first.")
        
        self.log(f"--> Generating Extras (Tilt: {tilt_value})...")
        cmd = [sys.executable, "-u", script_path, "--input", input_sofa, "--output_dir", output_dir, "--tilt", str(tilt_value)]
        self.run_external_command(cmd)

if __name__ == "__main__":
    app = HRTFProjectManager()
    app.mainloop()