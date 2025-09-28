from __future__ import annotations

import math

from pocketscope.data.spatial import approx_bbox_for_radius, haversine_miles


def test_haversine_miles_accuracy() -> None:
    """Great-circle distance between BOS and JFK is ~187.1 miles."""
    dist = haversine_miles(42.3656, -71.0096, 40.6413, -73.7781)
    assert math.isclose(dist, 186.3, abs_tol=0.1)


def test_approx_bbox_contains_radius() -> None:
    lat, lon = 42.0, -71.0
    radius = 50.0
    minx, miny, maxx, maxy = approx_bbox_for_radius(lat, lon, radius)
    miles_per_deg_lat = 69.0
    delta_lat = radius / miles_per_deg_lat
    delta_lon = radius / (miles_per_deg_lat * max(math.cos(math.radians(lat)), 1e-6))
    assert lon - delta_lon >= minx - 1e-6
    assert lon + delta_lon <= maxx + 1e-6
    assert lat - delta_lat >= miny - 1e-6
    assert lat + delta_lat <= maxy + 1e-6
