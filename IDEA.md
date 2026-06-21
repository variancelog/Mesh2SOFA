## DFHRTF Outputs
- Add new weighting mode from SOFA Buddy (accounts for measurement grids which lack low elevations)

## SOFA Outputs
- Remove 512 sample checkbox

## Mesh processing
- use bpy/bmesh to run degenerate dissolve on mesh prior to inspect/clean step (setting of .3)
- DONE: flow: degenerate dissolve > triangulate > pymeshfix.clean() > cut_and_cap > 
- After initial cleaning, some small triangles are still left. opening inspect and fix to go to loop cutter warns you but doesn't fix the small triangles?