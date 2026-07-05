"""
core/geometry_weights.py
Shared geometric weighting helpers for diffuse-field computation.

Ported verbatim from the sofa_buddy project so both tools compute diffuse-field
weights identically.  Used by generate_extras.py (the DFHRTF worker).  Depends
only on numpy and scipy — no extra import cost beyond what that script already
pays.
"""
import numpy as np
from scipy.spatial import ConvexHull

try:  # scipy >= 1.15 renamed sph_harm -> sph_harm_y (new arg order: l, m, colat, az)
    from scipy.special import sph_harm_y as _sph_harm_y

    def _sh_complex(l, m, col, az):
        return _sph_harm_y(l, abs(m), col, az)
except ImportError:  # older scipy: sph_harm(m, l, azimuth, colat)
    from scipy.special import sph_harm as _sph_harm_old

    def _sh_complex(l, m, col, az):
        return _sph_harm_old(abs(m), l, az, col)


def spherical_to_cartesian(r, az, el):
    """Convert spherical (r, az, el) in degrees to Cartesian (x, y, z)."""
    az_rad = np.radians(az)
    el_rad = np.radians(el)
    x = r * np.cos(el_rad) * np.cos(az_rad)
    y = r * np.cos(el_rad) * np.sin(az_rad)
    z = r * np.sin(el_rad)
    return np.column_stack((x, y, z))


def get_dominant_radius(source_pos):
    """Return the most-common measurement radius from a SourcePosition array."""
    r = source_pos[:, 2]
    radii_rounded = np.round(r, decimals=3)
    values, counts = np.unique(radii_rounded, return_counts=True)
    return values[np.argmax(counts)]


def _is_equal_weight_design(cart_coords, tol=1e-6):
    """Return True iff the points form a genuine spherical t/n-design.

    A spherical design's defining property is that EQUAL (1/N) weighting
    integrates every spherical harmonic up to its design degree exactly.  We
    test that property directly: for a real design the worst residual is ~1e-11,
    while measured/weighted grids (equiangular, Gauss, hemispherical, Lebedev)
    sit at ~1e0 — a clean ~9-order-of-magnitude gap with no overlap, so the
    1e-6 tolerance never produces a borderline call.

    This is provenance-free (no metadata or filename trust required) and
    replaces the old geo-weight-CV heuristic, whose CV for real designs is
    erratic (≈8–16 %) with no threshold that cleanly separates them from
    non-uniform grids.

    The test degree must be ≤ the grid's design degree, and a grid needs at
    least (deg+1)**2 points to be a candidate, so we drop from degree 8 (needs
    ≥81 pts) to 6 (needs ≥49 pts) for small grids such as LISTEN (187 nodes is
    still comfortably above 81, but tiny custom grids are handled gracefully).
    """
    N = len(cart_coords)
    test_order = 8 if N >= 100 else 6
    x, y, z = cart_coords[:, 0], cart_coords[:, 1], cart_coords[:, 2]
    r = np.linalg.norm(cart_coords, axis=1)
    az = np.arctan2(y, x)
    col = np.arccos(np.clip(z / r, -1, 1))
    for l in range(1, test_order + 1):
        for m in range(-l, l + 1):
            Y = _sh_complex(l, m, col, az)
            if m < 0:
                Yr = np.sqrt(2) * (-1) ** m * Y.imag
            elif m > 0:
                Yr = np.sqrt(2) * (-1) ** m * Y.real
            else:
                Yr = Y.real
            if abs(4 * np.pi / N * np.sum(Yr)) > tol:
                return False
    return True


def calculate_geometric_weights(source_pos, advanced_dfeq=True,
                                weighting_mode='auto'):
    """Calculate solid-angle weights for spherical diffuse-field integration.

    Parameters
    ----------
    source_pos : ndarray, shape (M, 3)
        SOFA SourcePosition array — columns are (azimuth °, elevation °, radius m).
    advanced_dfeq : bool
        When True (default), apply data sanitisation before computing weights:
        filter to the dominant measurement radius (±0.05 m) and deduplicate
        angular positions.  Set False only when the source positions are already
        known to be clean.
    weighting_mode : {'auto', 'geometric', 'uniform'}
        'auto'      — compute ghost-node ConvexHull weights, then snap to
                      uniform 1/N if the grid passes a spherical-design
                      quadrature test (integrates every SH up to degree 8 with
                      equal weights to machine precision).  Correct for genuine
                      t/n-designs; non-designs always receive geometric weights.
        'geometric' — always use ghost-node ConvexHull solid-angle weights.
        'uniform'   — always return equal 1/N weights (bypass ConvexHull).

    Returns
    -------
    weights : ndarray, shape (M,)
        Non-negative weights that sum to 1.0.  Points that were removed by the
        sanitisation pass have weight 0.
    """
    az = source_pos[:, 0]
    el = source_pos[:, 1]

    # ── Cleaning prologue ────────────────────────────────────────────────────
    if advanced_dfeq:
        dominant_r = get_dominant_radius(source_pos)

        # Filter out points that deviate significantly from the dominant radius
        valid_idx = np.abs(source_pos[:, 2] - dominant_r) < 0.05

        # Remove duplicate angular positions
        az_el_rounded = np.round(source_pos[valid_idx][:, 0:2], decimals=3)
        _, unique_idx = np.unique(az_el_rounded, axis=0, return_index=True)

        final_idx = np.where(valid_idx)[0][unique_idx]
        az_clean = az[final_idx]
        el_clean = el[final_idx]
        cart_coords = spherical_to_cartesian(1.0, az_clean, el_clean)
    else:
        cart_coords = spherical_to_cartesian(1.0, az, el)
        final_idx = np.arange(len(source_pos))

    # ── Uniform helper ───────────────────────────────────────────────────────
    def _uniform_weights():
        w = np.zeros(len(source_pos))
        w[final_idx] = 1.0 / len(final_idx)
        return w

    mode = weighting_mode.lower()
    if mode == 'uniform':
        print(f"   [Weighting] Uniform (1/{len(final_idx)})")
        return _uniform_weights()

    # ── Ghost-node ConvexHull solid-angle weights ────────────────────────────
    # Augments the real measurement grid with a dense Fibonacci-sphere cap of
    # dummy points placed below the dataset's lowest real point.  This closes
    # the ConvexHull for open/hemispherical grids (e.g. ARI) so that every
    # real measurement node receives a solid-angle weight proportional to its
    # true coverage area rather than benefiting from the artificial "floor"
    # that the bare-hull algorithm creates.
    try:
        # 1. Dense Fibonacci-sphere dummy points
        samples = 1000
        indices = np.arange(0, samples, dtype=float) + 0.5
        phi = np.arccos(1 - 2 * indices / samples)
        theta = np.pi * (1 + 5 ** 0.5) * indices

        gx = np.cos(theta) * np.sin(phi)
        gy = np.sin(theta) * np.sin(phi)
        gz = np.cos(phi)
        ghost_coords = np.column_stack((gx, gy, gz))

        # 2. Keep only ghosts that fall below the real dataset's lowest point
        min_z = np.min(cart_coords[:, 2])
        ghost_coords = ghost_coords[ghost_coords[:, 2] < (min_z - 0.01)]

        # 3. Build hull over the closed (real + ghost) point set
        cart_coords_closed = np.vstack((cart_coords, ghost_coords))
        hull = ConvexHull(cart_coords_closed)

        weights = np.zeros(len(source_pos))
        temp_weights = np.zeros(len(cart_coords_closed))
        for simplex in hull.simplices:
            A, B, C = cart_coords_closed[simplex]
            cross_prod = np.cross(B - A, C - A)
            area = 0.5 * np.linalg.norm(cross_prod)
            temp_weights[simplex] += area / 3.0

        # 4. Strip ghost-node weights; keep only real-node weights
        real_weights = temp_weights[:len(cart_coords)]

        # 5. Map back and normalise
        weights[final_idx] = real_weights
        weights /= np.sum(weights)

    except Exception as e:
        print(f"[Warning] Weight calculation fallback used: {e}")
        weights = np.ones(len(source_pos)) / len(source_pos)

    if mode == 'geometric':
        return weights

    # ── Auto: uniform iff the grid is a genuine spherical design ─────────────
    # A t/n-design's defining property is that EQUAL weights integrate every SH
    # up to its degree exactly; for such grids 1/N is exact and the hull weights
    # computed above only add error.  We test that property directly — it is
    # provenance-free and, unlike the old geo-weight-CV heuristic, has no
    # threshold overlap between designs (~1e-11) and measured grids (~1e0).
    if _is_equal_weight_design(cart_coords):
        print("   [Weighting] Auto -> uniform  "
              "(grid passes spherical-design quadrature test)")
        return _uniform_weights()
    print("   [Weighting] Auto -> geometric (not an equal-weight design)")
    return weights
