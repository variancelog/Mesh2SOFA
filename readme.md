# Mesh2SOFA Orchestrator

An unofficial Python-based GUI orchestrator for [Mesh2HRTF](https://github.com/Mesh2HRTF/Mesh2HRTF) that streamlines the end-to-end workflow of generating personalized HRTFs. This tool automates the process from 3D mesh alignment to numerical simulation and final SOFA file export. It requires a 3D mesh of a head to start.

> **Note:** This is an unofficial orchestrator and is not directly affiliated with the Mesh2HRTF project.

![Mesh2SOFA Screenshot](screenshot_mesh2sofa.png?v=2)

## Features

* **Workflow Management:** Guided 7-step process from raw mesh to final SOFA.
* **Visual Interface:** User-friendly GUI built with CustomTkinter.
* **Formal Mesh Import:** Browsing for a raw mesh automatically runs a cleaning pipeline — sliver removal via Blender, structural repair via pymeshfix — before the mesh enters the project.
* **Mesh Inspection & Repair:** Dedicated step to detect and fix mesh problems (holes, non-manifold edges, self-intersections, topological tunnels). Includes an interactive 3D tunnel viewer with click-to-select cut loops and one-keypress in-app tunnel removal.
* **Mesh Alignment:** Interactive 3D viewer for aligning the mesh to the Frankfurt plane with point picking and pitch fine-tuning.
* **Blender Automation:** Automatically sets up scenes for mesh grading and export.
* **Simulation Control:** Launches NumCalc simulations including "Test Mode" to process only the highest frequencies prior to full simulation.
* **SOFA Mastering:** Generates both raw and diffuse-field equalized SOFA files at 44.1 kHz and 48 kHz.
* **DFHRTF:** Compute and export DFHRTF responses with optional tilt.
* **VTK Viewer:** Export and view VTK files to observe sound pressure for each simulated frequency.
* **Cross-Platform:** Designed to run on Windows, Mac, and Linux.

## Prerequisites

To use this orchestrator, you must have the following installed:

1.  **Python 3.10+** including required packages (outlined below and in requirements.txt)
2.  **[Mesh2HRTF](https://sourceforge.net/projects/mesh2hrtf/)** including compiled NumCalc and Mesh Grading Tool executables.
    - NOTE: Make sure to compile NumCalc from source - the Windows binaries on mesh2HRTF-tools for Windows are outdated.
    - NumCalc binaries should be in the `Mesh2HRTF/NumCalc/bin` folder
3.  **[Blender](https://www.blender.org/)** (4.5 LTS recommended) — required for the mesh import step and for Step 4.
4.  **Additional Python Libraries** outlined below.

## Installation

1.  Clone the Mesh2SOFA repository:
    ```bash
    git clone https://github.com/variancelog/Mesh2SOFA.git
    ```

2.  Install required Python packages:
    ```bash
    pip install -r requirements.txt
    ```
    > **Note:** `mesh2hrtf` (from your Mesh2HRTF installation) and Blender's `bpy` are additional dependencies not installed via pip — see Prerequisites above.

3.  **Install/compile the mesh grading tool:**
    * **Windows:** You can install the compiled binaries from [mesh2hrtf-tools](https://sourceforge.net/p/mesh2hrtf-tools/code/ci/master/tree/hrtf_mesh_grading_WindowsExe/).
    * **Mac/Linux:** You must compile the tool from the source code included in your Mesh2HRTF installation.
        * Navigate to: `path/to/Mesh2HRTF/mesh2hrtf/Mesh2Input/Meshes/GradingDistanceBased`
        * Build the project using CMake (referenced by the `CMakeLists.txt` in that folder).

4. **Install the Blender Add-on (Initial Setup only):**
    * Open Blender.
    * Go to **Edit > Preferences > Add-ons**.
    * Click **Install From Disk...** from the mini drop-down menu and select `blender_scripts/mesh2SOFA_blender_addon.py` from this repository.
    * Search for "Mesh2SOFA Automation" in the list and enable it by checking the box (it should be checked by default).
    * The **Mesh2SOFA** panel will now always be available in the 3D Viewport sidebar (press 'N' to open the sidebar, or enable it from the "View" menu).

## Usage

1.  Run the main application:
    ```bash
    python _project_manager_gui.py
    ```

2.  **Configuration:**
    * Create a New Project or Open an existing Project.
    * If your computer has limited RAM (e.g. 8-16GB), select the "Lowres" option from the Project Settings. This will use a slightly lower resolution graded mesh and calculate the HRTF only up to 16 kHz to save on RAM, but will still produce 44.1 kHz and 48 kHz SOFA files through upsampling.
      * NOTE: Limited RAM users are encouraged to use a shoulderless/torsoless mesh to greatly reduce RAM usage during simulation.
    * Under **App Settings**, configure your Mesh2HRTF root folder, **Blender executable** path (required — used both for mesh import and Step 4), and the **Mesh Grading Tool** binary.
    * Under **Project Settings**, select your **Evaluation Grid(s)** and resolution mode.

3.  **Workflow:**

    * **Browse for Raw Mesh** is the entry point for your 3D head scan (`.obj` or `.ply`). Clicking Browse prompts you to Move or Copy the file into the project, then automatically runs a full cleaning pipeline: it inspects for defects, removes tiny sliver triangles via Blender, and repairs structural issues with pymeshfix. A popup summarises the result. If a topological tunnel is detected, you will see a warning — tunnels can only be removed after alignment in Step 2. The Raw Mesh field is read-only; Browse is the only way to set it.

    * **1. Align Mesh** opens an interactive 3D viewer. In Phase 1, pick three landmarks: Left Ear, Right Ear, and Nose Tip. In Phase 2, use a slider to fine-tune the pitch (tilt) of the head to ensure it is level.

    * **2. Inspect & Fix Mesh** *(optional)* runs a mesh health check on the aligned mesh and shows any detected problems: holes, non-manifold edges, self-intersections, and topological tunnels (genus > 0 "pierced-ear" artifacts that break BEM simulation). You can run an automatic pymeshfix repair pass, or launch the tunnel viewer to cut and cap tunnels interactively — click a candidate loop (green "Cut here" / red "Avoid") to select it, then press **C** to apply the cut in-app. This step is optional — grading will proceed with a warning if you skip it.

    * **3. Process & Grade Mesh** uses PyMeshLab to optimize the mesh for your selected resolution (Standard or Lowres), then runs the mesh grading tool to produce optimized left and right meshes for NumCalc.

    * **4. Open Graded Meshes in Blender** opens a reference Blender scene containing the graded meshes and materials. If a `.blend` already exists, you can choose to open it as-is or overwrite it with a fresh import.

    * **5. Export Project Folders** is done inside Blender using the Mesh2SOFA panel (details below). This generates the `Left_Project` and `Right_Project` folders used by NumCalc.

    * **6. Run NumCalc Simulation** runs multiple NumCalc instances against your project folders. This step is very compute- and memory-intensive and can take 8–24 hours on a typical home computer. Use "Test Mode" to run a stability check on the highest frequencies before committing to a full simulation. You can stop the simulation at any time with the **STOP PROCESS** button — simulation output is preserved so you can resume later.

    * **7. Generate Mastered SOFA Files** produces four SOFA file variants: diffuse-field equalized and non-equalized, at both 44.1 kHz and 48 kHz. Either variant can be used with SPARTA Binauraliser (which has a built-in optional "Apply Diffuse-Field EQ" setting), while renderers such as APL Virtuoso expect pre-equalized files.

    * **EXTRAS:**
      * **Generate DFHRTF Files** generates tilted diffuse-field responses as CSV files for left, right, and average channels, plus frequency response plots. You can specify a tilt value (e.g. -1.0 dB/octave) and run it multiple times to test different outputs.
      * **Generate Paraview VTK Files** exports pressure data from your simulation into VTK format for a specified frequency range. Use the included `_vtk_viewer.py` tool (or ParaView) to interactively visualize the acoustic pressure fields around the head model.

## Blender Steps

Once the Blender Add-on is installed (see Installation step 4), use the **Mesh2SOFA** panel in the 3D Viewport sidebar to complete Step 5:

1. Press **'N'** in the 3D Viewport to open the sidebar if it's not already visible.
2. Click the **Mesh2SOFA** tab.
3. **Step 1: Assign Materials.** Click `Assign Materials`. This will:
   - Create the necessary `Skin`, `Left Ear`, and `Right Ear` materials.
   - Assign `Skin` to the `Left_Graded` and `Right_Graded` meshes.
   - Automatically find the ear openings and assign the correct ear material to the closest face on each side.
   - NOTE: double-check that the left and right ear materials are applied to the correct locations (success depends on the position of the ear canal entrance relative to the tragus shape/size).
4. **Step 2: Export Projects.** Click `Export Projects`. This generates the two project folders needed by NumCalc in your project's `Exports` folder.

If there are errors or missing output in the `Exports` folder, check the Blender system console for details. Common issues include `Left_Graded` or `Right_Graded` meshes missing from the scene.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

**Note:** This is an unofficial orchestrator and is not directly affiliated with the Mesh2HRTF project.
