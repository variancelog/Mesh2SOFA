# Evaluation Grids

Evaluation grids for Mesh2HRTF, in Mesh2HRTF's own format (one folder per grid with `Nodes.txt` and `Elements.txt`). The evaluation grid sets the directions (and distance) at which the HRTF is calculated, and so the source positions in the final SOFA files.

## Using a grid

Mesh2SOFA lists the grids found in your Mesh2HRTF installation, not in this folder. `Default` and `ARI` already ship with Mesh2HRTF (they are here only for their previews). To make one of the other grids selectable:

1. Copy its folder (for example `N-Design_56/`) into `<Mesh2HRTF>/mesh2hrtf/Mesh2Input/EvaluationGrids/Data/`.
2. In the main GUI, choose it under **Project Settings > Evaluation Grid(s)**.
3. Export from Blender (a grid change needs a fresh export).

## Grids in this folder

All values are read from the `Nodes.txt` files. Radius is the distance of the points from the head centre.

| Grid | Points | Radius | Elevation range | Coverage |
|---|---|---|---|---|
| `Default` | 1850 | 1.5 m | -90° to 90° | Full sphere |
| `ARI` | 1550 | 1.2 m | -30° to 80° | Partial |
| `COAT-AACHEN_3072` | 3072 | 1.5 m | -70° to 88.7° | Partial |
| `HUTUBS_440` | 440 | 1.5 m | -90° to 90° | Full sphere |
| `RIEC_865` | 865 | 1.5 m | -30° to 90° | Partial |
| `SS2_1625` | 1625 | 1.5 m | -69° to 90° | Partial |
| `N-Design_30` | 480 | 1.5 m | -83.4° to 83.4° | Full sphere (spherical design) |
| `N-Design_56` | 1620 | 1.5 m | -86.5° to 86.5° | Full sphere (spherical design) |
| `T-Design_21` | 240 | 1.5 m | -82° to 82° | Full sphere (spherical design) |

The spherical designs spread their points evenly over the whole sphere, so a plain average over all directions is already a correct diffuse-field average. The other grids follow the measurement positions of existing HRTF datasets, which is useful for comparing a simulated HRTF with a measured one direction by direction.

Each folder also holds a `<grid>.png` preview, with the points coloured by their diffuse-field weight.

### `source_map.txt`

Grids rebuilt from a dataset's SOFA file include a `source_map.txt` with one line per node:

```
NodeID sofa_index az el r_original
```

It links each grid node to the source position index in the original SOFA file, with that position's azimuth, elevation and original radius (the grid itself may use a different radius; see the table).

## Source datasets

The dataset-named grids reproduce the source positions of published HRTF databases, so a simulated HRTF can be compared with the measured ones direction by direction. Counts and coverage below are for the grid in this folder.

* **`ARI`** — [ARI HRTF database](https://www.oeaw.ac.at/en/ari/outreach/software/hrtf-database), Acoustics Research Institute, Austrian Academy of Sciences. 1550 directions at 1.2 m, elevations -30° to 80°. This grid ships with Mesh2HRTF.
* **`COAT-AACHEN_3072`** — [COAT, Cross-site Oldenburg-Aachener transfer-functions](https://zenodo.org/records/4556707) (Kohnen, Denk, Llorca-Bofí, Kollmeier, Vorländer; RWTH Aachen and Universität Oldenburg). HRTFs and headphone transfer functions of 27 subjects, 14 of them measured at both sites. This grid follows the Aachen measurements: 3072 directions, elevations -70° to 88.7°. Licence: CC BY 4.0.
* **`HUTUBS_440`** — [HUTUBS HRTF database](https://depositonce.tu-berlin.de/items/dc2a3076-a291-417e-97f0-7697e332c960) (Brinkmann et al., TU Berlin, 2019). 96 subjects, with HRIRs, headphone responses, anthropometric measures and 3D head meshes. 440 directions over the full sphere, measured at 1.47 m. Licence: CC BY 4.0.
* **`RIEC_865`** — [RIEC HRTF Dataset](https://www.riec.tohoku.ac.jp/pub/hrtf/), Research Institute of Electrical Communication, Tohoku University. 105 subjects (210 ears), 865 directions in 5° azimuth and 10° elevation steps from -30° to 90°. Free for research, academic and educational use; see the page for the licence terms and the required citation (Watanabe et al., Acoust. Sci. & Tech. 35(3), 2014).
* **`SS2_1625`** — [SS2 HRTF Dataset](https://facebookresearch.github.io/SS2_HRTF/) (Meta, `facebookresearch`). 1625 directions, elevations -69° to 90°, measured at 2 m.

All grids here except `ARI` are placed at 1.5 m; the dataset's own distance is the `r_original` column of `source_map.txt`.

How the weights are calculated is described in [`core/readme_dfhrtf_calculation.md`](../core/readme_dfhrtf_calculation.md).
