import bpy
import bmesh
import os
import json

bl_info = {
    "name": "Mesh2SOFA Automation",
    "author": "Mesh2SOFA",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Mesh2SOFA",
    "description": "Automates material assignment and project export for Mesh2HRTF",
    "category": "3D View",
}

'''
Adapted from "AssignMaterial.py" by 
Robert Pelzer and Fabian Brinkmann, Audio Communication Group, Technical
University of Berlin, Germany to work with the  Mesh2SOFA workflow.

INSTRUCTIONS 
1. This add-on should be installed in Blender BEFORE launching the "Open in Blender" step in Mesh2SOFA.
2. Install this as an Add-on in Blender via:
    Edit > Preferences > Add-ons > Install From Disk... 
3. Once installed, the the add-on appears as a "Mesh2SOFA" panel in the 3D View Sidebar 
next to Item/Tool/View (top right of 3d view window). Note: If the panel is not visible, 
use the "N" keyboard shortcut to show the Sidebar, or enable it from the View menu.
4. AFTER running "Open in Blender" from Mesh2SOFA, you'll use the add-on to first apply 
the correct materials for the simulation, then export the blender project folders for the
next step (NumCalc simulation).
5. ASSIGN MATERIALS
The panel's "Assign Materials" button will attempt to place the correct "Left Ear" and 
"Righ Ear" materials on the Left_Ear and Right_Ear meshes, and the "Skin material" to 
the remainder of the meshes. Please double check that the materials have been assigned 
correctly before proceeding with the next step.
6. EXPORT PROJECTS
Once the materials have been assigned, the "Export Projects" button will use Mesh2Input 
functions from your local Mesh2HRTF folder to create the folders and files expected by the 
NumCalc simulation. The exported folders will be saved in your projects "Exports" folder 
(within your main Mesh2SOFA project folder.)
7. Once the project folders are exported, return to Mesh2SOFA to proceed with the simulation.
'''

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

    # Locate app_settings.json next to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    app_settings_path = os.path.join(script_dir, "app_settings.json")

    settings = {
        "m2h_path": DEFAULT_M2H_PATH, 
        "grid": DEFAULT_GRID,
        "resolution": "standard"
    }

    # Load app settings
    if os.path.exists(app_settings_path):
        try:
            with open(app_settings_path, 'r') as f:
                app_data = json.load(f)
                settings["m2h_path"] = app_data.get("mesh2hrtf_path", DEFAULT_M2H_PATH)
        except: pass

    # Load project settings
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
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

# ==============================================================================
# MATERIAL ASSIGNMENT LOGIC
# ==============================================================================

def setup_materials(obj):
    '''Remove existing and create new materials required by Mesh2HRTF.'''
    # remove existing materials
    if obj.data.materials:
        obj.data.materials.clear()

    # assign materials required for Mesh2HRTF
    for idx, (name, color) in enumerate(zip(
            ["Skin", "Left ear", "Right ear"],
            [(0.82, 0.66, 0.49, 1), (0, 0, 1, 1), (1, 0, 0, 1)])):

        # Use existing material or create new
        if name in bpy.data.materials:
            material = bpy.data.materials[name]
        else:
            material = bpy.data.materials.new(name=name)
        
        # Update colors to match expected
        material.diffuse_color = color
        material.specular_intensity = .1
        
        # Append to object's material slots
        obj.data.materials.append(material)

def get_ear_indices(bm, obj, tolerance, ear):
    '''Return indicees of faces at the entrance to the ear channels'''
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    left_indices = []
    left_xy_distance = []
    left_y = []
    right_indices = []
    right_xy_distance = []
    right_y = []
    min_y = [1000, 1000]

    world = obj.matrix_world

    for face in bm.faces:
        xyz = world @ face.calc_center_median()
        xy_distance = abs(xyz[0]) + abs(xyz[2])
        y = xyz[1]

        if y > 0 and abs(xyz[0]) < tolerance and abs(xyz[2]) < tolerance:
            left_indices.append(face.index)
            left_xy_distance.append(xy_distance)
            left_y.append(abs(y))
            if abs(y) < min_y[0]:
                min_y[0] = abs(y)
        elif y < 0 and abs(xyz[0]) < tolerance and abs(xyz[2]) < tolerance:
            right_indices.append(face.index)
            right_xy_distance.append(xy_distance)
            right_y.append(abs(y))
            if abs(y) < min_y[1]:
                min_y[1] = abs(y)

    left_index = None
    if ear in ("Both ears", "Left ear"):
        min_xy_dist = 1000
        for n in range(len(left_indices)):
            if left_y[n] < min_y[0] + tolerance and left_xy_distance[n] < min_xy_dist:
                min_xy_dist = left_xy_distance[n]
                left_index = left_indices[n]

    right_index = None
    if ear in ("Both ears", "Right ear"):
        min_xy_dist = 1000
        for n in range(len(right_indices)):
            if right_y[n] < min_y[1] + tolerance and right_xy_distance[n] < min_xy_dist:
                min_xy_dist = right_xy_distance[n]
                right_index = right_indices[n]

    return left_index, right_index

def assign_materials_to_object(obj, ear, tolerance=2.0):
    setup_materials(obj)

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    left_index, right_index = get_ear_indices(bm, obj, tolerance, ear)

    # By default everything is Skin (index 0)
    for face in bm.faces:
        face.material_index = 0

    if ear == "Left ear" and left_index is not None:
        bm.faces[left_index].material_index = 1
        print(f"[{obj.name}] Left ear assigned to face {left_index}")
    elif ear == "Right ear" and right_index is not None:
        # Note: "Right ear" is index 2 in our materials list ("Skin", "Left ear", "Right ear")
        bm.faces[right_index].material_index = 2
        print(f"[{obj.name}] Right ear assigned to face {right_index}")

    bm.to_mesh(obj.data)
    bm.free()

# ==============================================================================
# EXPORT LOGIC
# ==============================================================================

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
        max_freq = 18000
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

        # Safely duplicate without relying on bpy.ops context
        temp_obj = target_obj.copy()
        temp_obj.data = target_obj.data.copy()
        if target_obj.users_collection:
            target_obj.users_collection[0].objects.link(temp_obj)
        else:
            bpy.context.scene.collection.objects.link(temp_obj)
            
        temp_obj.name = "Reference" 
        
        deselect_all_safe() 
        temp_obj.hide_set(False) 
        temp_obj.select_set(True)
        bpy.context.view_layer.objects.active = temp_obj
        
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
                evaluationGrids=config["grid"].replace(",", ";"),
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

# ==============================================================================
# UI AND OPERATORS
# ==============================================================================

class MESH2SOFA_OT_assign_materials(bpy.types.Operator):
    bl_idname = "mesh2sofa.assign_materials"
    bl_label = "Assign Materials"
    bl_description = "Assign Skin and Ear materials to Left_Graded and Right_Graded objects"

    def execute(self, context):
        if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
            
        objects_assigned = 0
        for job in EXPORT_JOBS:
            obj_name = job['mesh']
            if obj_name in bpy.data.objects:
                obj = bpy.data.objects[obj_name]
                assign_materials_to_object(obj, ear=job['source'])
                objects_assigned += 1
            else:
                print(f"[Warning] {obj_name} not found in scene for material assignment.")
                
        if objects_assigned > 0:
            show_message_box(f"Assigned materials to {objects_assigned} object(s).", title="Success", icon='CHECKMARK')
        else:
            show_message_box("No target objects found (Left_Graded / Right_Graded).", title="Error", icon='ERROR')
            
        return {'FINISHED'}

class MESH2SOFA_OT_export_projects(bpy.types.Operator):
    bl_idname = "mesh2sofa.export_projects"
    bl_label = "Export Projects"
    bl_description = "Run Mesh2HRTF export for Left and Right projects"

    def execute(self, context):
        run_smart_export()
        return {'FINISHED'}

class MESH2SOFA_PT_main_panel(bpy.types.Panel):
    bl_label = "Mesh2SOFA"
    bl_idname = "MESH2SOFA_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Mesh2SOFA'

    def draw(self, context):
        layout = self.layout
        
        col = layout.column(align=True)
        col.label(text="1. Setup Materials:")
        col.operator(MESH2SOFA_OT_assign_materials.bl_idname, text="Assign Materials", icon='MATERIAL')
        
        layout.separator()
        
        col = layout.column(align=True)
        col.label(text="2. Mesh2HRTF Export:")
        col.operator(MESH2SOFA_OT_export_projects.bl_idname, text="Export Projects", icon='EXPORT')

classes = (
    MESH2SOFA_OT_assign_materials,
    MESH2SOFA_OT_export_projects,
    MESH2SOFA_PT_main_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
