# Mesh2SOFA Orchestrator

An unofficial Python-based GUI orchestrator for [Mesh2HRTF](https://github.com/Mesh2HRTF/Mesh2HRTF) that streamlines the end-to-end workflow of generating personalized HRTFs. This tool automates the process from 3D mesh alignment to numerical simulation and final SOFA file export. It requires a 3d mesh of a head to start.

> **Note:** This is an unofficial orchestrator and is not directly affiliated with the Mesh2HRTF project.

## Features

* **Workflow Management:** Guided 7-step process from raw mesh to final SOFA.
* **Visual Interface:** User-friendly GUI built with CustomTkinter.
* **Blender Automation:** Automatically sets up scenes for mesh grading.
* **Simulation Control:** Runs NumCalc simulations (supports both Standard and Low-Res/16kHz modes).
* **Cross-Platform:** Designed to run on Windows, Mac, and Linux.

## Prerequisites

To use this orchestrator, you must have the following installed:

1.  **Python 3.10+** including required packages (outlined below)
2.  **[Mesh2HRTF](https://sourceforge.net/projects/mesh2hrtf/)** including compiled NumCalc and Mesh Grading Tool executables.
    - NOTE: Make sure to compile NumCalc from source - the Windows binaries on mesh2HRTF tools for Windows are outdated. 
    - NumCalc binaries should be in the `NumCalc/bin` folder
3.  **[Blender](https://www.blender.org/)** (For use with Mesh2Input - 4.5 LTS recommended).
4.  **Additional Python Libraries** outlined below.

## Installation

1.  Clone the Mesh2SOFA repository:
    ```bash
    git clone https://github.com/variancelog/Mesh2SOFA.git
    ```

2.  Install required Python packages:
    ```bash
    pip install customtkinter pyvista pymeshlab matplotlib vtk
    ```

3.  **Install/compile the mesh grading tool:**
    * **Windows:** You can install the compiled binaries from [mesh2hrtf-tools](https://sourceforge.net/p/mesh2hrtf-tools/code/ci/master/tree/hrtf_mesh_grading_WindowsExe/).
    * **Mac/Linux:** You must compile the tool from the source code included in your Mesh2HRTF installation.
        * Navigate to: `path/to/Mesh2HRTF/mesh2hrtf/Mesh2Input/Meshes/GradingDistanceBased`
        * Build the project using CMake (referenced by the `CMakeLists.txt` in that folder).

4. **Install the Blender Add-on (Initial Setup only):**
    * Open Blender.
    * Go to **Edit > Preferences > Add-ons**.
    * Click **Install From Disk...** from the mini drop-down menu and select `blender_scripts/blender_export_project.py` from this repository.
    * Search for "Mesh2SOFA Automation" in the list and enable it by checking the box (it should be checked by default).
    * The **Mesh2SOFA** panel will now always be available in the 3D Viewport sidebar (press 'N' to open the sidebar that contains the panel, or enable the sidebar from the "View" menu). The panel should be visible by default when running the "Open in Blender" action from Mesh2SOFA.

## Usage

1.  Run the main application:
    ```bash
    pythonw _project_manager_gui.pyw
    ```

2.  **Configuration:**
    * Create a New Project or Open an existing Project.
    * If your computer has limited RAM (e.g. 8-16GB), select the "low-res" option from the settings (⚙) menu. This will use a slightly lower res graded mesh for the simulation, and will calculate the HRTF only up to 16 kHz to save on RAM. However, it will still produce the same 44.1 and 48 kHz SOFA files in the "Generate Mastered SOFA Files" step through upsampling.
      * NOTE: Limited RAM users should use a shoulderless/torsoless mesh to greatly reduce RAM usage on simulations.  
    * Configure your **Project Folder** location (where your project files will be stored), Mesh2HRTF root folder (e.g. C:/Mesh2HRTF), select an **Evaluation Grid** (recommended to stick with "Default"), **Blender Exe** (so Mesh2SOFA knows where Blender is on your computer in Step 3), and the **Mesh Grading Tool** binaries (e.g. mesh-hrtf-tools/hrtf_mesh_grading_WindowsExe/bin on Windows).

3.  **Workflow:**
    * **New Project:** Create a structured folder for your simulation.
    * **Select Raw Mesh** is where you specify which 3d head scan (`.obj` or `.ply`) you want to use, and whether you want to Move or Copy this mesh to your Project Folder for future processing. In either case, the original mesh is always left intact.
    * **1. Align Mesh** firt alingns your mesh along the axis between your ears and ensures the mesh is facing in the correct direction. It then allows you to fine tune the mesh's rotation so that it is facing level.
    * **2. Process & Grade** first uses PyMeshLab to optimize your mesh for the grading step based on your selected Project resolution (Standard or Low-Res), then uses the Mesh Grading tool to create optimized left and right side meshes recommended for NumCalc processing. 
    * **3. Open in Blender** opens a reference Blender file that contains the meshes and materials needed for generating the project folders used by NumCalc.
    * **4. Setup and Export in Blender** is where you use the Mesh2SOFA panel in Blender to assign materials and export the project files (details below). 
    * **5: Run NumCalc Simulation** Runs multiple instances of NumCalc against your project folders. This step is VERY compute and memory instensive. On a normal home computer, this can take anywhere from 8 to 24 hours. It is recommended to run this on as powerful a computer as possible. You can stop the simulation with the "STOP PROCESS" button; it will stop the processes but not delete the simulation output, so you can pick up where you left off.
    * **6. Generate Mastered SOFA Files** Generates multiple versions of the SOFA files that can be used with binaural rendering plugins such as SPARTA Binauraliser and APL Viruoso. Four versions are generated: diffuse-field equalized and non-diffused field equalized, both in 44.1 kHz and 48 kHz samplerates. Either version can be used with SPARTA (it has a built in optional "Apply Diffuse-Field EQ" setting), but Virtuoso expects a SOFA file that is already diffuse-field equalized.
      * *Note on sample length:* The standard HRIR output length is 256 samples. If you are importing external measured SOFA files with an unusually long impulse response, you can enable the **"512 sample (edge case use only)"** option to double the processing and output lengths.
    * **7. Generate Extras** generates separate tilted diffuse-field responses as CSV files for left, right, and average. Frequency response plots are also generated. This step allows you to specify a tilt value (e.g. -.8db per octave), and you can run it multiple times to produce and test different outputs. (The way to use these files is to take blocked-ear canal measurements of your headphones, and equalize or convolve them to the tilted diffuse-field response.)

## Blender Steps

Once the Blender Add-on is installed (see Installation step 4), you can use the **Mesh2SOFA** panel in the 3D Viewport sidebar to automate the workflow:

1. Press **'N'** in the 3D Viewport to open the sidebar if it's not already visible (it should be visible by default after running "Open in Blender" from Mesh2SOFA).
2. Click the **Mesh2SOFA** tab.
3. **Step 1: Assign Materials.** Click the `Assign Materials` button. This will automatically:
   - Create the necessary `Skin`, `Left Ear`, and `Right Ear` materials.
   - Assign the `Skin` material to the `Left_Graded` and `Right_Graded` meshes.
   - Automatically find the ear openings and assign the correct ear materials to the single closest face on each mesh.
   - NOTE: double check that the left and right ear materials are applied to the correct locations (success here depends on the position of the ear canal entrance relative to the tragus shape/size.)
4. **Step 2: Export Projects.** Click the `Export Projects` button. This will generate the two project folders needed by NumCalc in your project's `Exports` folder.

If there are any errors or missing output in the `Exports` folder, check the Blender system console for more details. common issues include the `Left_Graded` or `Right_Graded` meshes being missing from the scene.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

**Note:** This is an unofficial orchestrator and is not directly affiliated with the Mesh2HRTF project.