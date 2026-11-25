import bpy
import bmesh
import os
import json
import math

bl_info = {
    "name": "Mesh2SOFA Automation",
    "author": "Mesh2SOFA",
    "version": (1, 3, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Mesh2SOFA",
    "description": "Automates material assignment, skin/hair boundary conditions and project export for Mesh2HRTF",
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
"Right Ear" materials on the Left_Graded and Right_Graded meshes, and the "Skin" material to
the remainder of the meshes. A fourth "Hair" material slot is also created (unused until
you paint a hair region). Please double check that the materials have been assigned
correctly before proceeding with the next step. Re-running Assign Materials resets every
face (including any painted hair) to Skin.
6. BOUNDARY CONDITION
"Boundary" selects how the head surface is simulated:
  - Skin absorption (Brinkmann 2025) [default]: the Skin material gets the skin
    admittance from Brinkmann et al. (2025), written to <project>/Exports/Materials.
  - Rigid (legacy): fully rigid surface, as in Mesh2SOFA <= 1.6.
In Skin mode you can optionally enable "Hair absorption". The hair region is the set of
faces using the "Hair" material: use "Auto-select Hair Region" (adapted from the paper's
rule set), then refine it with "Assign Hair to Selected" (Edit Mode face selection on the
active object) or reset it with "Clear Hair". The hair slope m sets how fast hair
absorption rises with frequency (4.5 thin/short, 11 medium, 18 thick hair).
7. EXPORT PROJECTS
Once the materials have been assigned, the "Export Projects" button will use Mesh2Input
functions from your local Mesh2HRTF folder to create the folders and files expected by the
NumCalc simulation. The exported folders will be saved in your projects "Exports" folder
(within your main Mesh2SOFA project folder.) After export, NC.inp is checked to make sure
the boundary condition actually written matches the one selected.
8. Once the project folders are exported, return to Mesh2SOFA to proceed with the simulation.
'''

# ==============================================================================
# CONFIGURATION
# ==============================================================================
EXPORT_SUBFOLDER = "Exports"
MATERIALS_SUBFOLDER = "Materials"
DEFAULT_M2H_PATH = r'C:\Mesh2HRTF\mesh2hrtf'
DEFAULT_GRID = 'Default'

# project_resolution -> nominal max_hz, plus the selectable frequency step. The
# add-on can't import core, so this mirrors RESOLUTION_MODES / STEP_SIZES /
# normalize_step / frequency_plan in core/project_store.py -- keep them in sync
# (tests/test_resolution_modes.py checks they match).
NOMINAL_MAX_HZ = {
    "lowres":   16050,
    "standard": 18000,
    "hires":    20000,
}
STEP_SIZES = (150, 100)
DEFAULT_STEP_HZ = 150


def normalize_step(value):
    """150 or 100 (int, or a float equal to one); anything else -> 150."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_STEP_HZ
    return int(value) if value in STEP_SIZES else DEFAULT_STEP_HZ


def frequency_range(resolution, step_hz):
    """(max_hz, step_hz); max is floored onto the step grid."""
    nominal = NOMINAL_MAX_HZ.get(resolution, NOMINAL_MAX_HZ["standard"])
    step = normalize_step(step_hz)
    return (nominal // step) * step, step

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

EAR_MATERIALS = ("Left ear", "Right ear")
MATERIAL_DEFS = [
    # name, viewport colour. Order matters: indices 0-2 are relied upon.
    ("Skin", (0.82, 0.66, 0.49, 1)),
    ("Left ear", (0, 0, 1, 1)),
    ("Right ear", (1, 0, 0, 1)),
    ("Hair", (0.23, 0.13, 0.06, 1)),
]
HAIR_COLOR = MATERIAL_DEFS[3][1]

# ==============================================================================
# BOUNDARY-CONDITION PHYSICS (bpy-free; Brinkmann et al. 2025)
# ==============================================================================
PAPER_REF = ("Brinkmann et al. (2025), 'Skin and hair absorption' "
             "(research/Brinkmann-et-al_2025_Skin-and-hair-absorption.pdf)")

# Skin modelled as a series R-L-C impedance (Brinkmann et al. 2025)
SKIN_R = 9.0          # resistance
SKIN_L = 0.53e-3      # inductance
SKIN_C = 5.3e-6       # capacitance
# NOTE: the divisor is deliberately 1.5 (not 1.5e-4). With 1.5 the model
# reproduces the skin curve as simulated/plotted in Fig. 1 of the paper
# (plateau alpha ~= 0.0565 from ~500 Hz to 20 kHz; alpha(30 Hz) ~= 0.0159,
# alpha(100 Hz) ~= 0.0461, alpha(20 kHz) ~= 0.0560).
SKIN_AREA_DIVISOR = 1.5
RHO_C = 1.2 * 343.2   # characteristic impedance of air used for normalisation

HAIR_SLOPE_DEFAULT = 11.0
EAR_RADIUS_DEFAULT = 40.0

TABLE_F_MAX = 22050
TABLE_F_STEP = 50


def skin_normalized_impedance(f):
    """Normalised (specific) skin impedance Zn at frequency f [Hz] (f > 0)."""
    w = 2.0 * math.pi * f
    z = complex(SKIN_R, w * SKIN_L - 1.0 / (w * SKIN_C))
    return z / SKIN_AREA_DIVISOR / RHO_C


def skin_absorption(f):
    """Skin absorption coefficient alpha_s(f). alpha_s(0) = 0."""
    if f <= 0:
        return 0.0
    zn = skin_normalized_impedance(f)
    return 4.0 * zn.real / ((zn.real + 1.0) ** 2 + zn.imag ** 2)


def hair_absorption(f, slope):
    """Hair absorption alpha_h = max(alpha_s, min(m*f/1e5, 1))."""
    alpha_l = slope * f / 1e5
    return max(skin_absorption(f), min(alpha_l, 1.0))


def admittance_from_absorption(alpha):
    """Real normalised admittance Y = (1 - sqrt(1-a)) / (1 + sqrt(1-a))."""
    alpha = min(max(alpha, 0.0), 1.0)
    r = math.sqrt(1.0 - alpha)
    return (1.0 - r) / (1.0 + r)


def table_frequencies():
    return list(range(0, TABLE_F_MAX + 1, TABLE_F_STEP))


def material_csv_text(kind, hair_slope=None):
    """Mesh2HRTF material csv (ADMI) for kind 'skin' or 'hair'."""
    if kind == "skin":
        model = ("Skin: series R-L-C impedance, R=9, L=0.53e-3, C=5.3e-6, "
                 "Zn=(R+jwL+1/(jwC))/1.5/(rho*c), rho*c=1.2*343.2")
        alpha_fn = skin_absorption
    elif kind == "hair":
        if hair_slope is None:
            raise ValueError("hair_slope is required for kind='hair'")
        model = (f"Hair: alpha_h = max(alpha_skin, min(m*f/1e5, 1)), "
                 f"slope m={hair_slope:g}")
        alpha_fn = lambda f: hair_absorption(f, hair_slope)  # noqa: E731
    else:
        raise ValueError(f"unknown material kind {kind!r}")

    lines = [
        "# Info:",
        f"# Generated by the Mesh2SOFA Blender add-on {'.'.join(map(str, bl_info['version']))}.",
        f"# Model from {PAPER_REF}.",
        f"# {model}",
        "# Admittance Y = (1-sqrt(1-alpha))/(1+sqrt(1-alpha)) (real, normalised by rho*c).",
        "#",
        "# Keyword: ADMI = normalised admittance boundary condition",
        "ADMI",
        "#",
        "# Frequency in Hz, real value, imaginary value",
    ]
    for f in table_frequencies():
        y = admittance_from_absorption(alpha_fn(f))
        lines.append(f"{f}, {y:.8g}, 0.0")
    return "\n".join(lines) + "\n"


def write_material_files(folder, hair_slope_or_None):
    """Write Skin.csv (and Hair.csv if a slope is given) into folder.

    A stale Hair.csv is deleted when hair is off. Returns the written paths.
    """
    os.makedirs(folder, exist_ok=True)
    written = []
    skin_path = os.path.join(folder, "Skin.csv")
    with open(skin_path, "w", newline="\n") as fh:
        fh.write(material_csv_text("skin"))
    written.append(skin_path)

    hair_path = os.path.join(folder, "Hair.csv")
    if hair_slope_or_None is not None:
        with open(hair_path, "w", newline="\n") as fh:
            fh.write(material_csv_text("hair", hair_slope_or_None))
        written.append(hair_path)
    elif os.path.exists(hair_path):
        os.remove(hair_path)
    return written


def boundary_label(mode, hair_slope_or_None):
    if mode == 'RIGID':
        return "rigid (legacy)"
    if hair_slope_or_None is not None:
        return f"skin absorption + hair (m={hair_slope_or_None:g})"
    return "skin absorption"


def nc_inp_admi_materials(nc_text):
    """Return (set of material names with an ADMI line, total ADMI line count)."""
    found = set()
    count = 0
    current = None
    for raw in nc_text.splitlines():
        line = raw.strip()
        if line.startswith("# Material:"):
            current = line[len("# Material:"):].strip()
            continue
        if line.startswith("ELEM") and " ADMI " in f" {line} ":
            count += 1
            if current is not None:
                found.add(current)
            current = None
    return found, count


def verify_nc_inp_boundary(nc_text, expected_admi):
    """None if NC.inp's admittance materials equal expected_admi, else error text."""
    found, count = nc_inp_admi_materials(nc_text)
    expected = set(expected_admi)
    if not expected:
        if count:
            return (f"NC.inp contains {count} ADMI boundary line(s) "
                    f"({', '.join(sorted(found)) or 'unnamed'}) but RIGID was selected")
        return None
    if found != expected or count != len(expected):
        return (f"NC.inp admittance materials {sorted(found)} "
                f"do not match expected {sorted(expected)}")
    return None


def compute_hair_mask(centers, ear_radius, zmax=None, xmin=None):
    """Hair-region classifier adapted from Brinkmann et al. 2025, sec. 2.2.

    centers: list of (x, y, z) face centres in mm, frame x=front, y=left,
    z=up, origin on the interaural axis. zmax / xmin default to the extremes
    of the centres (callers may pass the vertex extremes instead).
    Returns a list of bool (True = hair).
    """
    n = len(centers)
    if n == 0:
        return []
    if zmax is None:
        zmax = max(c[2] for c in centers)
    if xmin is None:
        xmin = min(c[0] for c in centers)

    hair = [False] * n
    # (a) top of the head
    for i, (x, _y, z) in enumerate(centers):
        if z >= 0.7 * zmax:
            hair[i] = True
    # (b) back of the head
    for i, (x, _y, z) in enumerate(centers):
        if x <= 0.2 * xmin and z >= -0.2 * zmax:
            hair[i] = True
    # (c) extend forward up to half of the frontmost hair so far
    hair_x = [centers[i][0] for i in range(n) if hair[i]]
    xmax_hair = max(hair_x) if hair_x else 0.0
    for i, (x, _y, z) in enumerate(centers):
        if x <= 0.5 * xmax_hair and z >= 0.1 * zmax:
            hair[i] = True
    # (d) ear exclusion cylinder around the interaural (y) axis -> skin
    #     (replaces the paper's anthropometric ear ellipse)
    removed = []
    for i, (x, _y, z) in enumerate(centers):
        if hair[i] and math.sqrt(x * x + z * z) <= ear_radius:
            hair[i] = False
            removed.append(i)
    # (e) re-add removed faces in front of the line through
    #     (x=15, z=0.1*zmax) and (x=0, z=0.3*zmax)
    z_lo, z_hi = 0.1 * zmax, 0.3 * zmax
    for i in removed:
        x, _y, z = centers[i]
        if not (0.0 <= x <= xmax_hair and z >= z_lo):
            continue
        # x position of the line at height z (linear through both points)
        x_line = 15.0 + (z - z_lo) * (0.0 - 15.0) / (z_hi - z_lo) if z_hi != z_lo else 15.0
        if x > x_line:
            hair[i] = True
    return hair


# ==============================================================================
# HELPERS
# ==============================================================================

def show_message_box(message="", title="Mesh2HRTF Export", icon='INFO'):
    print(f"[{title}] {message}")
    if getattr(bpy.app, "background", False):
        return

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


def user_settings_path():
    """Mirror of core/app_settings.user_settings_path() (the add-on cannot import core).
    tests/test_app_settings.py asserts the two agree."""
    import sys
    override = os.environ.get("MESH2SOFA_SETTINGS_DIR")
    if override:
        folder = override
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
        folder = os.path.join(base, "Mesh2SOFA")
    elif sys.platform == "darwin":
        folder = os.path.join(os.path.expanduser("~"), "Library",
                              "Application Support", "Mesh2SOFA")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg and os.path.isabs(xdg):
            folder = os.path.join(xdg, "Mesh2SOFA")
        else:
            folder = os.path.join(os.path.expanduser("~"), ".config", "Mesh2SOFA")
    return os.path.join(folder, "app_settings.json")


def get_project_settings():
    base_path = bpy.path.abspath("//")
    json_path = os.path.join(base_path, "project.json")

    # Per-user settings file first, then the legacy file next to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_paths = [user_settings_path(), os.path.join(script_dir, "app_settings.json")]

    settings = {
        "m2h_path": DEFAULT_M2H_PATH,
        "grid": DEFAULT_GRID,
        "resolution": "standard",
        "step": DEFAULT_STEP_HZ
    }

    # Load app settings
    for app_settings_path in candidate_paths:
        if os.path.exists(app_settings_path):
            try:
                with open(app_settings_path, 'r') as f:
                    app_data = json.load(f)
                m2h = app_data.get("mesh2hrtf_path")
                if m2h:
                    settings["m2h_path"] = m2h
                    break
            except: pass

    # Load project settings
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
                settings["grid"] = data.get("eval_grid", DEFAULT_GRID)
                settings["resolution"] = data.get("project_resolution", "standard")
                if "step_size" in data:
                    raw_step = data["step_size"]
                    settings["step"] = normalize_step(raw_step)
                    if settings["step"] != raw_step:
                        print(f"[Warning] Invalid step_size {raw_step!r} in project.json; using {DEFAULT_STEP_HZ} Hz.")
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


def material_slot_index(obj, name):
    for i, m in enumerate(obj.data.materials):
        if m is not None and m.name == name:
            return i
    return None


def ear_slot_indices(obj):
    return {i for i, m in enumerate(obj.data.materials)
            if m is not None and m.name in EAR_MATERIALS}


def get_or_create_material(name, color):
    if name in bpy.data.materials:
        material = bpy.data.materials[name]
    else:
        material = bpy.data.materials.new(name=name)
    material.diffuse_color = color
    material.specular_intensity = .1
    return material


def ensure_hair_slot(obj):
    """Return the Hair slot index, appending the slot if an older .blend lacks it."""
    idx = material_slot_index(obj, "Hair")
    if idx is None:
        obj.data.materials.append(get_or_create_material("Hair", HAIR_COLOR))
        idx = len(obj.data.materials) - 1
    return idx


def count_material_faces(obj, name):
    idx = material_slot_index(obj, name)
    if idx is None:
        return 0
    return sum(1 for p in obj.data.polygons if p.material_index == idx)


def remap_material_faces(obj, src_name, dst_name):
    """Move faces from material src_name to dst_name. Returns count moved."""
    src = material_slot_index(obj, src_name)
    dst = material_slot_index(obj, dst_name)
    if src is None or dst is None:
        return 0
    moved = 0
    for p in obj.data.polygons:
        if p.material_index == src:
            p.material_index = dst
            moved += 1
    return moved


def m2h_default_material_conflicts(m2h_path):
    """Skin.csv / Hair.csv present in Mesh2HRTF's own material folder."""
    data_dir = os.path.join(m2h_path, 'Mesh2Input', 'Materials', 'Data')
    return [n for n in ("Skin.csv", "Hair.csv")
            if os.path.isfile(os.path.join(data_dir, n))], data_dir


class _ObjectModeGuard:
    """Switch to OBJECT mode for the duration of a block, restoring EDIT after."""

    def __enter__(self):
        obj = bpy.context.active_object
        self.was_edit = bool(obj and obj.mode == 'EDIT')
        if obj and obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        return self

    def __exit__(self, *exc):
        if self.was_edit and bpy.context.active_object:
            bpy.ops.object.mode_set(mode='EDIT')
        return False

# ==============================================================================
# MATERIAL ASSIGNMENT LOGIC
# ==============================================================================

def setup_materials(obj):
    '''Remove existing and create new materials required by Mesh2HRTF.'''
    # remove existing materials
    if obj.data.materials:
        obj.data.materials.clear()

    # Skin, Left ear, Right ear (indices 0-2, fixed) + Hair (index 3)
    for name, color in MATERIAL_DEFS:
        obj.data.materials.append(get_or_create_material(name, color))


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
        # Note: "Right ear" is index 2 in our materials list ("Skin", "Left ear", "Right ear", "Hair")
        bm.faces[right_index].material_index = 2
        print(f"[{obj.name}] Right ear assigned to face {right_index}")

    bm.to_mesh(obj.data)
    bm.free()

# ==============================================================================
# HAIR REGION LOGIC
# ==============================================================================

def auto_assign_hair(obj, ear_radius):
    """Classify faces of obj into Hair/Skin. Ear-source faces are untouched.
    Must be called in OBJECT mode. Returns number of Hair faces."""
    hair_idx = ensure_hair_slot(obj)
    skin_idx = material_slot_index(obj, "Skin")
    if skin_idx is None:
        raise ValueError(f"{obj.name}: no Skin material (run Assign Materials first)")
    ears = ear_slot_indices(obj)
    mw = obj.matrix_world
    mesh = obj.data

    verts = [mw @ v.co for v in mesh.vertices]
    zmax = max(v[2] for v in verts)
    xmin = min(v[0] for v in verts)
    centers = [tuple(mw @ p.center) for p in mesh.polygons]
    mask = compute_hair_mask(centers, ear_radius, zmax=zmax, xmin=xmin)

    n_hair = 0
    for p, is_hair in zip(mesh.polygons, mask):
        if p.material_index in ears:
            continue
        p.material_index = hair_idx if is_hair else skin_idx
        n_hair += is_hair
    mesh.update()
    return n_hair

# ==============================================================================
# EXPORT LOGIC
# ==============================================================================

def get_boundary_settings(scene):
    """(mode, hair_slope_or_None) from the scene properties."""
    mode = getattr(scene, "m2s_boundary", 'SKIN')
    hair_on = mode == 'SKIN' and bool(getattr(scene, "m2s_use_hair", False))
    slope = float(getattr(scene, "m2s_hair_slope", HAIR_SLOPE_DEFAULT)) if hair_on else None
    return mode, slope


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

    # BOUNDARY CONDITION
    mode, hair_slope = get_boundary_settings(bpy.context.scene)
    hair_active = hair_slope is not None
    label = boundary_label(mode, hair_slope)
    print(f"Boundary: {label}")

    conflicts, m2h_data_dir = m2h_default_material_conflicts(config["m2h_path"])
    if mode == 'RIGID':
        if conflicts:
            msg = (f"RIGID export aborted: {', '.join(conflicts)} found in Mesh2HRTF's "
                   f"material folder ({m2h_data_dir}); it would make the export non-rigid. "
                   "Remove/rename it or choose Skin absorption.")
            show_message_box(msg, icon='ERROR')
            return
        material_search_paths = 'None'
        expected_admi = set()
    else:
        if conflicts:
            print(f"[Warning] {', '.join(conflicts)} in {m2h_data_dir} is shadowed by "
                  "the Mesh2SOFA material folder and will not be used.")
        materials_dir = os.path.join(export_root, MATERIALS_SUBFOLDER)
        for p in write_material_files(materials_dir, hair_slope):
            print(f"Material file written: {p}")
        material_search_paths = materials_dir
        expected_admi = {"Skin", "Hair"} if hair_active else {"Skin"}

    # DETERMINE FREQUENCY RANGE
    resolution = config["resolution"]
    if resolution not in NOMINAL_MAX_HZ:
        print(f"[Warning] Unknown project_resolution '{resolution}'; using standard.")
        resolution = "standard"
    max_freq, step_freq = frequency_range(resolution, config["step"])
    print(f"Mode: {resolution} @ {step_freq} Hz steps (Max {max_freq}Hz)")

    if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

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

        if hair_active and count_material_faces(target_obj, "Hair") == 0:
            errors.append(f"{obj_name}: hair absorption is on but no faces use the Hair "
                          "material (run Auto-select Hair Region or disable hair)")
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

        # Hair not simulated: fold painted hair back into Skin on the copy only,
        # so the painted region persists on the real object.
        if not hair_active:
            moved = remap_material_faces(temp_obj, "Hair", "Skin")
            if moved:
                print(f"[{obj_name}] {moved} Hair face(s) exported as Skin (hair off)")

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
                frequencyVectorValue=step_freq,
                evaluationGrids=config["grid"].replace(",", ";"),
                materialSearchPaths=material_search_paths,
                pictures=False,
                reference=True,
                computeHRIRs=True,
                method='ML-FMM BEM',
                unit='mm',
                speedOfSound='343.18',
                densityOfMedium='1.1839'
            )
            nc_inp = os.path.join(final_output_path, "NumCalc", "source_1", "NC.inp")
            try:
                with open(nc_inp, "r") as fh:
                    verify_error = verify_nc_inp_boundary(fh.read(), expected_admi)
            except OSError as e:
                verify_error = f"cannot read {nc_inp}: {e}"
            if verify_error:
                errors.append(f"{obj_name}: boundary check FAILED, export is not "
                              f"'{label}': {verify_error}")
                print(f"[Error] {job['folder']}: {verify_error}")
            else:
                success_count += 1
                print(f"[Success] Exported {job['folder']} (boundary: {label})")
        except Exception as e:
            errors.append(f"{obj_name}: {e}")

        if "Reference" in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects["Reference"], do_unlink=True)

    if len(errors) == 0:
        show_message_box(f"Successfully exported {success_count} projects! "
                         f"Boundary: {label}", icon='CHECKMARK')
    else:
        for e in errors:
            print(f"[Export Error] {e}")
        more = f" (+{len(errors) - 1} more, see console)" if len(errors) > 1 else ""
        show_message_box(f"Export Errors: {errors[0]}{more}", icon='ERROR')

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


class MESH2SOFA_OT_auto_hair(bpy.types.Operator):
    bl_idname = "mesh2sofa.auto_hair"
    bl_label = "Auto-select Hair Region"
    bl_description = ("Assign the Hair material to the hair region of Left_Graded and "
                      "Right_Graded (rule set adapted from Brinkmann et al. 2025). "
                      "Ear-source faces are never changed")

    def execute(self, context):
        radius = float(getattr(context.scene, "m2s_ear_radius", EAR_RADIUS_DEFAULT))
        done = []
        with _ObjectModeGuard():
            for job in EXPORT_JOBS:
                obj = bpy.data.objects.get(job['mesh'])
                if obj is None:
                    continue
                try:
                    n = auto_assign_hair(obj, radius)
                except ValueError as e:
                    self.report({'ERROR'}, str(e))
                    return {'CANCELLED'}
                done.append(f"{obj.name}: {n} hair faces")
                print(f"[{obj.name}] auto hair region: {n} faces")
        if not done:
            self.report({'ERROR'}, "No target objects found (Left_Graded / Right_Graded).")
            return {'CANCELLED'}
        self.report({'INFO'}, "; ".join(done))
        return {'FINISHED'}


class MESH2SOFA_OT_assign_hair_selected(bpy.types.Operator):
    bl_idname = "mesh2sofa.assign_hair_selected"
    bl_label = "Assign Hair to Selected"
    bl_description = ("Assign the Hair material to the selected faces of the active object "
                      "(Edit Mode face selection). Ear-source faces are skipped")

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh (Left_Graded / Right_Graded) first.")
            return {'CANCELLED'}
        with _ObjectModeGuard():  # flushes the edit-mode selection to the mesh
            hair_idx = ensure_hair_slot(obj)
            ears = ear_slot_indices(obj)
            n = 0
            for p in obj.data.polygons:
                if p.select and p.material_index not in ears:
                    p.material_index = hair_idx
                    n += 1
            obj.data.update()
        if n == 0:
            self.report({'WARNING'}, "No (non-ear) faces selected.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{obj.name}: {n} face(s) set to Hair")
        return {'FINISHED'}


class MESH2SOFA_OT_clear_hair(bpy.types.Operator):
    bl_idname = "mesh2sofa.clear_hair"
    bl_label = "Clear Hair"
    bl_description = "Set all Hair faces of Left_Graded and Right_Graded back to Skin"

    def execute(self, context):
        total = 0
        with _ObjectModeGuard():
            for job in EXPORT_JOBS:
                obj = bpy.data.objects.get(job['mesh'])
                if obj is None:
                    continue
                total += remap_material_faces(obj, "Hair", "Skin")
                obj.data.update()
        self.report({'INFO'}, f"{total} Hair face(s) reset to Skin")
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
        scene = context.scene

        col = layout.column(align=True)
        col.label(text="1. Setup Materials:")
        col.operator(MESH2SOFA_OT_assign_materials.bl_idname, text="Assign Materials", icon='MATERIAL')

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Boundary Condition:")
        col.prop(scene, "m2s_boundary", text="")
        if scene.m2s_boundary == 'SKIN':
            col.prop(scene, "m2s_use_hair")
            if scene.m2s_use_hair:
                box = col.box()
                box.prop(scene, "m2s_hair_slope")
                box.prop(scene, "m2s_ear_radius")
                box.operator(MESH2SOFA_OT_auto_hair.bl_idname, icon='AUTO')
                box.operator(MESH2SOFA_OT_assign_hair_selected.bl_idname, icon='BRUSH_DATA')
                box.operator(MESH2SOFA_OT_clear_hair.bl_idname, icon='X')

        layout.separator()

        col = layout.column(align=True)
        col.label(text="2. Mesh2HRTF Export:")
        col.operator(MESH2SOFA_OT_export_projects.bl_idname, text="Export Projects", icon='EXPORT')


classes = (
    MESH2SOFA_OT_assign_materials,
    MESH2SOFA_OT_auto_hair,
    MESH2SOFA_OT_assign_hair_selected,
    MESH2SOFA_OT_clear_hair,
    MESH2SOFA_OT_export_projects,
    MESH2SOFA_PT_main_panel,
)

SCENE_PROPS = ("m2s_boundary", "m2s_use_hair", "m2s_hair_slope", "m2s_ear_radius")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.m2s_boundary = bpy.props.EnumProperty(
        name="Boundary",
        description="Acoustic boundary condition of the head surface",
        items=[
            ('SKIN', "Skin absorption (Brinkmann 2025)",
             "Skin admittance after Brinkmann et al. (2025) on the Skin material (recommended)"),
            ('RIGID', "Rigid (legacy)", "Fully rigid surface, as in earlier Mesh2SOFA versions"),
        ],
        default='SKIN',
    )
    bpy.types.Scene.m2s_use_hair = bpy.props.BoolProperty(
        name="Hair absorption",
        description="Simulate faces with the Hair material using the hair absorption model",
        default=False,
    )
    bpy.types.Scene.m2s_hair_slope = bpy.props.FloatProperty(
        name="Hair slope m",
        description=("Hair absorption slope m in alpha = m*f/1e5. Guidance: 4.5 thin/short "
                     "hair, 11 medium, 18 thick hair"),
        default=HAIR_SLOPE_DEFAULT, min=1.0, max=30.0,
    )
    bpy.types.Scene.m2s_ear_radius = bpy.props.FloatProperty(
        name="Ear exclusion radius (mm)",
        description=("Faces within this distance of the interaural axis are kept as Skin "
                     "by Auto-select Hair Region"),
        default=EAR_RADIUS_DEFAULT, min=0.0, max=200.0,
    )


def unregister():
    for name in SCENE_PROPS:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
