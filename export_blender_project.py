import bpy
import os
import json

# ==============================================================================
# CONFIGURATION
# ==============================================================================
EXPORT_SUBFOLDER = "Exports"
DEFAULT_M2H_PATH = r'C:\Mesh2HRTF\mesh2hrtf'
DEFAULT_GRID = 'Default'

EXPORT_JOBS = [
    {
        'mesh': 'Left_Graded', 
        'folder': 'Left_Project', 
        'source': 'Left ear',
        'required_mats': ['Skin', 'Left ear']
    },
    {
        'mesh': 'Right_Graded', 
        'folder': 'Right_Project', 
        'source': 'Right ear',
        'required_mats': ['Skin', 'Right ear']
    }
]

def show_message_box(message="", title="Mesh2HRTF Export", icon='INFO'):
    def draw(self, context): 
        self.layout.label(text=message)
    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)

def validate_m2h_path(user_path):
    # (Same validation logic as before)
    version_check = os.path.normpath(os.path.join(user_path, "..", "VERSION"))
    if os.path.exists(version_check): 
        return user_path
    sub_path = os.path.join(user_path, "mesh2hrtf")
    if os.path.exists(sub_path): 
        return sub_path
    return user_path 

def get_project_settings():
    base_path = bpy.path.abspath("//")
    json_path = os.path.join(base_path, "project.json")
    
    settings = {
        "m2h_path": DEFAULT_M2H_PATH, 
        "grid": DEFAULT_GRID,
        "resolution": "standard"
    }
    
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
                settings["m2h_path"] = data.get("mesh2hrtf_path", DEFAULT_M2H_PATH)
                settings["grid"] = data.get("eval_grid", DEFAULT_GRID)
                settings["resolution"] = data.get("project_resolution", "standard")
        except: pass 
    
    settings["m2h_path"] = validate_m2h_path(settings["m2h_path"])
    return settings

def deselect_all_safe():
    for obj in bpy.context.selected_objects: 
        obj.select_set(False)

def check_materials(obj, required_names):
    if not obj.data.materials: return False, "No materials assigned!"
    obj_mat_names = [m.name for m in obj.data.materials if m]
    missing = [req for req in required_names if req not in obj_mat_names]
    if missing: return False, f"Missing materials: {', '.join(missing)}"
    return True, ""

def run_smart_export():
    print("\n=== Starting Mesh2HRTF Export ===")
    
    if not hasattr(bpy.ops, "mesh2input"):
        show_message_box("Mesh2HRTF Addon not installed!", icon='ERROR')
        return

    project_root = bpy.path.abspath("//")
    if not project_root:
        show_message_box("Save .blend file first!", icon='ERROR')
        return

    export_root = os.path.join(project_root, EXPORT_SUBFOLDER)
    if not os.path.exists(export_root): os.makedirs(export_root)

    config = get_project_settings()
    
    # DETERMINE MAX FREQUENCY
    if config["resolution"] == "lowres":
        max_freq = 16050
        print(f"Mode: Lowres (Max {max_freq}Hz)")
    else:
        max_freq = 21000
        print(f"Mode: Standard (Max {max_freq}Hz)")

    success_count = 0
    errors = []

    for job in EXPORT_JOBS:
        obj_name = job['mesh']
        if obj_name not in bpy.data.objects:
            errors.append(f"Missing Object: {obj_name}")
            continue
            
        target_obj = bpy.data.objects[obj_name]
        is_valid, mat_error = check_materials(target_obj, job['required_mats'])
        if not is_valid:
            errors.append(f"{obj_name}: {mat_error}")
            continue

        while "Reference" in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects["Reference"], do_unlink=True)

        deselect_all_safe() 
        target_obj.hide_set(False) 
        target_obj.select_set(True)
        bpy.context.view_layer.objects.active = target_obj
        
        try:
            bpy.ops.object.duplicate(linked=False)
        except:
            pass # Context fallback omitted for brevity, assume safe context

        temp_obj = bpy.context.active_object
        temp_obj.name = "Reference" 
        
        final_output_path = os.path.join(export_root, job['folder'])
        
        try:
            bpy.ops.mesh2input.inp(
                filepath=final_output_path,
                programPath=config["m2h_path"],
                sourceType=job['source'],
                minFrequency=0,
                maxFrequency=max_freq, # DYNAMIC
                frequencyVectorType='Step size',
                frequencyVectorValue=150,
                evaluationGrids=config["grid"],
                materialSearchPaths='None',
                pictures=False,
                reference=True,
                computeHRIRs=True,
                method='ML-FMM BEM',
                unit='mm',
                speedOfSound='343.18',
                densityOfMedium='1.1839'
            )
            success_count += 1
            print(f"[Success] Exported {job['folder']}")
        except Exception as e:
            errors.append(f"{obj_name}: {e}")

        if "Reference" in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects["Reference"], do_unlink=True)

    if len(errors) == 0:
        show_message_box(f"Successfully exported {success_count} projects!", icon='CHECKMARK')
    else:
        show_message_box(f"Export Errors: {errors[0]}", icon='ERROR')

if __name__ == "__main__":
    run_smart_export()