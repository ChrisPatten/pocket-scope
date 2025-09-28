"""Spatial math and geometry helpers for map data access.

Provides lightweight haversine distance in statute miles, bounding box
approximations for radial queries, and minimal GeoJSON <-> WKB conversion
helpers so we can persist geometries in SQLite without external dependencies.
"""
from __future__ import annotations

import math
import sqlite3
import struct
from typing import Any, Iterator, List, Mapping, Sequence, Tuple

EARTH_RADIUS_MILES = 3958.7613  # mean earth radius in miles
_LAT_DEG_MILES = 69.0  # approximate miles per degree latitude


class GeometryError(Exception):
    """Raised when geometry cannot be parsed or encoded."""


def _extract_lon_lat(coord: Sequence[Any] | None) -> tuple[float, float]:
    """Extract longitude and latitude from a coordinate, handling 2D and 3D cases."""
    if coord is None:
        raise GeometryError("Coordinate cannot be None")
    if not isinstance(coord, (list, tuple)) or len(coord) < 2:
        raise GeometryError(f"Coordinate must have at least 2 values, got: {coord}")
    return float(coord[0]), float(coord[1])


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance between two WGS-84 points in miles."""
    if lat1 == lat2 and lon1 == lon2:
        return 0.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(_normalize_lon(lon2) - _normalize_lon(lon1))

    sin_dphi = math.sin(dphi / 2.0)
    sin_dlambda = math.sin(dlambda / 2.0)
    a = (
        sin_dphi * sin_dphi
        + math.cos(phi1) * math.cos(phi2) * sin_dlambda * sin_dlambda
    )
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_MILES * c


def approx_bbox_for_radius(
    lat: float, lon: float, radius_miles: float
) -> tuple[float, float, float, float]:
    """Return a loose lon/lat bounding box enclosing a radius around (lat, lon)."""
    lat_extent = (radius_miles / _LAT_DEG_MILES) * 1.05
    cos_lat = math.cos(math.radians(lat))
    # Avoid division by zero near the poles; fall back to latitude extent.
    lon_extent = radius_miles / (_LAT_DEG_MILES * max(cos_lat, 1e-6))
    lon_extent *= 1.05
    min_lat = lat - lat_extent
    max_lat = lat + lat_extent
    min_lon = lon - lon_extent
    max_lon = lon + lon_extent
    return (min_lon, min_lat, max_lon, max_lat)


def register_haversine(conn: sqlite3.Connection) -> None:
    """Register the HAV_MILES function on a sqlite3 connection."""
    conn.create_function(
        "HAV_MILES",
        4,
        lambda lat1, lon1, lat2, lon2: haversine_miles(lat1, lon1, lat2, lon2),
    )


def point_to_wkb(lon: float, lat: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, float(lon), float(lat))


def polygon_to_wkb(rings: Sequence[Sequence[Sequence[float]]]) -> bytes:
    payload = bytearray()
    payload.extend(struct.pack("<BI", 1, 3))
    payload.extend(struct.pack("<I", len(rings)))
    for ring in rings:
        payload.extend(struct.pack("<I", len(ring)))
        for coord in ring:
            lon, lat = _extract_lon_lat(coord)
            payload.extend(struct.pack("<dd", lon, lat))
    return bytes(payload)


def multipolygon_to_wkb(
    polygons: Sequence[Sequence[Sequence[Sequence[float]]]],
) -> bytes:
    payload = bytearray()
    payload.extend(struct.pack("<BI", 1, 6))
    payload.extend(struct.pack("<I", len(polygons)))
    for polygon in polygons:
        sub = polygon_to_wkb(polygon)
        payload.extend(sub)
    return bytes(payload)


def geometry_to_wkb(geometry: Mapping[str, Any]) -> bytes:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if coords is None:
        raise GeometryError(f"Geometry missing coordinates: {geometry}")
    if gtype == "Point":
        lon, lat = _extract_lon_lat(coords)
        return point_to_wkb(lon, lat)
    if gtype == "Polygon":
        rings = _ensure_rings(coords)
        return polygon_to_wkb(rings)
    if gtype == "MultiPolygon":
        polygons = [_ensure_rings(poly) for poly in coords]
        return multipolygon_to_wkb(polygons)
    raise GeometryError(f"Unsupported geometry type: {gtype}")


def bbox_for_geometry(geometry: Mapping[str, Any]) -> tuple[float, float, float, float]:
    points = list(_iter_points(geometry))
    if not points:
        raise GeometryError("Geometry has no coordinates")
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return (min(lons), min(lats), max(lons), max(lats))


def polygon_centroid(rings: Sequence[Sequence[Sequence[float]]]) -> tuple[float, float]:
    if not rings:
        raise GeometryError("Polygon centroid requested for empty polygon")
    # Use only exterior ring for centroid; interior holes cancel by area subtraction.
    ring = rings[0]
    if len(ring) < 3:
        raise GeometryError("Polygon ring must have at least 3 points")
    area = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(ring) - 1):
        x0, y0 = _extract_lon_lat(ring[i])
        x1, y1 = _extract_lon_lat(ring[i + 1])
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area *= 0.5
    if abs(area) < 1e-9:
        xs = [_extract_lon_lat(pt)[0] for pt in ring]
        ys = [_extract_lon_lat(pt)[1] for pt in ring]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cx /= 6.0 * area
    cy /= 6.0 * area
    return (cx, cy)


def wkb_to_geometry(blob: bytes) -> dict[str, Any]:
    geom, _ = _read_wkb(blob, 0)
    return geom


def _ensure_rings(
    obj: Sequence[Sequence[Sequence[object]]],
) -> List[List[Tuple[float, float]]]:
    rings: List[List[Tuple[float, float]]] = []
    for ring in obj:
        pts = []
        for coord in ring:
            lon, lat = _extract_lon_lat(coord)
            pts.append((lon, lat))
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        rings.append(pts)
    return rings


def _iter_points(geometry: Mapping[str, Any]) -> Iterator[Tuple[float, float]]:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if coords is None:
        raise GeometryError(f"Geometry missing coordinates: {geometry}")
    if gtype == "Point":
        lon, lat = _extract_lon_lat(coords)
        yield (lon, lat)
        return
    if gtype == "Polygon":
        for ring in coords:
            for coord in ring:
                lon, lat = _extract_lon_lat(coord)
                yield (lon, lat)
        return
    if gtype == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                for coord in ring:
                    lon, lat = _extract_lon_lat(coord)
                    yield (lon, lat)
        return
    raise GeometryError(f"Unsupported geometry type: {gtype}")


def _normalize_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def _read_wkb(data: bytes, offset: int) -> tuple[dict[str, Any], int]:
    if offset + 5 > len(data):
        raise GeometryError("Incomplete WKB data")
    byte_order = data[offset]
    fmt = "<" if byte_order == 1 else ">"
    offset += 1
    (geom_type,) = struct.unpack_from(f"{fmt}I", data, offset)
    offset += 4
    if geom_type == 1:  # Point
        if offset + 16 > len(data):
            raise GeometryError("Incomplete WKB point")
        x, y = struct.unpack_from(f"{fmt}dd", data, offset)
        offset += 16
        return ({"type": "Point", "coordinates": [x, y]}, offset)
    if geom_type == 3:  # Polygon
        (ring_count,) = struct.unpack_from(f"{fmt}I", data, offset)
        offset += 4
        rings: List[List[List[float]]] = []
        for _ in range(ring_count):
            (point_count,) = struct.unpack_from(f"{fmt}I", data, offset)
            offset += 4
            pts: List[List[float]] = []
            for _ in range(point_count):
                x, y = struct.unpack_from(f"{fmt}dd", data, offset)
                offset += 16
                pts.append([x, y])
            rings.append(pts)
        return ({"type": "Polygon", "coordinates": rings}, offset)
    if geom_type == 6:  # MultiPolygon
        (poly_count,) = struct.unpack_from(f"{fmt}I", data, offset)
        offset += 4
        polys: List[List[List[List[float]]]] = []
        for _ in range(poly_count):
            geom, offset = _read_wkb(data, offset)
            if geom.get("type") != "Polygon":
                raise GeometryError("Invalid MultiPolygon member")
            polys.append(geom["coordinates"])
        return ({"type": "MultiPolygon", "coordinates": polys}, offset)
    raise GeometryError(f"Unsupported WKB geometry type: {geom_type}")
