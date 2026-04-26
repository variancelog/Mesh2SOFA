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

        self.title("SOFA Mastering & Extras Tool")
        self.geometry("600x700")
        self.resizable(False, False)

        # Application Variables
        self.input_file = ctk.StringVar(value="")
        self.output_mode = ctk.StringVar(value="same") # "same" or "custom"
        self.custom_output_path = ctk.StringVar(value="")
        self.tilt_value = ctk.DoubleVar(value=-0.8)
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

        self.lbl_drop = ctk.CTkLabel(self.drop_frame, text="Drag & Drop .sofa file here\n\n(or click to browse)", font=("Roboto", 14), text_color="gray70")
        self.lbl_drop.pack(expand=True)
        self.lbl_drop.bind("<Button-1>", lambda e: self.browse_input_file())

        self.lbl_input_path = ctk.CTkLabel(self, textvariable=self.input_file, font=("Roboto", 11), text_color="#2CC985")
        self.lbl_input_path.pack(pady=(0, 10), padx=20, fill="x")

        # ==========================================
        # SECTION 2: OUTPUT ZONE
        # ==========================================
        self.frame_output = ctk.CTkFrame(self)
        self.frame_output.pack(pady=10, padx=20, fill="x")

        self.lbl_out_title = ctk.CTkLabel(self.frame_output, text="Output Destination", font=("Roboto Medium", 15))
        self.lbl_out_title.grid(row=0, column=0, columnspan=3, pady=(10, 5), padx=15, sticky="w")

        self.radio_same = ctk.CTkRadioButton(self.frame_output, text="Same Folder as Input", variable=self.output_mode, value="same", command=self.toggle_output_state)
        self.radio_same.grid(row=1, column=0, columnspan=3, pady=5, padx=15, sticky="w")

        self.radio_custom = ctk.CTkRadioButton(self.frame_output, text="Selected Folder:", variable=self.output_mode, value="custom", command=self.toggle_output_state)
        self.radio_custom.grid(row=2, column=0, pady=10, padx=15, sticky="w")

        self.entry_custom_out = ctk.CTkEntry(self.frame_output, textvariable=self.custom_output_path, state="disabled")
        self.entry_custom_out.grid(row=2, column=1, pady=10, padx=5, sticky="ew")

        self.btn_browse_out = ctk.CTkButton(self.frame_output, text="Browse", width=70, state="disabled", command=self.browse_output_dir)
        self.btn_browse_out.grid(row=2, column=2, pady=10, padx=15)

        self.frame_output.columnconfigure(1, weight=1)

        # ==========================================
        # SECTION 3: MASTERING ZONE
        # ==========================================
        self.frame_mastering = ctk.CTkFrame(self)
        self.frame_mastering.pack(pady=10, padx=20, fill="x")

        self.lbl_mastering_title = ctk.CTkLabel(self.frame_mastering, text="Mastered SOFA", font=("Roboto Medium", 15))
        self.lbl_mastering_title.grid(row=0, column=0, pady=(10, 5), padx=15, sticky="w")

        self.lbl_mastering_desc = ctk.CTkLabel(self.frame_mastering, text="Generates mastered variations of the input SOFA file.", text_color="gray60", font=("Roboto", 12))
        self.lbl_mastering_desc.grid(row=1, column=0, pady=(0, 10), padx=15, sticky="w")

        self.btn_run_mastering = ctk.CTkButton(self.frame_mastering, text="Generate Mastered SOFA", height=35, fg_color="#3B8ED0", hover_color="#36719F", command=self.run_mastering)
        self.btn_run_mastering.grid(row=1, column=1, pady=(0, 10), padx=15, sticky="e")
        
        self.frame_mastering.columnconfigure(0, weight=1)

        # ==========================================
        # SECTION 4: DFHRTF ZONE
        # ==========================================
        self.frame_df = ctk.CTkFrame(self)
        self.frame_df.pack(pady=10, padx=20, fill="x")

        self.lbl_df_title = ctk.CTkLabel(self.frame_df, text="Diffuse Field HRTF (Extras)", font=("Roboto Medium", 15))
        self.lbl_df_title.grid(row=0, column=0, columnspan=3, pady=(10, 5), padx=15, sticky="w")

        self.lbl_tilt = ctk.CTkLabel(self.frame_df, text="DF Tilt Amount:")
        self.lbl_tilt.grid(row=1, column=0, pady=5, padx=15, sticky="w")

        self.slider_tilt = ctk.CTkSlider(self.frame_df, from_=-1.5, to=0.5, variable=self.tilt_value, number_of_steps=20, command=self.update_tilt_label)
        self.slider_tilt.grid(row=1, column=1, pady=5, padx=(0, 10), sticky="ew")

        self.lbl_tilt_val = ctk.CTkLabel(self.frame_df, text="-0.80 dB/oct", width=80)
        self.lbl_tilt_val.grid(row=1, column=2, pady=5, padx=15, sticky="e")

        self.lbl_bias = ctk.CTkLabel(self.frame_df, text="Frontal Spatial Bias:")
        self.lbl_bias.grid(row=2, column=0, pady=5, padx=15, sticky="w")

        self.slider_bias = ctk.CTkSlider(self.frame_df, from_=0.0, to=4.0, variable=self.bias_value, number_of_steps=40, command=self.update_bias_label)
        self.slider_bias.grid(row=2, column=1, pady=5, padx=(0, 10), sticky="ew")

        self.lbl_bias_val = ctk.CTkLabel(self.frame_df, text="0.00", width=80)
        self.lbl_bias_val.grid(row=2, column=2, pady=5, padx=15, sticky="e")

        self.btn_run_df = ctk.CTkButton(self.frame_df, text="Generate DFHRTF Files", height=35, fg_color="#28a745", hover_color="#218838", command=self.run_dfhrtf)
        self.btn_run_df.grid(row=3, column=0, columnspan=3, pady=(15, 10), padx=15, sticky="ew")

        self.frame_df.columnconfigure(1, weight=1)

    # --- EVENT HANDLERS ---

    def on_file_drop(self, event):
        filepath = event.data.strip("{}")
        if filepath.lower().endswith(".sofa"):
            self.input_file.set(filepath)
            self.lbl_drop.configure(text="SOFA file loaded!")
            self.drop_frame.configure(border_color="#2CC985")
        else:
            messagebox.showerror("Invalid File", "Please drop a valid .sofa file.")

    def browse_input_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("SOFA Files", "*.sofa")])
        if filepath:
            self.input_file.set(filepath)
            self.lbl_drop.configure(text="SOFA file loaded!")
            self.drop_frame.configure(border_color="#2CC985")

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

    def update_tilt_label(self, value):
        self.lbl_tilt_val.configure(text=f"{float(value):.2f} dB/oct")

    def update_bias_label(self, value):
        self.lbl_bias_val.configure(text=f"{float(value):.2f}")

    # --- EXECUTION HELPERS ---

    def get_output_directory(self, input_path):
        """Calculates the final output directory based on user settings."""
        if self.output_mode.get() == "same":
            return os.path.dirname(os.path.normpath(input_path))
        else:
            return self.custom_output_path.get()

    def validate_paths(self):
        """Ensures inputs and outputs are valid before running."""
        input_path = self.input_file.get()
        if not input_path or not os.path.exists(input_path):
            messagebox.showerror("Error", "Please provide a valid input SOFA file.")
            return None, None
            
        output_dir = self.get_output_directory(input_path)
        if not output_dir or not os.path.exists(output_dir):
            messagebox.showerror("Error", "Please provide a valid custom output folder.")
            return None, None
            
        return input_path, output_dir

    # --- INDEPENDENT RUN ACTIONS ---

    def run_mastering(self):
        input_path, output_dir = self.validate_paths()
        if not input_path: return

        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_master = os.path.join(scripts_dir, "generate_sofa_outputs.py")

        if not os.path.exists(script_master):
            return messagebox.showerror("Missing Script", f"Could not find:\n{script_master}")

        try:
            # NOTE: Update these command arguments to exactly match what 
            # your standalone generate_sofa_outputs.py script expects!
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
        input_path, output_dir = self.validate_paths()
        if not input_path: return

        tilt_val = round(self.tilt_value.get(), 2)
        bias_val = round(self.bias_value.get(), 2)
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        script_extras = os.path.join(scripts_dir, "generate_extras.py")

        if not os.path.exists(script_extras):
            return messagebox.showerror("Missing Script", f"Could not find:\n{script_extras}")

        try:
            cmd = [
                sys.executable, "-u", script_extras, 
                "--input", input_path, 
                "--output_dir", output_dir, 
                "--tilt", str(tilt_val),
                "--front_bias", str(bias_val)
            ]
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
