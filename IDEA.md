## DFHRTF Outputs
- Add new weighting mode from SOFA Buddy (accounts for measurement grids which lack low elevations)

## SOFA Outputs
- Remove 512 sample checkbox

## Mesh processing
- use bpy/bmesh to run degenerate dissolve on mesh prior to inspect/clean step (setting of .3)
- flow: degenerate dissolve > triangulate > pymeshfix.clean() > cut_and_cap > 