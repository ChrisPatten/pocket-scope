"""Vectorised boundary-geometry helpers shared by map overlay layers.

These NumPy fast paths back both the state-boundary rendering in
``view_ppi`` and the airspace-sector rendering in ``sectors_layer``. Keeping
them in one module means both layers cull and simplify identically and the
equivalence tests only have to lock a single implementation.

All helpers degrade gracefully: callers should check ``_np is not None`` and
fall back to their scalar paths when NumPy is unavailable.
"""

from __future__ import annotations

from typing import Any, List, Tuple

from pocketscope.core.geo import geodetic_to_enu_batch

try:  # NumPy powers the vectorised boundary-geometry fast paths.
    import numpy as _np
except Exception:  # pragma: no cover - numpy is a core dependency
    _np = None  # type: ignore[assignment]

# Re-export the (underscore-prefixed) helpers explicitly so ``--strict`` mypy
# allows other modules to import them without an implicit-reexport error.
__all__ = [
    "_np",
    "_ring_visible_np",
    "_rdp_keep_mask_np",
    "_simplify_ring_np",
    "_any_within_nm",
]

# Spherical constants mirroring ``pocketscope.core.geo.haversine_nm`` so the
# vectorised cull below agrees with the scalar helper to within float noise.
_EARTH_RADIUS_SPHERE_M = 6371000.0
_M_PER_NM = 1852.0


def _ring_visible_np(e: Any, n: Any, r2: float) -> bool:
    """Vectorised test: is any part of a boundary ring within the view radius?

    ``e``/``n`` are ENU vertex coordinates (meters, center origin). Mirrors the
    original scalar three-phase test: (a) any vertex inside radius, (b) any edge
    passes within radius of the origin, (c) origin lies inside the polygon
    (ray cast along +east). ``r2`` is the squared radius in meters^2.
    """
    if _np.any((e * e + n * n) <= r2):
        return True
    x1, y1 = e, n
    x2, y2 = _np.roll(e, -1), _np.roll(n, -1)
    dx = x2 - x1
    dy = y2 - y1
    seg2 = dx * dx + dy * dy
    with _np.errstate(divide="ignore", invalid="ignore"):
        t = -(x1 * dx + y1 * dy) / seg2
    t = _np.where(seg2 <= 1e-12, 0.0, _np.clip(t, 0.0, 1.0))
    px = x1 + t * dx
    py = y1 + t * dy
    if _np.any((px * px + py * py) <= r2):
        return True
    cond = ((y1 <= 0) & (0 < y2)) | ((y2 <= 0) & (0 < y1))
    with _np.errstate(divide="ignore", invalid="ignore"):
        x_int = x1 + (0.0 - y1) * (x2 - x1) / (y2 - y1)
    crossings = int(_np.count_nonzero(cond & (x_int >= 0)))
    return (crossings % 2) == 1


def _rdp_keep_mask_np(e: Any, n: Any, tol: float) -> Any:
    """Vectorised iterative Ramer-Douglas-Peucker; returns a boolean keep mask.

    Equivalent to the recursive variant: the kept-vertex set is identical for a
    given tolerance. The per-segment perpendicular-distance computation is
    vectorised over all candidate points so each split is O(span) in C, not
    Python. ``tol`` is in meters.
    """
    m = int(e.shape[0])
    keep = _np.zeros(m, dtype=bool)
    if m == 0:
        return keep
    keep[0] = True
    keep[-1] = True
    import math as _math

    stack: list[tuple[int, int]] = [(0, m - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        ax, ay = float(e[i0]), float(n[i0])
        bx, by = float(e[i1]), float(n[i1])
        seg_len = _math.hypot(bx - ax, by - ay)
        sx = e[i0 + 1 : i1]
        sy = n[i0 + 1 : i1]
        if seg_len == 0.0:
            d = _np.hypot(sx - ax, sy - ay)
        else:
            cross = _np.abs((bx - ax) * (ay - sy) - (ax - sx) * (by - ay))
            d = cross / seg_len
        k = int(_np.argmax(d))
        if float(d[k]) > tol:
            idx = i0 + 1 + k
            keep[idx] = True
            stack.append((i0, idx))
            stack.append((idx, i1))
    return keep


def _simplify_ring_np(
    ring: List[Tuple[float, float]],
    lat_col: Any,
    lon_col: Any,
    dyn_factor: float,
    m_per_px: float,
    base_px: float,
) -> List[Tuple[float, float]]:
    """Vectorised adaptive RDP simplification of a boundary ring.

    Mirrors the original scalar implementation byte-for-byte: same adaptive
    pixel tolerance (scaled by ``dyn_factor`` and shrunk for small on-screen
    extents), same ENU projection origin (the ring's first vertex), same RDP
    tolerance, and the same minimum-retention rule. ``ring`` is the list of
    (lat, lon) vertices; ``lat_col``/``lon_col`` are the matching NumPy columns.
    """
    import math as _math

    n_pts = len(ring)
    if n_pts < 6:
        return ring
    px_tol = base_px * dyn_factor
    min_lat = float(lat_col.min())
    max_lat = float(lat_col.max())
    min_lon = float(lon_col.min())
    max_lon = float(lon_col.max())
    try:
        lat_mid = (min_lat + max_lat) * 0.5
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * _math.cos(_math.radians(lat_mid))
        est_w_m = max(1.0, (max_lon - min_lon) * m_per_deg_lon)
        est_h_m = max(1.0, (max_lat - min_lat) * m_per_deg_lat)
        est_max_dim_px = max(est_w_m, est_h_m) / m_per_px
        if est_max_dim_px < 80:
            scale = max(0.15, est_max_dim_px / 80.0)
            px_tol *= scale
        if est_max_dim_px < 30:
            px_tol *= 0.5
    except Exception:
        pass
    px_tol = max(0.2, min(px_tol, base_px * 8.0))
    tol_m = px_tol * m_per_px

    lat0, lon0 = ring[0]
    e_l, n_l = geodetic_to_enu_batch(lat_col, lon_col, lat0, lon0)
    keep_mask = _rdp_keep_mask_np(e_l, n_l, tol_m)
    keep_idx = [int(i) for i in _np.nonzero(keep_mask)[0]]

    min_keep = min(12, max(3, int(n_pts * 0.4)))
    if len(keep_idx) < min_keep:
        return ring
    if len(keep_idx) >= 3 and len(keep_idx) < n_pts:
        return [ring[i] for i in keep_idx]
    return ring


def _any_within_nm(lats: Any, lons: Any, lat0: float, lon0: float, max_nm: float) -> bool:
    """Vectorised "is any vertex within ``max_nm`` of (lat0, lon0)?" cull.

    Mirrors :func:`pocketscope.core.geo.haversine_nm` (spherical, R=6,371 km,
    longitudes normalised to [-180, 180) without rounding) evaluated over every
    vertex at once, returning the same keep/drop decision as the original
    per-vertex scalar loop but in a single NumPy pass.
    """
    phi0 = _np.radians(lat0)
    phi = _np.radians(lats)
    dphi = phi - phi0
    lon0n = (lon0 + 180.0) % 360.0 - 180.0
    lonsn = (_np.asarray(lons, dtype=_np.float64) + 180.0) % 360.0 - 180.0
    dlam = _np.radians(lonsn - lon0n)
    sdphi = _np.sin(dphi * 0.5)
    sdl = _np.sin(dlam * 0.5)
    a = sdphi * sdphi + _np.cos(phi0) * _np.cos(phi) * sdl * sdl
    a = _np.clip(a, 0.0, 1.0)
    c = 2.0 * _np.arcsin(_np.sqrt(a))
    d_nm = (_EARTH_RADIUS_SPHERE_M * c) / _M_PER_NM
    return bool(_np.any(d_nm <= max_nm))
