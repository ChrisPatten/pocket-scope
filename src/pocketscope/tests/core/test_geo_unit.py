from __future__ import annotations

from math import isfinite

from pocketscope.core.geo import (
    dest_point,
    ecef_to_enu,
    geodetic_to_ecef,
    haversine_nm,
    initial_bearing_deg,
    range_bearing_from,
)

EPS_NM = 1e-3  # ~2 m
EPS_DEG = 1e-3
EPS_M = 1e-3


def test_zero_distance_and_bearing() -> None:
    lat = 37.0
    lon = -122.0
    assert haversine_nm(lat, lon, lat, lon) == 0.0
    assert initial_bearing_deg(lat, lon, lat, lon) == 0.0


def test_cardinal_moves_near_equator() -> None:
    lat0, lon0 = 0.0, 0.0

    # 1 NM north
    lat1, lon1 = dest_point(lat0, lon0, 0.0, 1.0)
    r1, b1 = range_bearing_from(lat0, lon0, lat1, lon1)
    assert abs(r1 - 1.0) < 5e-4
    assert abs(b1 - 0.0) < 1e-2

    # 1 NM east
    lat2, lon2 = dest_point(lat0, lon0, 90.0, 1.0)
    r2, b2 = range_bearing_from(lat0, lon0, lat2, lon2)
    assert abs(r2 - 1.0) < 5e-4
    assert abs(b2 - 90.0) < 1e-2


def test_antimeridian_crossing() -> None:
    # Small separation across the antimeridian
    lat1, lon1 = 0.0, 179.9
    lat2, lon2 = 0.0, -179.9
    d_nm = haversine_nm(lat1, lon1, lat2, lon2)
    # Expected ~ 0.2 deg * 60 NM at equator
    expected_nm = 0.2 * 60.0
    assert abs(d_nm - expected_nm) < 0.1


def test_poles_bearing_finite() -> None:
    lat1, lon1 = 89.9, 0.0
    lat2, lon2 = 89.9, 90.0
    d_nm = haversine_nm(lat1, lon1, lat2, lon2)
    assert d_nm > 0.0
    b = initial_bearing_deg(lat1, lon1, lat2, lon2)
    assert isfinite(b)


def test_ecef_enu_roundtrip_origin_zero() -> None:
    # A point projected to ENU at itself should be near zero
    lat, lon, alt = 45.0, 45.0, 123.0
    x, y, z = geodetic_to_ecef(lat, lon, alt)
    e, n, u = ecef_to_enu(x, y, z, lat, lon, alt)
    assert abs(e) < EPS_M
    assert abs(n) < EPS_M
    assert abs(u) < EPS_M
