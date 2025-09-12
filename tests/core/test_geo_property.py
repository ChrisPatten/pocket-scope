from __future__ import annotations

from math import cos, radians, sin

import hypothesis.strategies as st
from hypothesis import assume, given, settings

from pocketscope.core.geo import (
    dest_point,
    ecef_to_enu,
    geodetic_to_ecef,
    haversine_nm,
    initial_bearing_deg,
    range_bearing_from,
)

EPS_NM = 1e-3
EPS_DEG = 1e-3


def angle_diff(a: float, b: float) -> float:
    d = (a - b) % 360.0
    if d > 180.0:
        d -= 360.0
    return abs(d)


lat_strat = st.floats(
    min_value=-89.9, max_value=89.9, allow_nan=False, allow_infinity=False
)
lon_strat = st.floats(
    min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False
)
brg_strat = st.floats(
    min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False
)
rng_nm_strat = st.floats(
    min_value=0.0, max_value=2000.0, allow_nan=False, allow_infinity=False
)


@settings(deadline=None, max_examples=120)
@given(
    lat1=lat_strat,
    lon1=lon_strat,
    lat2=lat_strat,
    lon2=lon_strat,
)
def test_symmetry(lat1: float, lon1: float, lat2: float, lon2: float) -> None:
    d1 = haversine_nm(lat1, lon1, lat2, lon2)
    d2 = haversine_nm(lat2, lon2, lat1, lon1)
    assert abs(d1 - d2) <= 1e-9


@settings(deadline=None, max_examples=100)
@given(
    lat1=lat_strat,
    lon1=lon_strat,
    lat2=lat_strat,
    lon2=lon_strat,
    lat3=lat_strat,
    lon3=lon_strat,
)
def test_triangle_inequality(
    lat1: float, lon1: float, lat2: float, lon2: float, lat3: float, lon3: float
) -> None:
    d12 = haversine_nm(lat1, lon1, lat2, lon2)
    d23 = haversine_nm(lat2, lon2, lat3, lon3)
    d13 = haversine_nm(lat1, lon1, lat3, lon3)
    assume(d13 < 10800)
    assert d13 <= d12 + d23 + 1e-6


@settings(deadline=None, max_examples=120)
@given(lat=lat_strat, lon=lon_strat, brg=brg_strat, rng=rng_nm_strat)
def test_forward_inverse_consistency(
    lat: float, lon: float, brg: float, rng: float
) -> None:
    lat2, lon2 = dest_point(lat, lon, brg, rng)
    r, b = range_bearing_from(lat, lon, lat2, lon2)
    assert abs(r - rng) < 1e-2  # within ~18 m for long ranges
    # Bearing is undefined at zero distance and numerically unstable at
    # extremely small ranges; only check when there is meaningful separation.
    # 1e-6 nm ≈ 1.85 mm.
    if rng > 1e-6:
        assert angle_diff(b, brg) <= 1e-2


@settings(deadline=None, max_examples=120)
@given(
    lat=lat_strat,
    lon=lon_strat,
    brg=brg_strat,
    rng=st.floats(min_value=0.0, max_value=5.0),
)
def test_enu_small_angle_consistency(
    lat: float, lon: float, brg: float, rng: float
) -> None:
    # Small range approximation in ENU should match spherical small-angle
    lat2, lon2 = dest_point(lat, lon, brg, rng)
    x, y, z = geodetic_to_ecef(lat2, lon2, 0.0)
    e, n, _u = ecef_to_enu(x, y, z, lat, lon, 0.0)

    rng_m = rng * 1852.0
    ex = rng_m * sin(radians(brg))
    ny = rng_m * cos(radians(brg))

    # 1% relative tolerance; add small absolute slack for zero
    def close(a: float, b: float) -> bool:
        scale = max(1.0, abs(b))
        return abs(a - b) <= 0.01 * scale + 0.01

    assert close(e, ex)
    assert close(n, ny)


@settings(deadline=None, max_examples=120)
@given(
    lat1=lat_strat,
    lon1=lon_strat,
    lat2=lat_strat,
    lon2=lon_strat,
    k=st.integers(min_value=-2, max_value=2),
)
def test_longitude_wrap_invariance(
    lat1: float, lon1: float, lat2: float, lon2: float, k: int
) -> None:
    # Add multiples of 360 to longitudes and confirm invariance
    lon1w = lon1 + 360.0 * k
    lon2w = lon2 - 360.0 * k
    d1 = haversine_nm(lat1, lon1, lat2, lon2)
    b1 = initial_bearing_deg(lat1, lon1, lat2, lon2)
    d2 = haversine_nm(lat1, lon1w, lat2, lon2w)
    b2 = initial_bearing_deg(lat1, lon1w, lat2, lon2w)
    assert abs(d1 - d2) < 1e-9
    if d1 > 1e-9:
        assert angle_diff(b1, b2) < 1e-7
