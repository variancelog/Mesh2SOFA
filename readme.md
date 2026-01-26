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

## Usage

Video tutorial is available [on YouTube here >>](https://youtu.be/NH1TiYYrJgk)

1.  Run the main application:
    ```bash
    python _project_manager_gui.py
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
    * **3. Open in Blender** opens a reference Blender file that contains the materials and export script needed for generating the project folders used by NumCalc. This step requires the user to assign the materials to the mesh (details below).
    * **4. Export Project** is where you run the export script in Blender to create the project files (details below). 
    * **5: Run NumCalc Simulation** Runs multiple instances of NumCalc against your project folders. This step is VERY compute and memory instensive. On a normal home computer, this can take anywhere from 8 to 24 hours. It is recommended to run this on as powerful a computer as possible. You can stop the simulation with the "STOP PROCESS" button; it will stop the processes but not delete the simulation output, so you can pick up where you left off.
    * **6. Generate Mastered SOFA Files** Generates multiple versions of the SOFA files that can be used with binaural rendering plugins such as SPARTA Binauraliser and APL Viruoso. Four versions are generated: diffuse-field equalized and non-diffused field equalized, both in 44.1 kHz and 48 kHz samplerates. Either version can be used with SPARTA (it has a built in optional "Apply Diffuse-Field EQ" setting), but Virtuoso expects a SOFA file that is already diffuse-field equalized.
    * **7. Generate Extras** generates separate tilted diffuse-field responses as CSV files for left, right, and average. Frequency response plots are also generated. This step allows you to specify a tilt value (e.g. -.8db per octave), and you can run it multiple times to produce and test different outputs. (The way to use these files is to take blocked-ear canal measurements of your headphones, and equalize or convolve them to the tilted diffuse-field response.)

## Blender Steps

In Blender, the first step is assigning materials to the two meshes (`Left_Graded` and `Right_Graded`). For each mesh, do the following:

1. Select the mesh. 
2. In the `Material` window on the bottom right side of Blender, click the `+` button three times to add three material slots.
3. With the first material slot selected, click the sphere icon (`Browse Material to be Linked`) and select the `Skin` Material. This will apply the skin material to the entire mesh.
4. Next, add `Left Ear` to the second slot and `Right Ear` to the third slot. NOTE: all three materials must be added to both `Left_Graded` and `Right_Graded` meshes.
5. Switch from `Object` mode to `Edit` mode, and click the `3` on your keyboard to switch to "Face selection" mode.
6. Find a triangle near the top-center of the selected mesh's ear opening. This represents the place a blocked-ear microphone would sit if positioned in the ear and should be roughly where the Y axis intersects with your mesh.
   1. With this face selected, select either the `Left Ear` or `Right Ear` material, depending on the selected mesh, and click `Assign` in the Material window. This will apply that material to the selected face.
   2. You only need to Assign the `Left Ear` material to the `Left_Graded` mesh and the `Right Ear` material to the `Right_Graded` mesh. The `Right Ear` material will remain unassigned on the Left_Graded mesh and vice versa.

Once the materials have been to each mesh, switch to the "Scripting" tab.
1. Click on the `Scripting` tab at the top of Blender.
2. At the bottom of the left pane, click the `Run Script` button (looks like a "Play" button) to generate the two project folders needed by NumCalc.
   1. If the export runs successfully, there should be two similar looking folders in your projects "Exports" folder.
   2. If there are any errors or missing output in the Exports folder, there are likely issues either with one or more of your meshes or materials weren't correctly assigned.


## License

This project is licensed under the MIT License - see the LICENSE file for details.

**Note:** This is an unofficial orchestrator and is not directly affiliated with the Mesh2HRTF project.
