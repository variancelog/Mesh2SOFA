# Changelog

## 2026-05-03

**1. Sofa Mastering Tool Updates**
- Enabled horizontal window resizing.
- Improved the layout when working with multiple SOFA files by upgrading the file list display to a dynamic, wrapping text box that adjusts its height automatically.

## 2026-05-02

**1. Sofa Mastering Tool Updates**
- Added support for processing multiple SOFA files at once.
- Removed the standalone `batch_dfhrtf_gui.py` script as batch processing is now natively supported.
- Restructured SOFA Mastering and DFHRTF outputs sections.
- Added file prefix option for DFHRTF file outputs.
- When selecting "Same Folder" for outputs, mastered SOFA files are now saved to a `sofa_mastered` subfolder and DFHRTF files are saved to a `DFHRTF` subfolder.
- Removed setting for front spatial bias because it turns out not to be very useful.
- Updated `readme_sofa_mastering_tool.md` to reflect the new functionality.

## 2026-04-25

**1. Frequency Resolution Updates**
- Changed the "Standard" mode maximum frequency target from 21kHz to **18kHz** across the project.
- Updated `process_and_grade.py` to use a slightly finer intermediate mesh target (`target_mm_base = 0.6` instead of `0.65`).
- Updated `run_numcalc_test.py` to check simulation stability at index 120 (18kHz) instead of 140.

**2. Multi-Grid Support**
- **GUI (`_project_manager_gui.py`)**: Replaced the single-select combobox for Evaluation Grids with a new `GridSelectionDialog`. You can now select multiple grids at once using checkboxes.
- **Blender Export (`export_blender_project.py`)**: Updated the export script to support multiple selected grids, formatting the comma-separated selections into semicolons for Mesh2HRTF.

**3. DFHRTF Generation & Mastering Improvements**
- **Queueing (`_project_manager_gui.py`)**: Step 7 (Generate Extras) now automatically scans the output folder for *all* exported `48000Hz.sofa` files and queues them up sequentially, instead of hardcoding a single `HRIR_48000Hz.sofa` target.
- **Frontal Spatial Bias (`sofa_mastering_tool.py`)**: Added a new "Frontal Spatial Bias" slider (0.0 to 4.0) to the standalone Mastering Tool, passing the `--front_bias` parameter to the generation script. I thought this might be interesting for use with DFHRTF-based headphone equalization but will likely remvove this in a later release.
- **New Tool (`batch_dfhrtf_gui.py`)**: Added a batch tool for generating DFHRTF frequency response files from SOFA files,  specifically for processing publicly available SOFA repos such as SONICOM and HUTUBS (which I've published on variancelog.squig.link)

**4. Blender Script Stability**
- **`export_blender_project.py`**: Improved the method for duplicating the "Reference" mesh. It now safely copies the object data directly via `target_obj.copy()` instead of relying on Blender's sometimes finicky `bpy.ops` context overrides. (Prevents deletion of references meshes that was happening in some cases.)
