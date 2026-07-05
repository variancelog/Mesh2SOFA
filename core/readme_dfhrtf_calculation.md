# Calculation of DFHRTF in `generate_extras.py`

When calculating a Diffuse Field HRTF (DFHRTF), we average the frequency responses from
every measured direction. But measurement / evaluation grids are rarely spaced perfectly
evenly, so a naïve average lets densely-packed regions dominate sparse ones. To compensate,
`generate_extras.py` assigns each direction a **spatial weight** before averaging.

The weighting logic lives in `core/geometry_weights.py`
(`calculate_geometric_weights`) — a module shared verbatim with the *sofa_buddy* project so
both tools weight grids identically. It has two important improvements over a plain
convex-hull average: **grid-type auto-detection** and a **ghost-node hull** that stays
correct for open / hemispherical grids.

---

## Step 0: Projecting onto a unit sphere

`spherical_to_cartesian` takes each measurement's azimuth and elevation from the SOFA
`SourcePosition` and projects it onto a **unit sphere** (radius forced to `1.0`), so the
weighting depends only on direction, not on the recorded distance.

A short cleaning prologue (on by default, `advanced_dfeq=True`) first filters the grid to
its dominant measurement radius (±0.05 m) and removes duplicate angular positions, so a
stray point or an accidental repeat can't distort the weights. Points removed here receive
weight 0.

## Step 1: Auto-detecting whether weighting is even needed

Some grids are **spherical `t`/`n`-designs** (e.g. the N-design evaluation grids used for
simulation). A design's defining property is that **equal `1/N` weighting integrates every
spherical harmonic up to its design degree exactly** — for such a grid, geometric weights
add error rather than remove it, so the correct answer is simply `1/N`.

`_is_equal_weight_design` tests that property directly: it sums each real spherical harmonic
(up to degree 8, or 6 for small grids) under equal weights and checks the residual.

* A genuine design lands at ~`1e-11`.
* A measured / equiangular / Gauss / Lebedev / hemispherical grid lands at ~`1e0`.

That ~9-order-of-magnitude gap means the `1e-6` tolerance never produces a borderline call,
and the test needs **no filename or metadata trust** — it reads the geometry alone.

The `weighting_mode` argument (`--weighting-mode`, default `auto`) controls this:

| Mode | Behaviour |
|---|---|
| `auto` (default) | Compute geometric weights, then **snap to uniform `1/N` iff the grid passes the design test.** |
| `geometric` | Always use the ghost-node hull weights below. |
| `uniform` | Always return equal `1/N` weights (skip the hull entirely). |

The script logs which branch it took (`Auto -> uniform` or `Auto -> geometric`).

## Step 2: Geometric weights via a ghost-node ConvexHull

For non-design grids, each point's weight should be proportional to the **solid angle** it
covers. The script computes this from a `scipy.spatial.ConvexHull` over the points, summing
one third of each hull triangle's area onto its three corners:

$$Area = \tfrac{1}{2}\,\lVert (\vec{B}-\vec{A}) \times (\vec{C}-\vec{A}) \rVert
\qquad Weight_{point} \mathrel{+}= \frac{Area}{3}$$

**The ghost-node trick (why hemispherical grids now work).** A bare hull over an *open* grid
(e.g. one with no measurements below the ears) closes itself with a flat artificial "floor",
which wrongly inflates the weights of the rim points. To prevent this, the code first adds a
dense **Fibonacci-sphere cap of dummy "ghost" points** placed just below the lowest real
point, builds the hull over the closed (real + ghost) set, computes areas, then **strips the
ghost weights back out**. Each real node is left with a solid-angle weight proportional to
its *true* coverage area. Finally the weights are normalised to sum to 1.

## Step 3: Frontal bias (optional, applied on top)

If `--front_bias > 0` (and not `--squigify`), `generate_extras.py` multiplies the resulting
weights by `((1 + x)/2) ** front_bias`, where `x` is the forward component of each direction,
then re-normalises. This emphasises frontal directions in the diffuse-field average. It is a
Mesh2SOFA-side post-step and is deliberately kept out of the shared weighting module.
