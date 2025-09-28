"""SQLite access helpers for PocketScope map data."""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from .spatial import register_haversine, wkb_to_geometry


def should_display_airport(ident: str, extra_airports: list[str] | None = None) -> bool:
    """Check if an airport identifier should be displayed.

    An airport is displayed if:
    1. Its identifier is exactly 3 alphabetic characters (no numbers), OR
    2. Its identifier is in the extra_airports list

    Args:
        ident: Airport identifier (will be normalized to uppercase)
        extra_airports: List of additional airport identifiers to display

    Returns:
        True if the airport should be displayed, False otherwise
    """
    if not ident:
        return False

    # Normalize identifier
    ident = str(ident).strip().upper()

    # Check if it's exactly 3 alphabetic characters
    if re.match(r"^[A-Z]{3}$", ident):
        return True

    # Check if it's in the extra airports list
    if extra_airports and ident in extra_airports:
        return True

    return False


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    register_haversine(conn)
    return conn


def query_airports(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    radius_mi: float,
    extra_airports: list[str] | None = None,
) -> list[dict[str, Any]]:
    sql = """
    WITH cand AS (
      SELECT a.rowid AS rid
      FROM airports_rtree a
      WHERE a.minx <= :maxx AND a.maxx >= :minx
        AND a.miny <= :maxy AND a.maxy >= :miny
    )
    SELECT ap.*
    FROM airports ap
    JOIN cand ON cand.rid = ap.rowid
    WHERE HAV_MILES(ap.lat, ap.lon, :lat, :lon) <= :radius;
    """
    params = {
        "lat": lat,
        "lon": lon,
        "minx": minx,
        "miny": miny,
        "maxx": maxx,
        "maxy": maxy,
        "radius": radius_mi,
    }
    cur = conn.execute(sql, params)
    rows = []
    for row in cur.fetchall():
        row_dict = _row_to_dict(row)
        geom_blob = row_dict.pop("geom_wkb", None)
        if geom_blob is not None:
            row_dict["geometry"] = wkb_to_geometry(geom_blob)

        # Apply airport identifier filtering
        ident = row_dict.get("ident")
        if ident and should_display_airport(str(ident), extra_airports):
            rows.append(row_dict)
    return rows


def query_runways(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    radius_mi: float,
) -> list[dict[str, Any]]:
    sql = """
    WITH cand AS (
      SELECT r.rowid AS rid
      FROM runways_rtree r
      WHERE r.minx <= :maxx AND r.maxx >= :minx
        AND r.miny <= :maxy AND r.maxy >= :miny
    )
    SELECT rw.*, ap.ident AS airport_ident
    FROM runways rw
    JOIN cand ON cand.rid = rw.rowid
    LEFT JOIN airports ap ON ap.id = rw.airport_id
    WHERE HAV_MILES(rw.centroid_lat, rw.centroid_lon, :lat, :lon) <= :radius;
    """
    params = {
        "lat": lat,
        "lon": lon,
        "minx": minx,
        "miny": miny,
        "maxx": maxx,
        "maxy": maxy,
        "radius": radius_mi,
    }
    cur = conn.execute(sql, params)
    rows: list[dict[str, Any]] = []
    for row in cur.fetchall():
        row_dict = _row_to_dict(row)
        geom_blob = row_dict.pop("geom_wkb", None)
        if geom_blob is not None:
            row_dict["geometry"] = wkb_to_geometry(geom_blob)
        rows.append(row_dict)
    return rows


def query_states(
    conn: sqlite3.Connection,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
) -> list[dict[str, Any]]:
    sql = """
    WITH cand AS (
      SELECT s.rowid AS rid
      FROM us_states_rtree s
      WHERE s.minx <= :maxx AND s.maxx >= :minx
        AND s.miny <= :maxy AND s.maxy >= :miny
    )
    SELECT st.*
    FROM us_states st
    JOIN cand ON cand.rid = st.rowid;
    """
    params = {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy}
    cur = conn.execute(sql, params)
    rows: list[dict[str, Any]] = []
    for row in cur.fetchall():
        row_dict = _row_to_dict(row)
        geom_blob = row_dict.pop("geom_wkb", None)
        if geom_blob is not None:
            row_dict["geometry"] = wkb_to_geometry(geom_blob)
        rows.append(row_dict)
    return rows


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
