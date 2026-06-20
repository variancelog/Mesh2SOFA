"""
Headless Blender worker — degenerate dissolve + triangulate.

Collapses tiny sliver triangles (edge length below `dist`) using
bmesh.ops.dissolve_degenerate, then triangulates to fix any resulting n-gons.
Run via Blender's --background --python flag; do NOT call directly from Python.

Usage:
  blender --background --python bmesh_cleanup.py -- <in_ply> <out_ply> <dist>

Arguments (after --):
  in_ply   Input PLY mesh path
  out_ply  Output PLY mesh path (written on success; in_ply is never modified)
  dist     Degenerate-dissolve distance threshold in mesh units
           (caller is responsible for unit scaling: 0.3 for mm meshes, 0.0003
           for metre meshes)
"""
import sys
import bpy
import bmesh


# ---------------------------------------------------------------------------
# Import / export helpers (Blender 4.0+ vs 3.x fallbacks)
# ---------------------------------------------------------------------------

def _import_ply(filepath):
    """Import a PLY — Blender 4.0+ C++ importer with 3.x fallback."""
    try:
        bpy.ops.wm.ply_import(filepath=filepath)
    except AttributeError:
        bpy.ops.import_mesh.ply(filepath=filepath)


def _export_ply(filepath):
    """Export the active mesh to PLY — Blender 4.0+ with 3.x fallback."""
    try:
        bpy.ops.wm.ply_export(filepath=filepath, export_selected_objects=True)
    except (AttributeError, TypeError):
        # AttributeError  -> operator doesn't exist (Blender < 4.0)
        # TypeError       -> 'export_selected_objects' not accepted (older 4.x build)
        try:
            bpy.ops.wm.ply_export(filepath=filepath)
        except AttributeError:
            bpy.ops.export_mesh.ply(filepath=filepath)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_cleanup():
    argv = sys.argv
    if "--" not in argv:
        print("[bmesh_cleanup] ERROR: No arguments. "
              "Usage: blender --background --python bmesh_cleanup.py -- <in_ply> <out_ply> <dist>")
        sys.exit(1)

    args = argv[argv.index("--") + 1:]
    if len(args) < 3:
        print(f"[bmesh_cleanup] ERROR: Expected 3 args (in_ply out_ply dist), got {len(args)}")
        sys.exit(1)

    in_ply  = args[0]
    out_ply = args[1]
    dist    = float(args[2])

    print(f"[bmesh_cleanup] Input : {in_ply}")
    print(f"[bmesh_cleanup] Output: {out_ply}")
    print(f"[bmesh_cleanup] dist  : {dist}")

    # Clear the default scene (cube / camera / light) to start clean.
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # --- Import ---
    print("[bmesh_cleanup] Importing PLY...")
    _import_ply(in_ply)

    # Locate the imported mesh object (should be the only one in the scene).
    obj = bpy.context.active_object
    if obj is None or obj.type != 'MESH':
        for o in bpy.data.objects:
            if o.type == 'MESH':
                obj = o
                break
    if obj is None:
        print("[bmesh_cleanup] ERROR: No mesh object found after import.")
        sys.exit(1)

    n_verts_before = len(obj.data.vertices)
    n_faces_before = len(obj.data.polygons)
    print(f"[bmesh_cleanup] Before: {n_verts_before} verts, {n_faces_before} faces")

    # --- bmesh operations ---
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    # Step 1: Merge vertices within dist — directly collapses sliver triangles
    # whose vertices are all extremely close together. More aggressive than
    # dissolve_degenerate alone (which works on edge length and can leave n-gons
    # that triangulate then splits back into thin triangles).
    print(f"[bmesh_cleanup] Step 1: remove_doubles (dist={dist})")
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)

    # Step 2: Dissolve any remaining degenerate edges left after vertex merging.
    print(f"[bmesh_cleanup] Step 2: dissolve_degenerate (dist={dist})")
    bmesh.ops.dissolve_degenerate(bm, dist=dist, edges=bm.edges)

    # Step 3: Triangulate — dissolve/merge can leave n-gons; triangulate so the
    # output is a pure triangle mesh (required for BEM/NumCalc).
    print("[bmesh_cleanup] Step 3: triangulate")
    bmesh.ops.triangulate(bm, faces=bm.faces)

    # Step 4: Second dissolve pass — triangulate can create new thin edges when
    # splitting elongated n-gons; clean those up before export.
    print(f"[bmesh_cleanup] Step 4: dissolve_degenerate pass 2 (dist={dist})")
    bmesh.ops.dissolve_degenerate(bm, dist=dist, edges=bm.edges)

    # Step 5: Re-triangulate to ensure output is a pure triangle mesh after
    # the second dissolve (which can leave n-gons again).
    print("[bmesh_cleanup] Step 5: triangulate pass 2")
    bmesh.ops.triangulate(bm, faces=bm.faces)

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    n_verts_after = len(obj.data.vertices)
    n_faces_after = len(obj.data.polygons)
    print(f"[bmesh_cleanup] After : {n_verts_after} verts, {n_faces_after} faces")
    print(f"[bmesh_cleanup] Removed: {n_faces_before - n_faces_after} face(s), "
          f"{n_verts_before - n_verts_after} vert(s)")

    # --- Export ---
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"[bmesh_cleanup] Exporting PLY to: {out_ply}")
    _export_ply(out_ply)
    print("[bmesh_cleanup] Done.")


if __name__ == "__main__":
    run_cleanup()
