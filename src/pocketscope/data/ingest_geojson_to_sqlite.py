"""Ingest GeoJSON feature collections into a spatially indexed SQLite DB."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence, Tuple

from .spatial import (
    GeometryError,
    _extract_lon_lat,
    bbox_for_geometry,
    geometry_to_wkb,
    polygon_centroid,
)

SCHEMA_FILE = Path(__file__).with_name("schema.sql")
BATCH_SIZE = 256


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    # argparse wants a Sequence[str] | None; accept a general Iterable and
    # convert it to a list when provided.
    seq: Sequence[str] | None
    if argv is None:
        seq = None
    else:
        seq = list(argv)

    parser = argparse.ArgumentParser(
        description="Build PocketScope map SQLite database from GeoJSON assets."
    )
    parser.add_argument("--airports", required=True, help="Path to airports GeoJSON")
    parser.add_argument("--runways", required=True, help="Path to runways GeoJSON")
    parser.add_argument("--states", required=True, help="Path to US states GeoJSON")
    parser.add_argument("--out", required=True, help="Output SQLite database path")
    parser.add_argument(
        "--replace", action="store_true", help="Replace existing DB file if present"
    )
    return parser.parse_args(seq)


def main(argv: Iterable[str] | None = None) -> int:
    # argparse.parse_args will convert a provided iterable to a sequence;
    # our parse_args helper already handles Iterable inputs, so just forward.
    args = parse_args(argv)
    out_path = Path(args.out).expanduser()
    if args.replace and out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(out_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    with SCHEMA_FILE.open("r", encoding="utf-8") as fh:
        conn.executescript(fh.read())

    ingest_airports(conn, Path(args.airports))
    ingest_runways(conn, Path(args.runways))
    ingest_states(conn, Path(args.states))

    conn.commit()
    conn.execute("ANALYZE;")
    conn.close()
    return 0


def ingest_airports(conn: sqlite3.Connection, path: Path) -> None:
    sql = """
        INSERT OR REPLACE INTO airports (
            id, ident, name, icao_id, type_code, servcity, state, country, elevation_ft,
            lat, lon, geom_wkb, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    batch: list[Tuple[object, ...]] = []
    for feature in iter_geojson_features(path):
        geom = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            continue
        lon, lat = _extract_lon_lat(coords)
        try:
            wkb = geometry_to_wkb({"type": "Point", "coordinates": [lon, lat]})
            bbox_minx, bbox_miny, bbox_maxx, bbox_maxy = lon, lat, lon, lat
        except GeometryError:
            continue
        row = (
            str(props.get("GLOBAL_ID")) if props.get("GLOBAL_ID") is not None else None,
            _clean_text(props.get("IDENT")),
            _clean_text(props.get("NAME")),
            _clean_text(props.get("ICAO_ID")),
            _clean_text(props.get("TYPE_CODE")),
            _clean_text(props.get("SERVCITY")),
            _clean_text(props.get("STATE")),
            _clean_text(props.get("COUNTRY")),
            _safe_float(props.get("ELEVATION")),
            lat,
            lon,
            wkb,
            bbox_minx,
            bbox_miny,
            bbox_maxx,
            bbox_maxy,
        )
        if row[0] is None:
            continue
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)
    # Log how many airports were loaded
    cursor = conn.execute("SELECT COUNT(*) FROM airports")
    count = cursor.fetchone()[0]
    print(f"Loaded {count} airports")


def ingest_runways(conn: sqlite3.Connection, path: Path) -> None:
    # Create a set of valid airport IDs for faster lookup
    cursor = conn.execute("SELECT id FROM airports")
    valid_airport_ids = {row[0] for row in cursor.fetchall()}
    print(f"Found {len(valid_airport_ids)} airports for runway validation")

    sql = """
        INSERT OR REPLACE INTO runways (
            id, airport_id, designator, length_ft, width_ft, surface,
            light_actv, light_intns, geom_wkb, centroid_lat, centroid_lon,
            bbox_minx, bbox_miny, bbox_maxx, bbox_maxy
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    batch: list[Tuple[object, ...]] = []
    total_features = 0
    skipped_geometry = 0
    skipped_geometry_error = 0
    skipped_missing_ids = 0
    skipped_invalid_airport = 0

    for feature in iter_geojson_features(path):
        total_features += 1
        geom = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        gtype = geom.get("type")
        if gtype not in {"Polygon", "MultiPolygon"}:
            skipped_geometry += 1
            continue
        try:
            wkb = geometry_to_wkb(geom)
            bbox_minx, bbox_miny, bbox_maxx, bbox_maxy = bbox_for_geometry(geom)
        except GeometryError:
            skipped_geometry_error += 1
            continue
        centroid_lat = centroid_lon = None
        try:
            if gtype == "Polygon":
                rings = geom.get("coordinates") or []
                cx, cy = polygon_centroid(rings)
                centroid_lon, centroid_lat = cx, cy
            elif gtype == "MultiPolygon":
                first_poly = geom.get("coordinates") or []
                if first_poly:
                    cx, cy = polygon_centroid(first_poly[0])
                    centroid_lon, centroid_lat = cx, cy
        except GeometryError:
            pass
        if centroid_lat is None or centroid_lon is None:
            centroid_lon = (bbox_minx + bbox_maxx) / 2.0
            centroid_lat = (bbox_miny + bbox_maxy) / 2.0
        row = (
            _clean_text(props.get("GLOBAL_ID")),
            _clean_text(props.get("AIRPORT_ID")),
            _clean_text(props.get("DESIGNATOR")),
            _safe_int(props.get("LENGTH")),
            _safe_int(props.get("WIDTH")),
            _clean_text(props.get("COMP_CODE")),
            _safe_int(props.get("LIGHTACTV")),
            _clean_text(props.get("LIGHTINTNS")),
            wkb,
            centroid_lat,
            centroid_lon,
            bbox_minx,
            bbox_miny,
            bbox_maxx,
            bbox_maxy,
        )
        if row[0] is None or row[1] is None:
            skipped_missing_ids += 1
            continue
        # Skip runways that reference non-existent airports
        if row[1] not in valid_airport_ids:
            skipped_invalid_airport += 1
            continue
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)

    # Log detailed statistics
    cursor = conn.execute("SELECT COUNT(*) FROM runways")
    count = cursor.fetchone()[0]
    print("Runway processing stats:")
    print(f"  Total features processed: {total_features}")
    print(f"  Skipped - wrong geometry type: {skipped_geometry}")
    print(f"  Skipped - geometry error: {skipped_geometry_error}")
    print(f"  Skipped - missing required IDs: {skipped_missing_ids}")
    print(f"  Skipped - invalid airport reference: {skipped_invalid_airport}")
    print(f"  Successfully loaded: {count}")


def ingest_states(conn: sqlite3.Connection, path: Path) -> None:
    sql = """
        INSERT OR REPLACE INTO us_states (
            id, name, geom_wkb, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
    """
    batch: list[Tuple[object, ...]] = []
    for feature in iter_geojson_features(path):
        geom = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        if not geom or geom.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        try:
            wkb = geometry_to_wkb(geom)
            bbox_minx, bbox_miny, bbox_maxx, bbox_maxy = bbox_for_geometry(geom)
        except GeometryError:
            continue
        gid = props.get("GEO_ID") or props.get("GEOID") or props.get("geoid")
        row = (
            _clean_text(gid),
            _clean_text(props.get("NAME") or props.get("name")),
            wkb,
            bbox_minx,
            bbox_miny,
            bbox_maxx,
            bbox_maxy,
        )
        if row[0] is None or row[1] is None:
            continue
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)


def iter_geojson_features(path: Path) -> Iterator[dict[str, Any]]:
    """Iterate over features in a GeoJSON file."""
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
        features = data.get("features", [])
        for feature in features:
            # Only yield valid feature dictionaries
            if isinstance(feature, dict) and feature.get("type") == "Feature":
                yield feature


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        # coerce via string then float to handle various input types
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
