import customtkinter as ctk
import os
import sys
import subprocess
from tkinter import filedialog, messagebox
from tkinterdnd2 import TkinterDnD, DND_FILES

# CustomTkinter Theme Settings
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class CTkDnDWrapper(ctk.CTk, TkinterDnD.DnDWrapper):
    """Wraps CustomTkinter with TkinterDnD2 to enable drag and drop."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)

class SofaMasteringApp(CTkDnDWrapper):
    def __init__(self):
        super().__init__()

        self.title("SOFA Mastering & DFHRTF Tool")
        self.geometry("600x700")
        self.resizable(False, True)

        # Application Variables
        self.input_files = []
        self.input_file_display = ctk.StringVar(value="")
        
        self.output_mode = ctk.StringVar(value="same") # "same" or "custom"
        self.custom_output_path = ctk.StringVar(value="")
        
        self.df_output_mode = ctk.StringVar(value="same") # "same" or "custom"
        self.df_custom_output_path = ctk.StringVar(value="")
        self.df_file_prefix = ctk.StringVar(value="")
        
        self.tilt_value = ctk.DoubleVar(value=-1.0)
        self.bias_value = ctk.DoubleVar(value=0.0)

        self.create_widgets()

    def create_widgets(self):
        # ==========================================
        # SECTION 1: INPUT ZONE
        # ==========================================
        self.drop_frame = ctk.CTkFrame(self, height=100, corner_radius=10, border_width=2, border_color="gray50")
        self.drop_frame.pack(pady=(20, 5), padx=20, fill="x")
        self.drop_frame.pack_propagate(False)
        
        self.drop_frame.drop_target_register(DND_FILES)
        self.drop_frame.dnd_bind('<<Drop>>', self.on_file_drop)
        self.drop_frame.bind("<Button-1>", lambda e: self.browse_input_file())

        self.lbl_drop = ctk.CTkLabel(self.drop_frame, text="Drag & Drop .sofa file(s) here\n\n(or click to browse)", font=("Roboto", 14), text_color="gray70")
        self.lbl_drop.pack(expand=True)
        self.lbl_drop.bind("<Button-1>", lambda e: self.browse_input_file())

        self.lbl_input_path = ctk.CTkLabel(self, textvariable=self.input_file_display, font=("Roboto", 11), text_color="#2CC985", wraplength=500)
        self.lbl_input_path.pack(pady=(0, 10), padx=20, fill="x")

        # ==========================================
        # SECTION 2: MASTERING ZONE
        # ==========================================
        self.frame_mastering = ctk.CTkFrame(self)
        self.frame_mastering.pack(pady=10, padx=20, fill="x")

        self.lbl_mastering_title = ctk.CTkLabel(self.frame_mastering, text="Mastered SOFA", font=("Roboto Medium", 15))
        self.lbl_mastering_title.grid(row=0, column=0, columnspan=3, pady=(10, 5), padx=15, sticky="w")

        self.lbl_mastering_desc = ctk.CTkLabel(self.frame_mastering, text="Generates mastered variations of the input SOFA file.", text_color="gray60", font=("Roboto", 12))
        self.lbl_mastering_desc.grid(row=1, column=0, columnspan=2, pady=(0, 10), padx=15, sticky="w")

        self.btn_run_mastering = ctk.CTkButton(self.frame_mastering, text="Generate Mastered SOFA", height=35, state="disabled", fg_color="#3B8ED0", command=self.run_mastering)
        self.btn_run_mastering.grid(row=1, column=2, pady=(0, 10), padx=15, sticky="e")

        self.radio_same = ctk.CTkRadioButton(self.frame_mastering, text="Same Folder as Input (\\\\sofa_mastered)", variable=self.output_mode, value="same", command=self.toggle_output_state)
        self.radio_same.grid(row=2, column=0, columnspan=3, pady=5, padx=15, sticky="w")

        self.radio_custom = ctk.CTkRadioButton(self.frame_mastering, text="Selected Folder:", variable=self.output_mode, value="custom", command=self.toggle_output_state)
        self.radio_custom.grid(row=3, column=0, pady=10, padx=15, sticky="w")

        self.entry_custom_out = ctk.CTkEntry(self.frame_mastering, textvariable=self.custom_output_path, state="disabled")
        self.entry_custom_out.grid(row=3, column=1, pady=10, padx=5, sticky="ew")

        self.btn_browse_out = ctk.CTkButton(self.frame_mastering, text="Browse", width=70, state="disabled", command=self.browse_output_dir)
        self.btn_browse_out.grid(row=3, column=2, pady=10, padx=15)

        self.frame_mastering.columnconfigure(1, weight=1)

        # ==========================================
        # SECTION 3: DFHRTF ZONE
        # ==========================================
        self.frame_df = ctk.CTkFrame(self)
        self.frame_df.pack(pady=10, padx=20, fill="x")

        self.lbl_df_title = ctk.CTkLabel(self.frame_df, text="Diffuse Field HRTF (DFHRTF)", font=("Roboto Medium", 15))
        self.lbl_df_title.grid(row=0, column=0, columnspan=3, pady=(10, 5), padx=15, sticky="w")

        self.radio_df_same = ctk.CTkRadioButton(self.frame_df, text="Same Folder as Input (\\\\DFHRTF)", variable=self.df_output_mode, value="same", command=self.toggle_df_output_state)
        self.radio_df_same.grid(row=1, column=0, columnspan=3, pady=5, padx=15, sticky="w")

        self.radio_df_custom = ctk.CTkRadioButton(self.frame_df, text="Selected Folder:", variable=self.df_output_mode, value="custom", command=self.toggle_df_output_state)
        self.radio_df_custom.grid(row=2, column=0, pady=5, padx=15, sticky="w")

        self.entry_df_custom_out = ctk.CTkEntry(self.frame_df, textvariable=self.df_custom_output_path, state="disabled")
        self.entry_df_custom_out.grid(row=2, column=1, pady=5, padx=5, sticky="ew")

        self.btn_df_browse_out = ctk.CTkButton(self.frame_df, text="Browse", width=70, state="disabled", command=self.browse_df_output_dir)
        self.btn_df_browse_out.grid(row=2, column=2, pady=5, padx=15)

        self.lbl_prefix = ctk.CTkLabel(self.frame_df, text="File Prefix (Optional):")
        self.lbl_prefix.grid(row=3, column=0, pady=5, padx=15, sticky="w")

        self.entry_prefix = ctk.CTkEntry(self.frame_df, textvariable=self.df_file_prefix)
        self.entry_prefix.grid(row=3, column=1, columnspan=2, pady=5, padx=(5, 15), sticky="ew")

        self.lbl_tilt = ctk.CTkLabel(self.frame_df, text="DF Tilt Amount:")
        self.lbl_tilt.grid(row=4, column=0, pady=5, padx=15, sticky="w")

        self.slider_tilt = ctk.CTkSlider(self.frame_df, from_=-1.5, to=0.5, variable=self.tilt_value, number_of_steps=20, command=self.update_tilt_label)
        self.slider_tilt.grid(row=4, column=1, pady=5, padx=5, sticky="ew")

        self.lbl_tilt_val = ctk.CTkLabel(self.frame_df, text="-1.00 dB/oct", width=80)
        self.lbl_tilt_val.grid(row=4, column=2, pady=5, padx=15, sticky="e")

        self.btn_run_df = ctk.CTkButton(self.frame_df, text="Generate DFHRTF Files", height=35, state="disabled", fg_color="#3B8ED0", command=self.run_dfhrtf)
        self.btn_run_df.grid(row=5, column=0, columnspan=3, pady=(15, 10), padx=15, sticky="ew")

        self.frame_df.columnconfigure(1, weight=1)

    # --- EVENT HANDLERS ---

    def on_file_drop(self, event):
        files = self.tk.splitlist(event.data)
        valid_files = [f for f in files if f.lower().endswith(".sofa")]
        
        if valid_files:
            self.input_files = valid_files
            display_text = ", ".join([os.path.basename(f) for f in self.input_files])
            self.input_file_display.set(display_text)
            self.lbl_drop.configure(text=f"{len(self.input_files)} SOFA file(s) loaded!")
            self.drop_frame.configure(border_color="#2CC985")
            
            # Enable buttons and set to green
            self.btn_run_mastering.configure(state="normal", fg_color="#28a745", hover_color="#218838")
            self.btn_run_df.configure(state="normal", fg_color="#28a745", hover_color="#218838")
        else:
            messagebox.showerror("Invalid File", "Please drop valid .sofa file(s).")

    def browse_input_file(self):
        filepaths = filedialog.askopenfilenames(filetypes=[("SOFA Files", "*.sofa")])
        if filepaths:
            self.input_files = list(filepaths)
            display_text = ", ".join([os.path.basename(f) for f in self.input_files])
            self.input_file_display.set(display_text)
            self.lbl_drop.configure(text=f"{len(self.input_files)} SOFA file(s) loaded!")
            self.drop_frame.configure(border_color="#2CC985")
            
            # Enable buttons and set to green
            self.btn_run_mastering.configure(state="normal", fg_color="#28a745", hover_color="#218838")
            self.btn_run_df.configure(state="normal", fg_color="#28a745", hover_color="#218838")

    def browse_output_dir(self):
        dirpath = filedialog.askdirectory()
        if dirpath:
            self.custom_output_path.set(dirpath)

    def toggle_output_state(self):
        if self.output_mode.get() == "custom":
            self.entry_custom_out.configure(state="normal")
            self.btn_browse_out.configure(state="normal")
        else:
            self.entry_custom_out.configure(state="disabled")
            self.btn_browse_out.configure(state="disabled")

    def browse_df_output_dir(self):
        dirpath = filedialog.askdirectory()
        if dirpath:
            self.df_custom_output_path.set(dirpath)

    def toggle_df_output_state(self):
        if self.df_output_mode.get() == "custom":
            self.entry_df_custom_out.configure(state="normal")
            self.btn_df_browse_out.configure(state="normal")
        else:
            self.entry_df_custom_out.configure(state="disabled")
            self.btn_df_browse_out.configure(state="disabled")

    def update_tilt_label(self, value):
        self.lbl_tilt_val.configure(text=f"{float(value):.2f} dB/oct")

    def update_bias_label(self, value):
        self.lbl_bias_val.configure(text=f"{float(value):.2f}")

    # --- EXECUTION HELPERS ---

    def get_output_directory(self, input_path):
        """Calculates the final output directory based on user settings."""
        if self.output_mode.get() == "same":
            out_dir = os.path.join(os.path.dirname(os.path.normpath(input_path)), "sofa_mastered")
            if not os.path.exists(out_dir):
                os.makedirs(out_dir, exist_ok=True)
            return out_dir
        else:
            return self.custom_output_path.get()

    def validate_paths(self):
        """Ensures inputs and outputs are valid before running."""
        if not self.input_files:
            messagebox.showerror("Error", "Please provide at least one valid input SOFA file.")
            return False
            
        if self.output_mode.get() == "custom":
            output_dir = self.custom_output_path.get()
            if not output_dir or not os.path.exists(output_dir):
                messagebox.showerror("Error", "Please provide a valid custom output folder.")
                return False
            
        return True

    def get_df_output_directory(self, input_path):
        """Calculates the final output directory for DFHRTF based on user settings."""
        if self.df_output_mode.get() == "same":
            out_dir = os.path.join(os.path.dirname(os.path.normpath(input_path)), "DFHRTF")
            if not os.path.exists(out_dir):
                os.makedirs(out_dir, exist_ok=True)
            return out_dir
        else:
            return self.df_custom_output_path.get()

    def validate_df_paths(self):
        """Ensures inputs and outputs for DFHRTF are valid before running."""
        if not self.input_files:
            messagebox.showerror("Error", "Please provide at least one valid input SOFA file.")
            return False
            
        if self.df_output_mode.get() == "custom":
            output_dir = self.df_custom_output_path.get()
            if not output_dir or not os.path.exists(output_dir):
                messagebox.showerror("Error", "Please provide a valid custom output folder for DFHRTF.")
                return False
            
        return True

    # --- INDEPENDENT RUN ACTIONS ---

    def run_mastering(self):
        if not self.validate_paths(): return

        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_master = os.path.join(scripts_dir, "generate_sofa_outputs.py")

        if not os.path.exists(script_master):
            return messagebox.showerror("Missing Script", f"Could not find:\n{script_master}")

        try:
            # NOTE: Update these command arguments to exactly match what 
            # your standalone generate_sofa_outputs.py script expects!
            for input_path in self.input_files:
                output_dir = self.get_output_directory(input_path)
                cmd = [
                    sys.executable, "-u", script_master,
                    "--input", input_path,
                    "--output", output_dir
                ]
                print("Executing:", " ".join(cmd))
                subprocess.run(cmd, check=True)
            messagebox.showinfo("Success", "Mastered SOFA files generated successfully!")
            
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Execution Error", f"Script failed with code: {e.returncode}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def run_dfhrtf(self):
        if not self.validate_df_paths(): return

        tilt_val = round(self.tilt_value.get(), 2)
        bias_val = round(self.bias_value.get(), 2)
        prefix_val = self.df_file_prefix.get().strip()
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_extras = os.path.join(scripts_dir, "generate_extras.py")

        if not os.path.exists(script_extras):
            return messagebox.showerror("Missing Script", f"Could not find:\n{script_extras}")

        try:
            for input_path in self.input_files:
                output_dir = self.get_df_output_directory(input_path)
                cmd = [
                    sys.executable, "-u", script_extras, 
                    "--input", input_path, 
                    "--output_dir", output_dir, 
                    "--tilt", str(tilt_val),
                    "--front_bias", str(bias_val)
                ]
                if prefix_val:
                    cmd.extend(["--prefix", prefix_val])
                print("Executing:", " ".join(cmd))
                subprocess.run(cmd, check=True)
            messagebox.showinfo("Success", f"DFHRTF files generated successfully (Tilt: {tilt_val} dB/oct, Bias: {bias_val})!")

        except subprocess.CalledProcessError as e:
            messagebox.showerror("Execution Error", f"Script failed with code: {e.returncode}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

if __name__ == "__main__":
    app = SofaMasteringApp()
    app.mainloop()