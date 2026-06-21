import customtkinter as ctk
import json
import os
import shutil
from tkinter import filedialog, messagebox
import subprocess
import sys
import threading
import queue
import ctypes

from project_store import ProjectStore, CleanState, MESH_ALIGNED, MESH_GRADED

# Hide the console window so running as .py looks like .pyw (no terminal).
# A real (hidden) console still exists, so child processes (e.g. NumCalc.exe)
# can inherit it without spawning their own console windows.
if sys.platform == 'win32':
    _hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if _hwnd:
        ctypes.windll.user32.ShowWindow(_hwnd, 0)  # SW_HIDE

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

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

class Tooltip:
    """Lightweight tooltip bound to any tkinter/CTk widget."""
    def __init__(self, widget, text):
        self._widget = widget
        self._text = text
        self._tip_win = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, event=None):
        if self._tip_win or not self._text:
            return
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip_win = tw = __import__("tkinter").Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        import tkinter as tk
        lbl = tk.Label(
            tw, text=self._text, justify="left",
            background="#2b2b2b", foreground="#e0e0e0",
            relief="solid", borderwidth=1,
            font=("Roboto", 10), wraplength=280, padx=6, pady=4
        )
        lbl.pack()

    def _hide(self, event=None):
        if self._tip_win:
            self._tip_win.destroy()
            self._tip_win = None


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

class BlenderOpenDialog(ctk.CTkToplevel):
    """Asks whether to overwrite the existing project .blend or open it as-is."""
    def __init__(self, parent, proj_name, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Open in Blender")
        self.geometry("440x190")
        self.resizable(False, False)
        self.lift()
        self.attributes("-topmost", True)
        self.focus()
        self.grab_set()

        lbl = ctk.CTkLabel(self,
            text=f"A Blender file for '{proj_name}' already exists.\n\n"
                 "Open the existing file without changes?\n"
                 "or Overwrite it (delete and re-import the graded meshes),\n"
                 ,
            font=("Roboto", 13))
        lbl.pack(pady=20, padx=20)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10, fill="x")

        ctk.CTkButton(btn_frame, text="Cancel", fg_color="transparent", border_width=1,
                      text_color=("gray10", "#DCE4EE"), command=self.on_cancel).pack(side="right", padx=10)
        ctk.CTkButton(btn_frame, text="Overwrite Existing", fg_color="#C0392B",
                      hover_color="#A93226", command=self.on_overwrite).pack(side="right", padx=10)
        ctk.CTkButton(btn_frame, text="Open Existing", fg_color="#2CC985",
                      hover_color="#209F69", command=self.on_open).pack(side="right", padx=10)

    def on_overwrite(self): self.callback("overwrite"); self.destroy()
    def on_open(self):      self.callback("open");      self.destroy()
    def on_cancel(self):    self.destroy()

class NumCalcOptionsDialog(ctk.CTkToplevel):
    """Asks whether to run a stability test, the full simulation, or cancel."""
    def __init__(self, parent, freq_label, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Run NumCalc Simulation")
        self.geometry("460x210")
        self.resizable(False, False)
        self.lift()
        self.attributes("-topmost", True)
        self.focus()
        self.grab_set()

        lbl = ctk.CTkLabel(self,
            text=f"Run a quick STABILITY TEST ({freq_label}) on both ears,\n"
                 "or start the FULL simulation?\n\n"
                 "(The full simulation is very compute-intensive and\n"
                 "can take 8-24 hours.)",
            font=("Roboto", 13))
        lbl.pack(pady=20, padx=20)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10, fill="x")

        ctk.CTkButton(btn_frame, text="Cancel", fg_color="transparent", border_width=1,
                      text_color=("gray10", "#DCE4EE"), command=self.on_cancel).pack(side="right", padx=10)
        ctk.CTkButton(btn_frame, text="Full Sim", fg_color="#C0392B",
                      hover_color="#A93226", command=self.on_full).pack(side="right", padx=10)
        ctk.CTkButton(btn_frame, text="Test Only", fg_color="#2CC985",
                      hover_color="#209F69", command=self.on_test).pack(side="right", padx=10)

    def on_test(self):   self.callback("test"); self.destroy()
    def on_full(self):   self.callback("full"); self.destroy()
    def on_cancel(self): self.destroy()

class TiltSettingsDialog(ctk.CTkToplevel):
    """Popup window for DFHRTF Generation (Spectral Tilt)"""
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
            messagebox.showerror("Error", "Please enter a valid number for tilt (e.g. -1.0 or 0).")

class VTKSettingsDialog(ctk.CTkToplevel):
    """Popup window for VTK Export (Frequency Range)"""
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Generate Paraview VTK Files")
        self.geometry("400x250")
        
        self.lift()
        self.attributes("-topmost", True)
        self.focus()
        
        self.lbl = ctk.CTkLabel(self, text="VTK Export Settings", font=("Roboto Medium", 16))
        self.lbl.pack(pady=20)

        self.frame = ctk.CTkFrame(self)
        self.frame.pack(pady=10, padx=20, fill="x")

        # Min Freq
        self.lbl_min = ctk.CTkLabel(self.frame, text="Min Freq (Hz):")
        self.lbl_min.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.entry_min = ctk.CTkEntry(self.frame, placeholder_text="1000")
        self.entry_min.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        self.entry_min.insert(0, "1000")

        # Max Freq
        self.lbl_max = ctk.CTkLabel(self.frame, text="Max Freq (Hz):")
        self.lbl_max.grid(row=1, column=0, padx=10, pady=10, sticky="w")
        self.entry_max = ctk.CTkEntry(self.frame, placeholder_text="16000")
        self.entry_max.grid(row=1, column=1, padx=10, pady=10, sticky="ew")
        self.entry_max.insert(0, "16000")

        self.frame.grid_columnconfigure(1, weight=1)

        self.btn_run = ctk.CTkButton(self, text="Generate VTK Files", fg_color="green", command=self.on_confirm)
        self.btn_run.pack(pady=10, padx=20, fill="x")

    def on_confirm(self):
        try:
            min_freq = float(self.entry_min.get())
            max_freq = float(self.entry_max.get())
            if min_freq < 0 or max_freq < min_freq:
                raise ValueError
            self.callback(min_freq, max_freq)
            self.destroy()
        except ValueError:
            messagebox.showerror("Error", "Please enter valid positive numbers (Min <= Max).")

class GridSelectionDialog(ctk.CTkToplevel):
    """Dialog to allow multi-selection of evaluation grids."""
    def __init__(self, parent, available_grids, selected_grids_str, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Select Evaluation Grids")
        self.geometry("350x400")
        
        self.attributes('-topmost', True)
        self.focus_force()
        self.grab_set()

        self.lbl_title = ctk.CTkLabel(self, text="Select Grids to Process:", font=("Roboto", 14, "bold"))
        self.lbl_title.pack(pady=15)

        self.scroll_frame = ctk.CTkScrollableFrame(self, width=300, height=250)
        self.scroll_frame.pack(pady=10, padx=20, fill="both", expand=True)

        self.checkboxes = {}
        selected_list = [g.strip() for g in selected_grids_str.split(",")] if selected_grids_str else []

        for grid in available_grids:
            var = ctk.StringVar(value="on" if grid in selected_list else "off")
            cb = ctk.CTkCheckBox(self.scroll_frame, text=grid, variable=var, onvalue="on", offvalue="off")
            cb.pack(pady=5, anchor="w")
            self.checkboxes[grid] = var

        self.btn_save = ctk.CTkButton(self, text="Save Selection", fg_color="green", command=self.on_save)
        self.btn_save.pack(pady=15)

    def on_save(self):
        selected = [grid for grid, var in self.checkboxes.items() if var.get() == "on"]
        self.callback(",".join(selected))
        self.destroy()

class MeshQualityDialog(ctk.CTkToplevel):
    """Dialog shown when graded meshes have critical quality issues (blocks Blender)."""
    def __init__(self, parent, mesh_path, callback):
        super().__init__(parent)
        self.callback = callback
        self.mesh_path = mesh_path
        self.title("Mesh Quality Issues — Blender Blocked")
        self.geometry("500x210")
        self.resizable(False, False)
        self.lift()
        self.attributes("-topmost", True)
        self.focus()
        self.grab_set()

        lbl = ctk.CTkLabel(self,
            text="One or more graded meshes have critical quality issues.\n"
                 "The Blender step is blocked until they are resolved.\n\n"
                 "Repair automatically, or open the problem viewer to inspect.",
            font=("Roboto", 13))
        lbl.pack(pady=20, padx=20)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10, fill="x")

        ctk.CTkButton(btn_frame, text="Cancel", fg_color="transparent", border_width=1,
                      text_color=("gray10", "#DCE4EE"), command=self.on_cancel).pack(side="right", padx=10)
        ctk.CTkButton(btn_frame, text="Visualize Problems", fg_color="#C0392B",
                      hover_color="#A93226", command=self.on_visualize).pack(side="right", padx=10)
        ctk.CTkButton(btn_frame, text="Attempt Repair", fg_color="#2CC985",
                      hover_color="#209F69", command=self.on_repair).pack(side="right", padx=10)

    def on_repair(self):    self.callback("repair", self.mesh_path);    self.destroy()
    def on_visualize(self): self.callback("visualize", self.mesh_path); self.destroy()
    def on_cancel(self):    self.destroy()


class AlignedMeshDialog(ctk.CTkToplevel):
    """Dialog for the standalone 'Inspect & Fix Mesh' step (operates on the
    aligned mesh). Repair is suppressed when the only criticals are topological
    tunnels (genus>0), which geometric repair cannot fix — those go to the
    interactive tunnel viewer for click-to-select + cut & cap."""
    def __init__(self, parent, mesh_dir, summary, tunnel_only, callback):
        super().__init__(parent)
        self.callback = callback
        self.mesh_dir = mesh_dir
        self.title("Mesh Quality Issues — Inspect & Fix")
        self.geometry("560x300")
        self.resizable(False, False)
        self.lift()
        self.attributes("-topmost", True)
        self.focus()
        self.grab_set()

        if tunnel_only:
            hint = ("Topological tunnel(s) detected. Geometric repair cannot fix these —\n"
                    "open the tunnel viewer, click the cut loop, and Apply Cut & Cap.")
        else:
            hint = ("Critical mesh issues found. Attempt Repair (pymeshfix) to clean\n"
                    "self-intersections / non-manifold / boundaries, then re-inspect.")
        ctk.CTkLabel(self, text=hint, font=("Roboto", 13), justify="left").pack(pady=(16, 6), padx=20)

        box = ctk.CTkTextbox(self, height=110, width=520)
        box.pack(pady=6, padx=20, fill="both", expand=False)
        box.insert("end", summary)
        box.configure(state="disabled")

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10, fill="x")

        ctk.CTkButton(btn_frame, text="Cancel", fg_color="transparent", border_width=1,
                      text_color=("gray10", "#DCE4EE"), command=self.on_cancel).pack(side="right", padx=10)
        ctk.CTkButton(btn_frame, text="Fix Tunnels (Viewer)", fg_color="#C0392B",
                      hover_color="#A93226", command=self.on_visualize).pack(side="right", padx=10)
        if not tunnel_only:
            ctk.CTkButton(btn_frame, text="Attempt Repair", fg_color="#2CC985",
                          hover_color="#209F69", command=self.on_repair).pack(side="right", padx=10)

    def on_repair(self):    self.callback("repair", self.mesh_dir);    self.destroy()
    def on_visualize(self): self.callback("visualize", self.mesh_dir); self.destroy()
    def on_cancel(self):    self.destroy()


class HRTFProjectManager(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup
        self.title("Mesh2SOFA")
        self.geometry("800x800")
        
        self.app_settings_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_settings.json")
        self.app_settings = {
            "mesh2hrtf_path": "C:/Mesh2HRTF/mesh2hrtf",
            "blender_path": "",
            "grading_bin_path": os.getcwd()
        }
        self.load_app_settings()

        # Data State
        self.project_data = {
            "base_path": "",
            "project_resolution": "standard", # Default: standard or lowres
            "scripts_path": os.path.dirname(os.path.abspath(__file__)),       
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
        self.update_ui_from_data()
        
        # Start checking the log queue
        self.check_log_queue()

    def create_widgets(self):
        # --- TITLE AREA ---
        self.frame_top = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_top.grid(row=0, column=0, pady=20, padx=20, sticky="ew")
        
        self.lbl_title = ctk.CTkLabel(self.frame_top, text="No Project Loaded", font=("Roboto Medium", 24))
        self.lbl_title.pack(side="left")
        self.lbl_title.bind("<Button-1>", self.open_project_folder)
        self.lbl_title.bind("<Enter>", lambda e: self.lbl_title.configure(cursor="hand2", text_color="#7ecfad"))
        self.lbl_title.bind("<Leave>", lambda e: self.lbl_title.configure(cursor="", text_color="white"))

        self.frame_controls = ctk.CTkFrame(self.frame_top, fg_color="transparent")
        self.frame_controls.pack(side="right")
        
        self.btn_refresh = ctk.CTkButton(self.frame_controls, text="Refresh", width=80, command=self.manual_refresh)
        self.btn_refresh.pack(side="right", padx=5)

        self.btn_load = ctk.CTkButton(self.frame_controls, text="Open", width=80, fg_color="#444", command=self.load_project_json)
        self.btn_load.pack(side="right", padx=5)

        self.btn_new = ctk.CTkButton(self.frame_controls, text="New", width=80, fg_color="#28a745", hover_color="#218838", command=self.create_new_project)
        self.btn_new.pack(side="right", padx=5)

        # --- SECTION 1: PATHS & CONFIG ---
        self.tabview_config = ctk.CTkTabview(self, height=0)
        self.tabview_config.grid(row=1, column=0, padx=20, pady=(10, 0), sticky="ew")
        
        self.tabview_config.add("  App Settings  ")
        self.tabview_config.add("  Project Settings  ")
        self.tabview_config.set("  App Settings  ")

        tab_app = self.tabview_config.tab("  App Settings  ")
        tab_app.grid_columnconfigure(1, weight=1)
        
        tab_proj = self.tabview_config.tab("  Project Settings  ")
        tab_proj.grid_columnconfigure(1, weight=1)

        # APP TAB PATH BUTTONS
        self.add_config_row(0, "Mesh2HRTF Root:", "entry_m2h", "C:/Mesh2HRTF", browse_cmd=self.browse_m2h, parent_frame=tab_app)
        self.add_config_row(1, "Blender Executable:", "entry_blender", "Path to blender.exe...", browse_cmd=self.browse_blender, parent_frame=tab_app)
        self.add_config_row(2, "Mesh Grading Tool Bin:", "entry_bins", os.getcwd(), browse_cmd=self.browse_bins, parent_frame=tab_app)

        # PROJECT TAB PATH BUTTONS
        # entry_base must exist for all base-path readers (.get()/.insert()), but the
        # folder is no longer user-selectable — it is derived from project.json location.
        self.entry_base = ctk.CTkEntry(tab_proj, placeholder_text="Select project root...")
        # (intentionally not gridded — kept hidden as the base-path store)

        # Resolution mode toggle (replaces the old Project Folder row)
        _MODE_LABELS = {
            "standard": "Standard (18 kHz Max)",
            "lowres":   "Lowres (16 kHz Max)",
        }
        self._mode_label_to_value = {v: k for k, v in _MODE_LABELS.items()}
        self._mode_value_to_label = _MODE_LABELS

        lbl_mode = ctk.CTkLabel(tab_proj, text="Project Mode:")
        lbl_mode.grid(row=0, column=0, padx=10, pady=5, sticky="w")

        self.seg_mode = ctk.CTkSegmentedButton(
            tab_proj,
            values=list(_MODE_LABELS.values()),
            command=self.on_mode_changed,
        )
        self.seg_mode.grid(row=0, column=1, padx=10, pady=5, sticky="ew")
        self.seg_mode.set(_MODE_LABELS["standard"])  # default; updated in update_ui_from_data

        # Tooltips are attached after the widget is rendered so _buttons_dict is populated.
        self.after(100, self._attach_mode_tooltips)

        lbl_grid = ctk.CTkLabel(tab_proj, text="Evaluation Grid(s):")
        lbl_grid.grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.entry_grid = ctk.CTkEntry(tab_proj, placeholder_text="Set Mesh2HRTF Path first...", state="disabled")
        self.entry_grid.grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        self.btn_select_grids = ctk.CTkButton(tab_proj, text="Select Grids", width=80, command=self.open_grid_dialog)
        self.btn_select_grids.grid(row=1, column=2, padx=10, pady=5)

        self.add_config_row(2, "Raw Mesh:", "entry_raw", "Browse to import a raw mesh (.obj/.ply/.stl)...", browse_cmd=self.browse_raw, parent_frame=tab_proj)
        # Read-only: the only way to set a raw mesh is via Browse → import.
        # Freetyping does nothing (the field is only ever read, never triggers
        # import), and now disabling the field makes that explicit.
        self.entry_raw.configure(state="disabled")

        # --- SECTION 2: WORKFLOW ACTIONS ---
        self.frame_actions = ctk.CTkFrame(self)
        self.frame_actions.grid(row=2, column=0, padx=20, pady=(5, 20), sticky="nsew")
        
        self.lbl_workflow = ctk.CTkLabel(self.frame_actions, text="Workflow Steps", font=("Roboto Medium", 18))
        self.lbl_workflow.grid(row=0, column=0, padx=10, pady=5, sticky="w")

        # WORKFLOW BUTTONS
        self.btn_align = ctk.CTkButton(self.frame_actions, text="1. Align Mesh", command=self.run_alignment)
        self.btn_align.grid(row=1, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.btn_inspect = ctk.CTkButton(self.frame_actions, text="2. Inspect & Fix Mesh", command=self.run_inspect_fix)
        self.btn_inspect.grid(row=2, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.btn_process = ctk.CTkButton(self.frame_actions, text="3. Process & Grade Mesh", command=self.run_processing)
        self.btn_process.grid(row=3, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.btn_blender = ctk.CTkButton(self.frame_actions, text="4. Open Graded Meshes in Blender (Setup Scene)", command=self.run_blender_setup)
        self.btn_blender.grid(row=4, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.btn_export = ctk.CTkButton(self.frame_actions, text="5. Export Project Folders (Manual/Script)", state="disabled", fg_color="gray30", text_color="gray")
        self.btn_export.grid(row=5, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.btn_numcalc = ctk.CTkButton(self.frame_actions, text="6. Run NumCalc Simulation", command=self.run_numcalc)
        self.btn_numcalc.grid(row=6, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.btn_sofa = ctk.CTkButton(self.frame_actions, text="7. Generate Mastered SOFA Files", command=self.run_sofa_generation)
        self.btn_sofa.grid(row=7, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        # EXTRAS SECTION
        self.lbl_extras_spacer = ctk.CTkLabel(self.frame_actions, text="EXTRAS", font=("Roboto Medium", 12))
        self.lbl_extras_spacer.grid(row=8, column=0, columnspan=2, pady=(10, 0))

        self.frame_extras = ctk.CTkFrame(self.frame_actions, fg_color="transparent")
        self.frame_extras.grid(row=9, column=0, columnspan=2, padx=10, pady=5, sticky="ew")
        self.frame_extras.grid_columnconfigure(0, weight=1)
        self.frame_extras.grid_columnconfigure(1, weight=1)

        self.btn_extras = ctk.CTkButton(self.frame_extras, text="Generate DFHRTF Files", command=self.open_tilt_dialog)
        self.btn_extras.grid(row=0, column=0, padx=(0, 5), sticky="ew")

        self.btn_vtk = ctk.CTkButton(self.frame_extras, text="Generate Paraview VTK Files", command=self.open_vtk_dialog)
        self.btn_vtk.grid(row=0, column=1, padx=(5, 0), sticky="ew")

        # LOGGING AREA
        self.textbox = ctk.CTkTextbox(self.frame_actions, height=150)
        self.textbox.grid(row=10, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")

        self.btn_stop = ctk.CTkButton(self.frame_actions, text="STOP PROCESS", fg_color=COLOR_ERROR, state="disabled", command=self.kill_process)
        self.btn_stop.grid(row=11, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.frame_actions.grid_rowconfigure(10, weight=1)
        self.frame_actions.grid_columnconfigure(0, weight=1)

    def add_config_row(self, row, label_text, attr_name, placeholder, browse_cmd=None, parent_frame=None):
        parent = parent_frame if parent_frame else self.frame_config
        lbl = ctk.CTkLabel(parent, text=label_text)
        lbl.grid(row=row, column=0, padx=10, pady=5, sticky="w")
        entry = ctk.CTkEntry(parent, placeholder_text=placeholder)
        entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")
        setattr(self, attr_name, entry)
        if browse_cmd:
            btn = ctk.CTkButton(parent, text="Browse", width=80, command=browse_cmd)
            btn.grid(row=row, column=2, padx=10, pady=5)

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
                    text=True, bufsize=1, universal_newlines=True, shell=shell, creationflags=CREATE_NO_WINDOW
                )
                for line in iter(self.current_process.stdout.readline, ''):
                    self.log_queue.put(line.strip())
                self.current_process.stdout.close()
                rc = self.current_process.wait()
                if rc == 0: self.log_queue.put("[+] Success.")
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
                        text=True, bufsize=1, universal_newlines=True, creationflags=CREATE_NO_WINDOW
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
                    if getattr(self, '_pending_mesh_check', False):
                        self._pending_mesh_check = False
                        self.after(200, self._check_mesh_quality_result)
                    if getattr(self, '_pending_inspect_check', False):
                        self._pending_inspect_check = False
                        self.after(200, self._check_aligned_quality_result)
                    if getattr(self, '_pending_import_check', False):
                        self._pending_import_check = False
                        self.after(200, self._check_import_result)
                    if getattr(self, '_pending_cutcap_revalidate', False):
                        self._pending_cutcap_revalidate = False
                        self.after(200, self._after_cutcap_revalidated)
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
            subprocess.run("taskkill /F /IM NumCalc.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
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
    def update_settings(self, new_res):
        self.project_data["project_resolution"] = new_res
        self.log(f"Project Resolution set to: {new_res.upper()}")
        self.save_project_json(silent=True)

    def on_mode_changed(self, label):
        """Called when the user clicks a segment on the Project Mode toggle."""
        value = self._mode_label_to_value.get(label, "standard")
        self.update_settings(value)

    def _attach_mode_tooltips(self):
        """Bind tooltips to each internal segment button after they are rendered."""
        _tips = {
            "Standard (18 kHz Max)": (
                "Standard mode: outputs up to 18 kHz.\n"
                "Higher mesh resolution — requires more RAM and longer NumCalc simulation time."
            ),
            "Lowres (16 kHz Max)": (
                "Lowres mode: outputs up to 16 kHz.\n"
                "Lower mesh resolution — uses less RAM and runs faster."
            ),
        }
        try:
            for label, tip_text in _tips.items():
                btn = self.seg_mode._buttons_dict.get(label)
                if btn:
                    Tooltip(btn, tip_text)
        except Exception:
            pass  # Gracefully ignore if internal CTk API changes

    def open_project_folder(self, event=None):
        """Open the current project folder in the OS file browser (cross-platform)."""
        path = self.entry_base.get()
        if not path or not os.path.isdir(path):
            self.log("[!] No project folder loaded.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.log(f"[!] Could not open folder: {e}")

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
        self._set_entry_raw()
        
        self.save_project_json(silent=True)
        self.update_workflow_state()

    def get_project_name(self):
        base_path = self.entry_base.get()
        if base_path and os.path.isdir(base_path):
            return os.path.basename(os.path.normpath(base_path))
        return "Project"

    def get_mesh_dir(self):
        return ProjectStore(self.entry_base.get()).mesh_dir

    def _set_entry_raw(self, value=""):
        """Write to the read-only Raw Mesh field (briefly enable → set → disable)."""
        self.entry_raw.configure(state="normal")
        self.entry_raw.delete(0, "end")
        if value:
            self.entry_raw.insert(0, value)
        self.entry_raw.configure(state="disabled")

    def _rel_to_base(self, abs_path, base):
        """Convert abs_path to a path relative to base, if it's under base."""
        try:
            if abs_path and os.path.isabs(abs_path) and base:
                rel = os.path.relpath(abs_path, base)
                if not rel.startswith(".."):   # only if actually inside base
                    return rel
        except ValueError:
            pass  # different drive on Windows — fall through
        return abs_path

    def _abs_from_base(self, stored, base):
        """Resolve a stored (possibly relative) raw_scan path back to absolute."""
        if stored and not os.path.isabs(stored) and base:
            return os.path.normpath(os.path.join(base, stored))
        return stored

    # --- SMART MESH2HRTF LOGIC ---
    def get_valid_m2h_input_path(self):
        raw_path = self.entry_m2h.get()
        if not raw_path or not os.path.exists(raw_path): return None
        
        candidate_1 = os.path.join(raw_path, "mesh2hrtf", "Mesh2Input")
        if os.path.exists(candidate_1): return candidate_1
        
        candidate_2 = os.path.join(raw_path, "Mesh2Input")
        if os.path.exists(candidate_2): return candidate_2
        
        return None

    def get_available_grids(self):
        m2h_input = self.get_valid_m2h_input_path()
        if not m2h_input:
            self.log("Error: Could not locate 'Mesh2Input' folder in Mesh2HRTF path.")
            return []

        grid_path = os.path.join(m2h_input, "EvaluationGrids", "Data")

        if os.path.exists(grid_path):
            folders = [f for f in os.listdir(grid_path) if os.path.isdir(os.path.join(grid_path, f))]
            return folders
        else:
            self.log(f"Error: Could not find grids at {grid_path}")
            return []

    def open_grid_dialog(self):
        available_grids = self.get_available_grids()
        if not available_grids:
            return
            
        def on_grids_selected(selected_grids_str):
            self.entry_grid.configure(state="normal")
            self.entry_grid.delete(0, "end")
            self.entry_grid.insert(0, selected_grids_str)
            self.entry_grid.configure(state="disabled")
            self.save_project_json()

        GridSelectionDialog(self, available_grids, self.entry_grid.get(), on_grids_selected)

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
    # --- Hopefully now works on both Mac & Windows ---
    def browse_bins(self):
        # 1. Determine expected binary name for the title
        bin_name = "hrtf_mesh_grading.exe" if sys.platform == "win32" else "hrtf_mesh_grading"
        
        # 2. Set filetypes
        if sys.platform == "win32":
            filetypes = [("Executables", "*.exe"), ("All Files", "*.*")]
        else:
            filetypes = [("All Files", "*.*")]

        # 3. Ask for the FILE, not the directory
        kwargs = {"title": f"Select {bin_name}", "filetypes": filetypes}
        current_path = self.entry_bins.get()
        if current_path and os.path.exists(os.path.dirname(current_path)):
            kwargs["initialdir"] = os.path.dirname(current_path)
            
        path = filedialog.askopenfilename(**kwargs)

        if path:
            self.entry_bins.delete(0, "end")
            self.entry_bins.insert(0, path)
            self.save_project_json()
    
    def _browse_dir(self, entry_widget):
        kwargs = {}
        current_path = entry_widget.get()
        if current_path and os.path.isdir(current_path):
            kwargs["initialdir"] = current_path
        elif current_path and os.path.exists(os.path.dirname(current_path)):
            kwargs["initialdir"] = os.path.dirname(current_path)
            
        path = filedialog.askdirectory(**kwargs)
        if path:
            entry_widget.delete(0, "end")
            entry_widget.insert(0, path)
            self.update_workflow_state()
    
    # --- Hopefully now works on both Mac & Windows ---
    def browse_blender(self):
        # 1. Define filetypes based on OS
        if sys.platform == "win32":
            filetypes = [("Executables", "*.exe"), ("All Files", "*.*")]
        else:
            # On macOS/Linux, allow all files so we can select binaries with no extension
            filetypes = [("All Files", "*.*")]

        kwargs = {"title": "Select Blender Executable", "filetypes": filetypes}
        current_path = self.entry_blender.get()
        if current_path and os.path.exists(os.path.dirname(current_path)):
            kwargs["initialdir"] = os.path.dirname(current_path)

        path = filedialog.askopenfilename(**kwargs)
        
        if path:
            # 2. Smart handling for macOS .app bundles
            # If the user selected 'Blender.app', we point to the internal binary
            if sys.platform == "darwin" and path.endswith(".app"):
                potential_binary = os.path.join(path, "Contents", "MacOS", "Blender")
                if os.path.exists(potential_binary):
                    path = potential_binary
            
            self.entry_blender.delete(0, "end")
            self.entry_blender.insert(0, path)
            self.save_project_json()

    # --- Formal mesh import: Browse → Move/Copy → inspect+dissolve+repair ---
    def browse_raw(self):
        # 1. Check project folder
        if not self.entry_base.get():
            return messagebox.showerror("Error", "Please define a Project Folder first.")

        # 2. Hard-require Blender (sliver dissolve only works via Blender).
        blender_exe = self.entry_blender.get()
        if not blender_exe or not os.path.exists(blender_exe):
            return messagebox.showerror(
                "Blender Required",
                "A valid Blender path must be set in App Settings before importing a mesh.\n\n"
                "Blender is required to remove tiny sliver triangles that pymeshlab\n"
                "cannot fix. Please configure 'Blender Executable' above and try again.",
            )

        # 3. Open file dialog
        kwargs = {"filetypes": [("3D Mesh", "*.obj *.ply *.stl")]}
        current_path = self.entry_raw.get()
        base_path = self.entry_base.get()
        if current_path and os.path.exists(os.path.dirname(current_path)):
            kwargs["initialdir"] = os.path.dirname(current_path)
        elif base_path and os.path.isdir(base_path):
            kwargs["initialdir"] = base_path

        src_path = filedialog.askopenfilename(**kwargs)
        if not src_path:
            return

        # 4. Determine destination in Meshes/
        project_mesh_dir = self.get_mesh_dir()
        if not os.path.exists(project_mesh_dir):
            os.makedirs(project_mesh_dir, exist_ok=True)

        src_dir  = os.path.dirname(os.path.normpath(src_path))
        dest_dir = os.path.normpath(project_mesh_dir)
        filename = os.path.basename(src_path)
        dest_path = os.path.join(dest_dir, filename)

        # 4b. Warn before discarding prior pipeline work for this project.
        existing_artifacts = self._store(project_mesh_dir).list_mesh_artifacts()
        if existing_artifacts:
            artifact_list = "\n  ".join(existing_artifacts)
            if not messagebox.askyesno(
                "Replace Current Mesh?",
                "Importing a new mesh will DELETE the existing aligned / graded / "
                "inspection files in the Meshes folder:\n\n"
                f"  {artifact_list}\n\n"
                "Alignment and Inspect & Fix (cut & cap) must be redone for the "
                "new mesh.\n\nContinue?",
            ):
                return

        # 5. After the file lands in Meshes/, run the import worker.
        def finalize_selection(final_path):
            self._set_entry_raw(final_path)
            # New base mesh — prior alignment / inspection / grading no longer
            # apply.  Clear them so Align + Inspect & Fix must be redone.
            removed = self._store(project_mesh_dir).reset_mesh_artifacts()
            if removed:
                self.log(
                    "--> Cleared prior mesh artifacts: "
                    + ", ".join(removed)
                )
            self.save_project_json(silent=True)
            self.update_workflow_state()

            # Guard: don't queue a second process if one is already running.
            if self.is_running:
                self.log("[!] A process is already running — import queued next time.")
                return

            scripts_dir = os.path.dirname(os.path.abspath(__file__))
            inspector = os.path.join(scripts_dir, "mesh_inspector.py")
            self.log(f"--> Importing mesh: {os.path.basename(final_path)} "
                     "(inspect → Blender dissolve → repair)...")
            self._pending_import_check = True
            cmd = [sys.executable, "-u", inspector, "import_mesh", final_path, blender_exe]
            self.run_external_command(cmd)

        # 6. Move/Copy or use in place
        if src_dir == dest_dir:
            finalize_selection(src_path)
        else:
            def on_dialog_result(action):
                if not action:
                    return
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

            MoveCopyDialog(self, filename, on_dialog_result)

    # --- STATE MANAGEMENT ---
    def load_app_settings(self):
        if os.path.exists(self.app_settings_file):
            try:
                with open(self.app_settings_file, 'r') as f:
                    data = json.load(f)
                    self.app_settings.update(data)
            except Exception:
                pass

    def save_app_settings(self):
        self.app_settings.update({
            "mesh2hrtf_path": self.entry_m2h.get(),
            "blender_path": self.entry_blender.get(),
            "grading_bin_path": self.entry_bins.get()
        })
        try:
            with open(self.app_settings_file, 'w') as f:
                json.dump(self.app_settings, f, indent=4)
        except Exception:
            pass

    def update_ui_from_data(self):
        d = self.project_data
        app = self.app_settings
        self.entry_base.delete(0, "end"); self.entry_base.insert(0, d.get("base_path", ""))
        self.entry_m2h.delete(0, "end"); self.entry_m2h.insert(0, app.get("mesh2hrtf_path", ""))
        self.entry_blender.delete(0, "end"); self.entry_blender.insert(0, app.get("blender_path", ""))
        self.entry_bins.delete(0, "end"); self.entry_bins.insert(0, app.get("grading_bin_path", ""))
        self._set_entry_raw(self._abs_from_base(d.get("raw_scan", ""), d.get("base_path", "")))
        self.entry_grid.configure(state="normal")
        self.entry_grid.delete(0, "end")
        if d.get("eval_grid"): self.entry_grid.insert(0, d.get("eval_grid"))
        self.entry_grid.configure(state="disabled")
        # Sync resolution toggle
        res = d.get("project_resolution", "standard")
        self.seg_mode.set(self._mode_value_to_label.get(res, "Standard (18 kHz Max)"))
        self.update_workflow_state()

    def load_project_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON Project", "*.json")])
        if not path: return
        try:
            base = os.path.dirname(os.path.abspath(path))
            data = ProjectStore(base).load_project()
            self.project_data.update(data)
            # Cleanup old keys if they exist in a legacy project.json
            for k in ["mesh2hrtf_path", "blender_path", "grading_bin_path"]:
                if k in self.project_data:
                    del self.project_data[k]

            if "project_resolution" not in self.project_data:
                self.project_data["project_resolution"] = "standard"

            # base_path is authoritative from the file's own location, so the
            # project folder can be moved/renamed without breaking anything.
            self.project_data["base_path"] = base

            self.update_ui_from_data()
            self.log(f"Loaded project: {path} ({self.project_data['project_resolution']})")
            # Self-heal: write the corrected base_path back to disk immediately.
            self.save_project_json(silent=True)
        except Exception as e:
            self.log(f"Error loading project: {e}")

    def save_project_json(self, silent=False):
        self.save_app_settings()
        
        base = self.entry_base.get()
        self.project_data.update({
            "base_path": base,
            "scripts_path": os.path.dirname(os.path.abspath(__file__)), # AUTO-DETECTED
            "eval_grid": self.entry_grid.get(),
            "raw_scan": self._rel_to_base(self.entry_raw.get(), base)
        })
        if not os.path.exists(self.project_data["base_path"]):
            return

        store = ProjectStore(self.project_data["base_path"])
        try:
            store.write_project(self.project_data)
            if not silent: self.log(f"Settings saved to {store.project_json_path}")
        except Exception as e:
            if not silent: self.log(f"Error saving: {e}")

    def manual_refresh(self):
        self.save_project_json(silent=True)
        self.update_workflow_state()
        self.log("Refreshed & saved.")

    # --- MESH QUALITY HELPERS ---

    def _watch_viewer(self, proc, on_close):
        """Poll a detached viewer subprocess every second; call on_close() when
        it exits so the GUI picks up any mesh/check-file changes it made.
        Uses `after()` — no threads, no is_running block."""
        if proc.poll() is None:
            self.after(1000, lambda: self._watch_viewer(proc, on_close))
        else:
            on_close()

    def _store(self, mesh_path=None):
        """ProjectStore anchored at the current project; mesh_path pins the mesh
        dir for callers that already resolved it via get_mesh_dir()."""
        return ProjectStore(self.entry_base.get(), mesh_dir=mesh_path)

    def _mesh_check_passed(self, mesh_path):
        """Return True if mesh_check.json is absent (backward compat) or has no critical severity."""
        return self._store(mesh_path).read_check(MESH_GRADED) != CleanState.CRITICAL

    def _aligned_check_passed(self, mesh_path):
        """True if aligned_check.json is present and has no critical severity.
        Absent => not yet inspected => the Inspect & Fix step is not complete."""
        return self._store(mesh_path).read_check(MESH_ALIGNED) == CleanState.CLEAN

    @staticmethod
    def _counts_tunnel_only(counts):
        """True if genus>0 is the only thing making the mesh critical."""
        genus = counts.get("genus", 0) or 0
        vol = counts.get("volume")
        other_critical = (
            (counts.get("holes", 0) or 0) > 0
            or (counts.get("boundary_edges", 0) or 0) > 0
            or (counts.get("non_manifold_edges", 0) or 0) > 0
            or (counts.get("non_manifold_verts", 0) or 0) > 0
            or (counts.get("si_faces", 0) or 0) > 0
            or (counts.get("components", 0) or 0) > 1
            or (vol is not None and vol < 0)
        )
        return genus > 0 and not other_critical

    def _check_aligned_quality_result(self):
        """Called after an inspect_aligned / repair_aligned run, or when the
        detached tunnel viewer closes — shows AlignedMeshDialog if the aligned
        mesh still has critical issues, or restyles the Inspect button to 'done'
        if the check now passes."""
        mesh_dir = self.get_mesh_dir()
        store = self._store(mesh_dir)
        state = store.read_check(MESH_ALIGNED)
        if state == CleanState.NOT_RUN:
            return
        # Always refresh workflow buttons so the Inspect step's styling reflects
        # the current check result (covers the viewer-close "ok" path too).
        self.update_workflow_state()
        if state != CleanState.CRITICAL:
            self.log("[OK] Aligned mesh passed inspection — you may proceed to grading.")
            return
        info = store.read_check_data(MESH_ALIGNED).get("aligned", {})
        counts = info.get("counts", {})
        tunnel_only = self._counts_tunnel_only(counts)
        summary = "\n".join(f"  {k}: {v}" for k, v in counts.items() if v not in (0, None))
        AlignedMeshDialog(self, mesh_dir, summary, tunnel_only, self._on_aligned_quality_action)

    def _on_aligned_quality_action(self, action, mesh_dir):
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        inspector = os.path.join(scripts_dir, "mesh_inspector.py")

        if action == "repair":
            self.log("--> Attempting mesh repair on aligned mesh (pymeshfix)...")
            self._pending_inspect_check = True
            cmd = [sys.executable, "-u", inspector, "repair_aligned", mesh_dir]
            self.run_external_command(cmd)

        elif action == "visualize":
            viewer = os.path.join(scripts_dir, "mesh_problem_viewer.py")
            ply = os.path.join(mesh_dir, "aligned_head.ply")
            if os.path.exists(ply):
                self.log("--> Opening tunnel viewer. Choose the cut loop (Tab/Space), "
                         "then press C to apply Cut & Cap — the viewer will close and "
                         "re-validate automatically.")
                proc = subprocess.Popen([sys.executable, viewer, ply],
                                        creationflags=CREATE_NO_WINDOW)
                self._watch_viewer(proc, lambda: self._after_tunnel_viewer(mesh_dir))

    def _after_tunnel_viewer(self, mesh_dir):
        """Called when the tunnel viewer process exits. If the viewer applied a
        cut & cap it writes cutcap_report.json; if that file is present we run
        repair_aligned to re-inspect and auto-repair the newly-saved mesh, then
        show a good/bad popup. If the file is absent the user closed the viewer
        without cutting, so we fall through to the normal quality-result check."""
        sentinel = os.path.join(mesh_dir, "cutcap_report.json")
        if not os.path.exists(sentinel):
            # User closed without cutting — existing check flow.
            self._check_aligned_quality_result()
            return
        try:
            os.remove(sentinel)
        except Exception:
            pass
        self.log("--> Cut & cap applied. Re-inspecting and auto-repairing aligned mesh…")
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        inspector = os.path.join(scripts_dir, "mesh_inspector.py")
        self._pending_cutcap_revalidate = True
        self._cutcap_mesh_dir = mesh_dir
        cmd = [sys.executable, "-u", inspector, "repair_aligned", mesh_dir]
        self.run_external_command(cmd)

    def _after_cutcap_revalidated(self):
        """Called after the post-cut&cap repair_aligned finishes. Reads
        aligned_check.json and shows a good/bad popup, then updates the workflow."""
        mesh_dir = getattr(self, "_cutcap_mesh_dir", None)
        self.update_workflow_state()
        if not mesh_dir:
            return
        state = self._store(mesh_dir).read_check(MESH_ALIGNED)
        if state == CleanState.CRITICAL:
            info = self._store(mesh_dir).read_check_data(MESH_ALIGNED).get("aligned", {})
            counts = info.get("counts", {})
            issues_txt = "\n".join(
                f"  {k}: {v}" for k, v in counts.items()
                if v not in (0, None, False)
            )
            messagebox.showwarning(
                "Re-validation — Issues Remain",
                "The mesh was saved after Cut & Cap but still has critical issues:\n\n"
                f"{issues_txt}\n\n"
                "Run 'Inspect & Fix Mesh' again to view and address them.",
            )
        else:
            messagebox.showinfo(
                "All Clear",
                "Tunnel removed and mesh re-validated — no critical issues remain.\n\n"
                "You may proceed to Step 3 (Process & Grade).",
            )

    def _check_import_result(self):
        """Called after an import_mesh run — reads import_report.json and shows
        a summary popup. Tunnels (genus>0) show a warning; everything else that
        was fixable is fixed before this is called."""
        mesh_dir = self.get_mesh_dir()
        report_path = os.path.join(mesh_dir, "import_report.json")
        self.update_workflow_state()

        if not os.path.exists(report_path):
            self.log("[!] import_report.json not found — import may have failed. "
                     "Check the log above for details.")
            return

        try:
            with open(report_path) as f:
                data = json.load(f)
        except Exception as e:
            self.log(f"[!] Could not read import report: {e}")
            return

        filename = data.get("filename", "mesh")
        after    = data.get("after", {})
        critical = after.get("critical", [])

        # Separate tunnel (genus) warnings from other residual criticals.
        tunnel_issues     = [c for c in critical
                             if "tunnel" in c.get("issue", "").lower()
                             or "genus" in c.get("issue", "").lower()]
        non_tunnel_issues = [c for c in critical if c not in tunnel_issues]

        base_msg = f"Mesh imported as '{filename}'."

        if non_tunnel_issues:
            # Geometry criticals survived auto-repair — unusual, surface them.
            issues_txt = "\n".join(f"  - {c['issue']}" for c in non_tunnel_issues)
            messagebox.showwarning(
                "Import Complete — Issues Remain",
                f"{base_msg}\n\nResidual critical issues after auto-repair:\n{issues_txt}\n\n"
                "Run Step 2 (Inspect & Fix Mesh) after alignment to address these.",
            )
        elif tunnel_issues:
            # Tunnels detected but not removable before alignment.
            messagebox.showwarning(
                "Import Complete — Tunnel Detected",
                f"{base_msg}\n\n"
                "A topological tunnel (genus>0) was detected — this is a scanning "
                "artifact that cannot be removed on the raw mesh.\n\n"
                "After running Step 1 (Align), use Step 2 (Inspect & Fix Mesh) to "
                "locate and remove the tunnel with the interactive cut & cap tool.",
            )
        else:
            messagebox.showinfo("Import Complete", base_msg)

    def _check_mesh_quality_result(self):
        """Called after a process_and_grade run — shows MeshQualityDialog if criticals found."""
        mesh_path = self.get_mesh_dir()
        if self._store(mesh_path).read_check(MESH_GRADED) == CleanState.CRITICAL:
            MeshQualityDialog(self, mesh_path, self._on_mesh_quality_action)

    def _on_mesh_quality_action(self, action, mesh_path):
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        inspector = os.path.join(scripts_dir, "mesh_inspector.py")

        if action == "repair":
            self.log("--> Attempting mesh repair on graded files...")
            self._pending_mesh_check = True
            cmd = [sys.executable, "-u", inspector, "repair_graded", mesh_path]
            self.run_external_command(cmd)

        elif action == "visualize":
            viewer = os.path.join(scripts_dir, "mesh_problem_viewer.py")
            try:
                data = self._store(mesh_path).read_check_data(MESH_GRADED) or {}
                for side, side_data in data.items():
                    if side_data.get("severity") == "critical":
                        ply = os.path.join(mesh_path, f"{side.capitalize()}_Graded.ply")
                        if os.path.exists(ply):
                            proc = subprocess.Popen([sys.executable, viewer, ply],
                                                    creationflags=CREATE_NO_WINDOW)
                            def _on_close_graded():
                                self.update_workflow_state()
                                self._check_mesh_quality_result()
                            self._watch_viewer(proc, _on_close_graded)
            except Exception as e:
                self.log(f"[!] Could not launch viewer: {e}")

    def update_workflow_state(self):
        base_path = self.entry_base.get()
        proj_name = self.get_project_name()
        
        if base_path and os.path.isdir(base_path):
            self.lbl_title.configure(text=proj_name)
            self.title(f"Mesh2SOFA ({base_path})")
        else:
            self.lbl_title.configure(text="No Project Loaded")
            self.title("Mesh2SOFA")

        mesh_path = self.get_mesh_dir()
        
        step_align_done = os.path.exists(os.path.join(mesh_path, "aligned_head.ply"))
        # Inspect & Fix is OPTIONAL: it does not gate grading (grading just uses
        # whatever aligned_head.ply currently is). step_inspect_done only drives
        # the Inspect button's own "done" styling.
        step_inspect_done = step_align_done and self._aligned_check_passed(mesh_path)
        step_grade_done = (step_align_done
                           and os.path.exists(os.path.join(mesh_path, "Left_Graded.ply"))
                           and self._mesh_check_passed(mesh_path))

        blend_file = os.path.join(base_path, f"{proj_name}.blend")
        step_blender_done = step_grade_done and os.path.exists(blend_file)

        step_export_done = step_blender_done and os.path.exists(os.path.join(base_path, "Exports", "Left_Project"))

        nc_log_left = os.path.join(base_path, "Exports", "Left_Project", "NumCalc", "source_1", "NC.out")
        step_numcalc_done = step_export_done and os.path.exists(nc_log_left)

        output_dir = os.path.join(base_path, "Output")
        step_sofa_done = step_numcalc_done and os.path.exists(output_dir) and any(f.endswith(".sofa") for f in os.listdir(output_dir))

        # Linear progress for the MAIN path (Inspect is off this path).
        progress_index = 0
        if step_align_done: progress_index = 1
        if step_grade_done: progress_index = 2
        if step_blender_done: progress_index = 3
        if step_export_done: progress_index = 4
        if step_numcalc_done: progress_index = 5
        if step_sofa_done: progress_index = 6

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

        # Inspect & Fix: optional side-step. Available once align is done; shows
        # "done" styling once the aligned mesh passes inspection, but it never
        # blocks Process & Grade.
        if not step_align_done:
            self.btn_inspect.configure(state="disabled", fg_color=COLOR_LOCKED, text_color="grey")
        elif step_inspect_done:
            self.btn_inspect.configure(state="normal", fg_color=COLOR_DONE, hover_color=HOVER_DONE, text_color="white")
        else:
            self.btn_inspect.configure(state="normal", fg_color=COLOR_ACTIVE, hover_color=HOVER_ACTIVE, text_color="black")

        if step_sofa_done:
            self.btn_extras.configure(state="normal", fg_color=COLOR_ACTIVE, text_color="black")
            self.btn_vtk.configure(state="normal", fg_color=COLOR_ACTIVE, text_color="black")
        else:
            self.btn_extras.configure(state="disabled", fg_color=COLOR_LOCKED)
            self.btn_vtk.configure(state="disabled", fg_color=COLOR_LOCKED)

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

    def run_inspect_fix(self):
        """Step 2: inspect the aligned mesh; on criticals show the Inspect & Fix
        dialog (pymeshfix repair + interactive tunnel cut & cap)."""
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        inspector = os.path.join(scripts_dir, "mesh_inspector.py")
        mesh_dir = self.get_mesh_dir()
        aligned = os.path.join(mesh_dir, "aligned_head.ply")
        if not os.path.exists(aligned):
            return self.log("Error: Run '1. Align Mesh' first (aligned_head.ply not found).")
        if not os.path.exists(inspector):
            return self.log(f"Error: {inspector} missing")

        self.log("--> Inspecting aligned mesh...")
        self._pending_inspect_check = True
        cmd = [sys.executable, "-u", inspector, "inspect_aligned", mesh_dir]
        self.run_external_command(cmd)

    def _aligned_inspection_warning(self, mesh_dir):
        """Return a warning string if grading should warn the user (mesh never
        inspected, or inspection found unresolved criticals), else None.
        Inspect & Fix is optional, so this only warns -- it never blocks.

        Note: a corrupt aligned_check.json folds into NOT_RUN (the "not inspected"
        warning), which is strictly safer than the old silent pass and matches the
        CleanState design."""
        store = self._store(mesh_dir)
        state = store.read_check(MESH_ALIGNED)
        if state == CleanState.NOT_RUN:
            return ("The aligned mesh has not been inspected (Step 2).\n"
                    "Grading an un-inspected mesh may fail later if it has "
                    "self-intersections or topological tunnels.")
        if state == CleanState.CRITICAL:
            counts = store.read_check_data(MESH_ALIGNED).get("aligned", {}).get("counts", {})
            summary = ", ".join(f"{k}={v}" for k, v in counts.items()
                                if v not in (0, None))
            return ("Inspection found UNRESOLVED critical issues on the aligned "
                    f"mesh:\n  {summary}\nGrading anyway may produce a bad result.")
        return None

    # --- Updated to work with new browse_blender and brows_bins functions ---
    def run_processing(self):
        mesh_dir = self.get_mesh_dir()
        aligned_mesh = os.path.join(mesh_dir, "aligned_head.ply")
        if not os.path.exists(aligned_mesh):
            return self.log("Error: Run '1. Align Mesh' first (aligned_head.ply not found).")

        warn = self._aligned_inspection_warning(mesh_dir)
        if warn:
            from tkinter import messagebox
            if not messagebox.askyesno("Proceed to grading?",
                                       f"{warn}\n\nProceed with grading anyway?",
                                       icon="warning", parent=self):
                self.log("--> Grading cancelled (run Inspect & Fix first, or proceed when prompted).")
                return
        self._launch_processing(mesh_dir, aligned_mesh)

    def _launch_processing(self, mesh_dir, aligned_mesh):
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(scripts_dir, "process_and_grade.py")
        # --- Hopefully now works on both Mac & Windows ---
        grading_exe = self.entry_bins.get()
        # Fallback: If the user manually entered a folder path, try to find the binary
        if os.path.isdir(grading_exe):
            binary_name = "hrtf_mesh_grading.exe" if sys.platform == "win32" else "hrtf_mesh_grading"
            grading_exe = os.path.join(grading_exe, binary_name)

        if not os.path.exists(grading_exe): return self.log(f"Error: Grading binary missing at {grading_exe}")

        self.log("--> Starting Processing & Grading...")
        self._pending_mesh_check = True
        cmd = [sys.executable, "-u", script_path, aligned_mesh, grading_exe]
        self.run_external_command(cmd)

    def run_blender_setup(self):
        blender_exe = self.entry_blender.get()
        if not blender_exe or not os.path.exists(blender_exe):
            return self.log("[ERROR] Blender path is invalid. Please configure it above.")

        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(scripts_dir, "blender_scripts", "setup_blender_scene.py")
        ref_blend   = os.path.join(scripts_dir, "3d_Reference.blend")
        base_folder = os.path.normpath(self.entry_base.get())
        proj_name   = self.get_project_name()
        proj_blend  = os.path.join(base_folder, f"{proj_name}.blend")

        if os.path.exists(proj_blend):
            def on_choice(action):
                if action == "overwrite":
                    self._launch_blender(blender_exe, proj_blend, ref_blend, script_path, fresh=True)
                elif action == "open":
                    self._launch_blender(blender_exe, proj_blend, ref_blend, script_path, fresh=False)
            BlenderOpenDialog(self, proj_name, on_choice)
        else:
            self._launch_blender(blender_exe, proj_blend, ref_blend, script_path, fresh=True)

    def _launch_blender(self, blender_exe, proj_blend, ref_blend, script_path, fresh):
        # DETACHED_PROCESS on Windows: no console is attached, so Blender's
        # Window > Toggle System Console (AllocConsole) works on demand.
        blender_flags = subprocess.DETACHED_PROCESS if sys.platform == 'win32' else 0

        if fresh:
            try:
                if os.path.exists(proj_blend):
                    os.remove(proj_blend)
                shutil.copy(ref_blend, proj_blend)
                self.log(f"   [i] Created {os.path.basename(proj_blend)}")
            except Exception as e:
                return self.log(f"[ERROR] Copy failed: {e}")
            mesh_folder = self.get_mesh_dir()
            cmd = [blender_exe, proj_blend, "--python", script_path, "--", mesh_folder]
            self.log("--> Launching Blender (fresh import)...")
        else:
            cmd = [blender_exe, proj_blend]
            self.log("--> Opening existing Blender file...")

        subprocess.Popen(cmd, creationflags=blender_flags)
        self.log("[i] Blender launched separately.")

    def run_numcalc(self):
        numcalc_exe = self.get_binary_path("NumCalc")
        if not numcalc_exe: return self.log("[ERROR] NumCalc.exe not found.")

        res_mode = self.project_data.get("project_resolution", "standard")
        freq_label = "16 kHz" if res_mode == "lowres" else "18 kHz"

        def on_choice(action):
            if action == "test":
                self._run_numcalc_test(numcalc_exe, freq_label)
            elif action == "full":
                self._run_numcalc_full(numcalc_exe, res_mode)

        NumCalcOptionsDialog(self, freq_label, on_choice)

    def _run_numcalc_test(self, numcalc_exe, freq_label):
        base_folder = os.path.normpath(self.entry_base.get())
        left_proj   = os.path.join(base_folder, "Exports", "Left_Project")
        right_proj  = os.path.join(base_folder, "Exports", "Right_Project")
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        test_script = os.path.join(scripts_dir, "run_numcalc_test.py")

        self.log(f"--> Running Stability Test on both ears ({freq_label})...")
        cmd_left  = [sys.executable, "-u", test_script, left_proj,  numcalc_exe]
        cmd_right = [sys.executable, "-u", test_script, right_proj, numcalc_exe]
        self.run_sequential_commands([cmd_left, cmd_right])

    def _run_numcalc_full(self, numcalc_exe, res_mode):
        base_folder = os.path.normpath(self.entry_base.get())
        self.log(f"--> Starting Full Simulation ({res_mode.upper()})...")

        m2h_input_path = self.get_valid_m2h_input_path()
        if not m2h_input_path: return self.log("[ERROR] Mesh2Input not found.")

        m2h_root_inner = os.path.dirname(m2h_input_path)
        full_script = os.path.join(m2h_root_inner, "NumCalc", "manage_numcalc_script.py")
        if not os.path.exists(full_script): return self.log(f"[ERROR] Script missing: {full_script}")

        # Pass directory on Windows, exe path on other platforms
        if sys.platform == 'win32':
            numcalc_arg = os.path.dirname(numcalc_exe)
        else:
            numcalc_arg = numcalc_exe

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
        
        if not os.path.exists(output_dir):
            return self.log("[ERROR] Output folder missing. Please run Step 7 first.")
            
        # Find all 48000Hz sofa files
        sofa_files = [f for f in os.listdir(output_dir) if f.endswith("48000Hz.sofa")]
        
        if not sofa_files:
            return self.log("[ERROR] No 48000Hz SOFA files found. Please run Step 7 first.")
            
        cmds = []
        for sf_file in sofa_files:
            input_sofa = os.path.join(output_dir, sf_file)
            self.log(f"--> Queueing Extras for {sf_file} (Tilt: {tilt_value})...")
            cmd = [sys.executable, "-u", script_path, "--input", input_sofa, "--output_dir", output_dir, "--tilt", str(tilt_value)]
            cmds.append(cmd)
            
        self.run_sequential_commands(cmds)

    def open_vtk_dialog(self):
        VTKSettingsDialog(self, self.run_vtk_script).grab_set()

    def run_vtk_script(self, min_freq, max_freq):
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(scripts_dir, "generate_vtk_outputs.py")
        
        m2h_root = self.entry_m2h.get()
        base_folder = os.path.normpath(self.entry_base.get())
        output_dir = os.path.join(base_folder, "Output")
        os.makedirs(output_dir, exist_ok=True)
        
        left_proj = os.path.join(base_folder, "Exports", "Left_Project")
        right_proj = os.path.join(base_folder, "Exports", "Right_Project")
        
        self.log(f"--> Starting VTK Export ({min_freq}Hz to {max_freq}Hz)...")
        cmd = [
            sys.executable, "-u", script_path, 
            "--left", left_proj, 
            "--right", right_proj, 
            "--m2h_path", m2h_root, 
            "--output", output_dir,
            "--min_freq", str(min_freq),
            "--max_freq", str(max_freq)
        ]
        self.run_external_command(cmd)

if __name__ == "__main__":
    app = HRTFProjectManager()
    app.mainloop()
