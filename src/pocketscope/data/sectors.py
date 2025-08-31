"""ARTCC sector polygons loader.

Parses a JSON file containing an array of objects with the fields:
    - name: sector name string (e.g., "ZBW37"). Required.
    - points: array of objects with fields {lat: float, lon: float}. Required.

Entries with missing or invalid fields are ignored. Names are trimmed and
uppercased. The resulting sectors are returned as immutable dataclass
instances with a list of (lat, lon) tuples.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

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
        if gtype != "POLYGON":
            continue  # ignore non-polygon for now
        coords = geom.get("coordinates")
        if not isinstance(coords, list) or not coords:
            continue
        # Polygon coordinates: [ [ [lon, lat], ... ] , [holes...] ]
        ring = coords[0]
        if not isinstance(ring, list):
            continue
        pts: list[tuple[float, float]] = []
        for pt in ring:
            if (
                isinstance(pt, (list, tuple))
                and len(pt) >= 2
                and isinstance(pt[0], (int, float))
                and isinstance(pt[1], (int, float))
            ):
                lon = float(pt[0])
                lat = float(pt[1])
                pts.append((lat, lon))
        if len(pts) < 3:
            continue
        props = (
            feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
        )
        nm_raw = None
        if isinstance(props, dict):
            nm_raw = props.get("SECTOR") or props.get("IDENT") or props.get("NAME")
        name = _coerce_name(nm_raw) or "SECTOR"
        out.append(Sector(name=name, points=pts))
    return out


def load_sectors_json(path: str) -> list[Sector]:
    """
    Parse JSON array of objects with fields:
      name: str
      points: array of {lat:float, lon:float}
    Normalize name to uppercase. Ignore invalid entries.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Support both simple array format and GeoJSON FeatureCollection
    # Try GeoJSON first
    gj = _parse_geojson(data)
    if gj:
        return gj

    out: list[Sector] = []
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
    return out
