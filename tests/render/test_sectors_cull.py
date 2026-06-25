"""Equivalence tests for the vectorised sector-cull helper.

Locks ``_any_within_nm`` to the original per-vertex ``haversine_nm`` cull loop
it replaced, so a future change can't silently alter which sectors are drawn.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from pocketscope.core.geo import haversine_nm
from pocketscope.render.geom_np import _any_within_nm


def _ref_cull(
    points: list[tuple[float, float]],
    center_lat: float,
    center_lon: float,
    max_nm: float,
) -> bool:
    """Original scalar cull: keep if any vertex within ``max_nm``."""
    for lat, lon in points:
        if haversine_nm(center_lat, center_lon, lat, lon) <= max_nm:
            return True
    return False


def _make_points(seed: int, cx: float, cy: float, spread: float, n: int):
    rng = np.random.default_rng(seed)
    lats = cx + rng.uniform(-spread, spread, n)
    lons = cy + rng.uniform(-spread, spread, n)
    return [(float(a), float(o)) for a, o in zip(lats, lons)]


@pytest.mark.parametrize("seed", [1, 2, 3, 5, 8])
def test_any_within_matches_scalar(seed: int) -> None:
    center_lat, center_lon = 42.0, -71.1
    # Mix of near, straddling, and far sector clusters at varied spreads.
    clusters = [
        (42.0, -71.1, 0.4, 40),  # right on top of center
        (43.5, -70.0, 0.6, 60),  # a bit away
        (10.0, 10.0, 0.5, 30),  # far away -> culled
        (44.0, -72.0, 2.0, 120),  # large spread straddling the cull radius
    ]
    for cx, cy, spread, n in clusters:
        pts = _make_points(seed, cx, cy, spread, n)
        lat_col = np.array([p[0] for p in pts], dtype=np.float64)
        lon_col = np.array([p[1] for p in pts], dtype=np.float64)
        for range_nm in (10.0, 50.0, 100.0):
            max_nm = 2.0 * range_nm
            got = _any_within_nm(lat_col, lon_col, center_lat, center_lon, max_nm)
            assert got == _ref_cull(pts, center_lat, center_lon, max_nm)


def test_any_within_handles_lon_wrap() -> None:
    """Vertices near the antimeridian must match the scalar normaliser."""
    center_lat, center_lon = 0.0, 179.9
    pts = [(0.0, -179.95), (0.1, 179.99), (0.0, -179.8)]
    lat_col = np.array([p[0] for p in pts], dtype=np.float64)
    lon_col = np.array([p[1] for p in pts], dtype=np.float64)
    for max_nm in (5.0, 15.0, 50.0):
        got = _any_within_nm(lat_col, lon_col, center_lat, center_lon, max_nm)
        assert got == _ref_cull(pts, center_lat, center_lon, max_nm)


def test_any_within_single_vertex_distance() -> None:
    """A lone vertex's keep/drop flips exactly at the haversine radius."""
    center_lat, center_lon = 42.0, -71.0
    # One point ~30 NM north of center.
    far_lat = center_lat + (30.0 * 1852.0) / 111_320.0
    d = haversine_nm(center_lat, center_lon, far_lat, center_lon)
    lat_col = np.array([far_lat], dtype=np.float64)
    lon_col = np.array([center_lon], dtype=np.float64)
    assert _any_within_nm(lat_col, lon_col, center_lat, center_lon, d + 1e-6) is True
    assert _any_within_nm(lat_col, lon_col, center_lat, center_lon, d - 1.0) is False
    assert not math.isnan(d)
