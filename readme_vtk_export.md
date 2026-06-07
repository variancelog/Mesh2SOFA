# VTK Export Workflow in Mesh2SOFA

Here is a step-by-step breakdown of how the VTK export workflow operates in Mesh2SOFA:

### 1. User Input (The GUI)
*   The workflow begins in the main project manager interface (`_project_manager_gui.py`). 
*   When the user initiates a VTK export, a popup dialog (`VTKSettingsDialog`) appears.
*   The user is prompted to enter a **Min Freq (Hz)** (default: 1000) and a **Max Freq (Hz)** (default: 16000).
*   Upon clicking "Generate VTK Files", the GUI validates that the inputs are positive numbers and that the maximum is greater than or equal to the minimum.

### 2. Launching the Export Script
*   The GUI gathers all necessary paths: the main project base folder, the paths to the `Left_Project` and `Right_Project` (inside the `Exports` folder), and the user's defined Mesh2HRTF root path.
*   It then spawns a separate background process, running the `generate_vtk_outputs.py` script and passing the paths and the selected frequency range as command-line arguments.

### 3. Parsing Parameters & Step Mapping
Inside `generate_vtk_outputs.py`, the script must determine which specific simulation steps correspond to the requested frequencies:
*   The script reads the `parameters.json` file located inside the Left/Right project folders to retrieve the full, original list of simulated frequencies.
*   It iterates through this list, checking which frequencies fall within the user's `min_freq` and `max_freq` range.
*   Mesh2HRTF expects 1-based indexing for its VTK steps. Therefore, the script assigns a step number (`index + 1`) to each valid frequency based on its position in the original list.

### 4. Handling the Mesh2HRTF "Reverse" Quirk
There is a specific quirk in Mesh2HRTF's VTK export logic that the script must account for:
*   When Mesh2HRTF exports a range of steps, it processes and outputs the data in **reverse** frequency order relative to the simulated list. This means the file labeled with the lowest step index actually contains the data for the highest frequency in that requested sub-range.
*   To fix this, `generate_vtk_outputs.py` creates a special mapping dictionary (`step_to_freq_map`). It takes the list of valid ascending step numbers and maps them to a *reversed* list of the valid frequencies, ensuring the final files will be labeled correctly later.

### 5. Executing the Mesh2HRTF Export
*   The script dynamically imports the `mesh2hrtf` Python library from the user-specified root path.
*   It identifies the highest and lowest step numbers from the mapping phase (`[min(side_steps), max(side_steps)]`).
*   It calls the Mesh2HRTF API function (`vtk_export` or `export_vtk`) for both the Left and Right ears. It explicitly requests the data in `pressure` mode and in decibels (`dB=True`) for that specific range of steps.
*   Mesh2HRTF runs its internal process, generating raw files named `frequency_step_N.vtk` deep inside the project's internal numerical calculation folders (e.g., `Output2HRTF/vtk` or `NumCalc/source_1/vtk`).

### 6. Copying and Formatting the Output
Once Mesh2HRTF finishes generating the raw files, the script organizes them for the user:
*   **Copying:** It locates the newly generated `vtk` directory and copies its entire contents to the main user-facing output directory: `<project_base>/Output/VTK/Left` (and `Right`).
*   **Renaming:** It walks through these copied files looking for the generic `frequency_step_N.vtk` naming convention. 
*   It extracts the step number (`N`), looks it up in the `step_to_freq_map` to find the correct actual frequency (accounting for the reverse-order quirk), and renames the file to a clean, readable format with leading zeros: **`<frequency>Hz.vtk`** (e.g., `01000Hz.vtk`). 

This ensures the user ends up with a neatly organized `Output/VTK` folder containing accurately named files ready for import into visualization software like ParaView or Mesh2SOFA `_vtk_viewer.py`.