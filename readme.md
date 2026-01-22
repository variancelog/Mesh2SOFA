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
4.  **Python Libraries** 

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

1.  Run the main application:
    ```bash
    python _project_manager_gui.py
    ```

2.  **Configuration:**
    * On first launch, point the app to your **Mesh2HRTF Root** folder (e.g. C:\Mesh2HRTF).
    * Select your **Scripts Location** (the folder containing this repo, e.g. C:\Mesh2SOFA).
    * Select the **Grading Tool Bin** folder (where `hrtf_mesh_grading.exe` is located).

3.  **Workflow:**
    * **New Project:** Create a structured folder for your simulation.
    * **Step 1-2:** Select your raw 3D head scan (`.obj` or `.ply`) to align and grade the mesh.
    * **Step 3:** Launch Blender automatically to assign materials to the mesh and generate project folders for NumCalc processing.
    * **Step 5:** Execute the NumCalc simulation.
    * **Step 6:** Generate the final spatially oriented acoustic (SOFA) files.
    * **Step 7:** Generate the tilted diffuse-field responses (for eq'ing over ear headphones).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

**Note:** This is an unofficial orchestrator and is not directly affiliated with the Mesh2HRTF project.