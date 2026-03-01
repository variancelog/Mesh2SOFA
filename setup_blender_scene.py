import bpy
import sys
import os

def setup_scene():
    # 1. Parse Arguments (Mesh Folder passed as last arg)
    argv = sys.argv
    if "--" not in argv: return
    
    args = argv[argv.index("--") + 1:]
    mesh_folder = args[0]
    
    print(f"--- Importing Meshes from: {mesh_folder} ---")

    # 2. Define File Paths
    left_ply = os.path.join(mesh_folder, "Left_Graded.ply")
    right_ply = os.path.join(mesh_folder, "Right_Graded.ply")

    # 3. Helper to Import Only (Blender 4.0+ Compatible)
    def import_mesh_only(ply_path):
        if not os.path.exists(ply_path):
            print(f"   [!] Missing: {ply_path}")
            return

        try:
            # Blender 4.0+ uses 'wm.ply_import' (C++ importer)
            # This preserves the filename as the object name (e.g. 'Left_Graded')
            bpy.ops.wm.ply_import(filepath=ply_path)
            print(f"   [+] Imported: {os.path.basename(ply_path)}")
            
        except AttributeError:
            # Fallback for Blender 3.6 and older
            try:
                bpy.ops.import_mesh.ply(filepath=ply_path)
                print(f"   [+] Imported (Legacy): {os.path.basename(ply_path)}")
            except Exception as e:
                print(f"   [Error] Legacy import failed: {e}")
                
        except Exception as e:
            print(f"   [Error] Import failed: {e}")

    # 4. Run Import
    import_mesh_only(left_ply)
    import_mesh_only(right_ply)

    # 5. Save the file (preserving your reference file settings)
    bpy.ops.wm.save_mainfile()
    print("   [i] Project saved.")

if __name__ == "__main__":
    setup_scene()