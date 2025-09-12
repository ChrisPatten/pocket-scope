"""ARTCC sector polygons loader.

Parses a JSON file containing either:
    - A simple array of objects with fields ``name`` (str) and ``points``
        (array of ``{lat: float, lon: float}``).
    - A GeoJSON ``FeatureCollection`` with Polygon or MultiPolygon geometries.

For GeoJSON, Polygon outer rings and all MultiPolygon parts are supported.
Holes are currently ignored. Each MultiPolygon part is emitted as an
independent ``Sector`` with the same ``name`` so our renderer can draw
disjoint outlines.

Entries with missing or invalid fields are ignored. Names are trimmed and
uppercased. The resulting sectors are returned as immutable dataclass
instances with a list of (lat, lon) tuples.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite

from pocketscope.core.geo import haversine_nm

__all__ = ["Sector", "load_sectors_json"]


@dataclass(frozen=True)
class Sector:
    name: str
    points: list[tuple[float, float]]  # [(lat, lon), ...]


def _coerce_name(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    return s if s else None


def _coerce_point(obj: object) -> tuple[float, float] | None:
    if not isinstance(obj, dict):
        return None
    try:
        lat = float(obj.get("lat"))  # type: ignore[arg-type]
        lon = float(obj.get("lon"))  # type: ignore[arg-type]
    except Exception:
        return None
    return (lat, lon)


def _parse_geojson(data: object) -> list[Sector]:
    out: list[Sector] = []
    if not isinstance(data, dict):
        return out
    if str(data.get("type")).upper() != "FEATURECOLLECTION":
        return out
    feats = data.get("features")
    if not isinstance(feats, list):
        return out
    for feat in feats:
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry")
        if not isinstance(geom, dict):
            continue
        gtype = str(geom.get("type")).upper()
        coords = geom.get("coordinates")
        if not isinstance(coords, list) or not coords:
            continue

        # Get a display name from common property keys
        props = (
            feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
        )
        nm_raw = None
        if isinstance(props, dict):
            nm_raw = props.get("SECTOR") or props.get("IDENT") or props.get("NAME")
        name = _coerce_name(nm_raw) or "SECTOR"

        def _ring_to_points(ring_obj: object) -> list[tuple[float, float]]:
            """Convert a GeoJSON linear ring (lon,lat pairs) to [(lat,lon)]."""
            pts_: list[tuple[float, float]] = []
            if isinstance(ring_obj, list):
                for pt in ring_obj:
                    if (
                        isinstance(pt, (list, tuple))
                        and len(pt) >= 2
                        and isinstance(pt[0], (int, float))
                        and isinstance(pt[1], (int, float))
                    ):
                        lon = float(pt[0])
                        lat = float(pt[1])
                        pts_.append((lat, lon))
            return pts_

        if gtype == "POLYGON":
            # Polygon: [ [ [lon,lat], ... ] , [holes...] ]
            ring = coords[0]
            pts = _ring_to_points(ring)
            if len(pts) >= 3:
                out.append(Sector(name=name, points=pts))
        elif gtype == "MULTIPOLYGON":
            # MultiPolygon: [ [ [ [lon,lat],... ] , [holes...] ] , ... ]
            for poly in coords:
                if not isinstance(poly, list) or not poly:
                    continue
                ring = poly[0]
                pts = _ring_to_points(ring)
                if len(pts) >= 3:
                    out.append(Sector(name=name, points=pts))
        else:
            # Ignore non-Polygon geometries for now
            continue
    return out


def load_sectors_json(
    path: str,
    *,
    center_lat: float | None = None,
    center_lon: float | None = None,
    range_nm: float | None = None,
    cull_factor: float = 2.0,
) -> list[Sector]:
    """
    Parse JSON array of objects with fields:
      name: str
      points: array of {lat:float, lon:float}
    Normalize name to uppercase. Ignore invalid entries.

    Optional viewport filtering
    ---------------------------
    If center_lat, center_lon and range_nm are provided, only return sectors
    that are at least partially visible, using the same heuristic as the
    renderer: keep a sector if any of its vertices is within
    (cull_factor * range_nm) NM of the center (default 2x range).
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Support both simple array format and GeoJSON FeatureCollection
    # Try GeoJSON first
    gj = _parse_geojson(data)
    out: list[Sector] = []
    if gj:
        out = gj
    else:
        if not isinstance(data, list):
            return out

        for item in data:
            if not isinstance(item, dict):
                continue
            name = _coerce_name(item.get("name"))
            pts_in = item.get("points")
            if name is None or not isinstance(pts_in, list):
                continue
            pts: list[tuple[float, float]] = []
            for p in pts_in:
                pt = _coerce_point(p)
                if pt is not None:
                    pts.append(pt)
            # Require at least 3 vertices to form a polygon boundary
            if name and len(pts) >= 3:
                out.append(Sector(name=name, points=pts))

    # Optional viewport filtering
    if (
        center_lat is not None
        and center_lon is not None
        and range_nm is not None
        and isfinite(center_lat)
        and isfinite(center_lon)
        and isfinite(range_nm)
        and range_nm > 0
        and cull_factor > 0
    ):
        max_nm = float(range_nm) * float(cull_factor)
        out = [
            s
            for s in out
            if any(
                haversine_nm(center_lat, center_lon, lat, lon) <= max_nm
                for (lat, lon) in s.points
            )
        ]

    return out
