"""Airport data loading and spatial queries.

This module provides a simple loader for a JSON array of airports and
nearest-neighbor selection using great-circle distance (haversine).

Schema
------
Input JSON should be an array of objects with fields:
    - identifier: Airport identifier string (e.g., "KBOS"). Required.
    - lat: Latitude in decimal degrees (float). Required.
    - lon: Longitude in decimal degrees (float). Required.

Entries with missing or invalid fields are ignored. Identifiers are trimmed
and uppercased. This module is intentionally minimal; a full directory will
be added later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from pocketscope.core.geo import haversine_nm

__all__ = ["Airport", "load_airports_json", "nearest_airports"]


@dataclass(frozen=True)
class Airport:
    ident: str  # e.g., KBOS
    lat: float
    lon: float


def _coerce_ident(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    return s if s else None


def _coerce_float(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
        return f
    except Exception:
        return None


def load_airports_json(path: str) -> list[Airport]:
    """Load airports from a JSON file.

    Accepts a JSON array with objects containing the fields:
        identifier (str), lat (float), lon (float).
    Ignores entries missing fields; trims/uppercases identifier.

    Parameters
    ----------
    path: str
        Path to a JSON file with an array of objects as described.

    Returns
    -------
    list[Airport]
        Parsed and normalized airport entries.
    """
    # Keep compatibility: if a sqlite DB is provided, read from the airports table.
    out: list[Airport] = []
    try:
        if str(path).lower().endswith(".sqlite") or str(path).lower().endswith(".db"):
            import sqlite3

            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT identifier, lat, lon FROM airports")
            rows = cur.fetchall()
            for r in rows:
                try:
                    ident = _coerce_ident(r["identifier"]) if r["identifier"] else None
                    lat = _coerce_float(r["lat"])
                    lon = _coerce_float(r["lon"])
                    if ident is None or lat is None or lon is None:
                        continue
                    out.append(Airport(ident=ident, lat=lat, lon=lon))
                except Exception:
                    continue
            conn.close()
            return out
    except Exception:
        # not a sqlite DB or cannot open - fall back to JSON
        pass

    # Fallback: treat path as JSON file
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        return out

    for item in data:
        if not isinstance(item, dict):
            continue
        ident = _coerce_ident(item.get("identifier"))
        lat = _coerce_float(item.get("lat"))
        lon = _coerce_float(item.get("lon"))
        if ident is None or lat is None or lon is None:
            continue
        out.append(Airport(ident=ident, lat=lat, lon=lon))

    return out


def nearest_airports(
    lat: float,
    lon: float,
    airports: Sequence[Airport],
    *,
    max_nm: float = 50.0,
    k: int = 3,
) -> list[Airport]:
    """Return up to k airports within max_nm, sorted by distance.

    Uses great-circle distance (haversine) from ``pocketscope.core.geo``.

    Parameters
    ----------
    lat, lon: float
        Reference position in degrees.
    airports: Sequence[Airport]
        Candidate airports to search.
    max_nm: float
        Maximum range in nautical miles for inclusion.
    k: int
        Maximum number of airports to return.
    """
    # Compute distances and filter by range
    scored: list[tuple[float, Airport]] = []
    for ap in airports:
        d = haversine_nm(lat, lon, ap.lat, ap.lon)
        if d <= max_nm:
            scored.append((d, ap))

    scored.sort(key=lambda t: t[0])
    return [ap for _, ap in scored[: max(0, int(k))]]
