"""BaseMap-backed data access and convenience helpers.

This is a renamed copy of the previous `runways_store.py` implementation. It
provides the `BaseMap` class (ingest/query) plus compatibility helpers used by
the rest of the codebase.
"""
from __future__ import annotations

import functools
import json
import os
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional

from pocketscope.core.geo import haversine_nm, initial_bearing_deg
from pocketscope.settings.store import SettingsStore


def _load_extra_airports() -> set[str]:
    """Load user-configured extra airport idents.

    Preferred path: use SettingsStore.load() which validates and normalizes
    entries. However users may hand-edit `settings.json` with a slightly
    different key name or format; to be robust we fall back to reading the
    raw JSON and accept several legacy key names and string/list forms.
    """
    try:
        s = SettingsStore.load()
        out = set(s.extra_airports or [])
        if out:
            return out
    except Exception:
        # Fall back to an empty set if settings cannot be loaded.
        pass
    return set()


def _is_three_letter_ident(ident: str) -> bool:
    # Accept only exact 3 ASCII letters (A-Z)
    if not ident or len(ident) != 3:
        return False
    return ident.isalpha() and ident.upper() == ident


DB_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS airports (
  id INTEGER PRIMARY KEY,
  identifier TEXT,
  global_id TEXT,
  lat REAL NOT NULL,
  lon REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_airports_ident ON airports(identifier);
CREATE TABLE IF NOT EXISTS runways (
  id INTEGER PRIMARY KEY,
  airport_ident TEXT,
  airport_global_id TEXT,
  rwy_ident TEXT,
  length_m REAL,
  width_m REAL,
  bearing_true REAL,
  lat1 REAL, lon1 REAL,
  lat2 REAL, lon2 REAL,
  min_lat REAL, max_lat REAL, min_lon REAL, max_lon REAL,
  surface TEXT,
  lighted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runways_airport ON runways(airport_ident);
CREATE TABLE IF NOT EXISTS states (
  id INTEGER PRIMARY KEY,
  name TEXT,
  geojson TEXT,
  min_lat REAL, max_lat REAL, min_lon REAL, max_lon REAL
);
"""


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.expanduser(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _geojson_features_from_path(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        feats = data.get("features") or []
    elif isinstance(data, list):
        feats = data
    else:
        feats = []
    for f in feats:
        if isinstance(f, dict):
            yield f


class BaseMap:
    """Manage an on-disk SQLite base map and provide view queries.

    See module-level docstring for usage.
    """

    def __init__(self, sqlite_path: str) -> None:
        self.sqlite_path = os.path.expanduser(sqlite_path)
        self._local = threading.local()
        self._airports_cache = functools.lru_cache(maxsize=256)(self._query_airports)
        self._runways_cache = functools.lru_cache(maxsize=256)(self._query_runways)
        self._states_cache = functools.lru_cache(maxsize=64)(self._query_states)
        conn = _connect(self.sqlite_path)
        cur = conn.cursor()
        cur.executescript(DB_SCHEMA)
        conn.commit()
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = _connect(self.sqlite_path)
            self._local.conn = c
        return c

    # Ingestors
    def ingest_airports(self, geojson_path: str) -> int:
        conn = _connect(self.sqlite_path)
        cur = conn.cursor()
        inserted = 0
        for feat in _geojson_features_from_path(geojson_path):
            props = feat.get("properties") or {}
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") if isinstance(geom, dict) else None
            if not coords or not isinstance(coords, list):
                continue
            try:
                lon, lat = float(coords[0]), float(coords[1])
            except Exception:
                continue
            ident = (
                props.get("icao_id")
                or props.get("ident")
                or props.get("IDENT")
                or props.get("identifier")
                or props.get("IDENTIFIER")
            )
            if not ident:
                continue
            ident = str(ident).strip().upper()
            global_id = props.get("global_id") or props.get("GLOBAL_ID") or None
            cur.execute(
                (
                    "REPLACE INTO airports (identifier, global_id, lat, lon) "
                    "VALUES (?, ?, ?, ?)"
                ),
                (ident, global_id, lat, lon),
            )
            inserted += 1
        conn.commit()
        conn.close()
        self._airports_cache.cache_clear()
        return inserted

    def ingest_runways(self, geojson_path: str) -> int:
        conn = _connect(self.sqlite_path)
        cur = conn.cursor()
        inserted = 0
        for feat in _geojson_features_from_path(geojson_path):
            props_raw = feat.get("properties") or {}
            props = (
                {k.lower(): v for k, v in props_raw.items()}
                if isinstance(props_raw, dict)
                else {}
            )
            geom = feat.get("geometry") or {}
            gtype = (geom.get("type") or "").lower()
            coords = geom.get("coordinates")
            lat1: Optional[float] = None
            lon1: Optional[float] = None
            lat2: Optional[float] = None
            lon2: Optional[float] = None
            if gtype == "linestring" and isinstance(coords, list) and len(coords) >= 2:
                try:
                    lon1 = float(coords[0][0])
                    lat1 = float(coords[0][1])
                    lon2 = float(coords[-1][0])
                    lat2 = float(coords[-1][1])
                except Exception:
                    # fall through to try reading properties
                    pass
            elif gtype == "polygon" and isinstance(coords, list) and coords:
                ring = coords[0]
                if len(ring) >= 4:
                    try:
                        lon1 = float(ring[0][0])
                        lat1 = float(ring[0][1])
                        lon2 = float(ring[2][0])
                        lat2 = float(ring[2][1])
                    except Exception:
                        pass
            try:
                if lat1 is None:
                    v = props.get("lat1")
                    if v is not None:
                        lat1 = float(v)
                if lon1 is None:
                    v = props.get("lon1")
                    if v is not None:
                        lon1 = float(v)
                if lat2 is None:
                    v = props.get("lat2")
                    if v is not None:
                        lat2 = float(v)
                if lon2 is None:
                    v = props.get("lon2")
                    if v is not None:
                        lon2 = float(v)
            except Exception:
                pass
            if lat1 is None or lat2 is None or lon1 is None or lon2 is None:
                continue
            length_nm = haversine_nm(lat1, lon1, lat2, lon2)
            length_m = length_nm * 1852.0
            bearing = initial_bearing_deg(
                float(lat1), float(lon1), float(lat2), float(lon2)
            )
            min_lat, max_lat = min(lat1, lat2), max(lat1, lat2)
            min_lon, max_lon = min(lon1, lon2), max(lon1, lon2)
            airport_ident = (
                props.get("airport_ident")
                or props.get("airport")
                or props.get("airport_id")
                or props.get("apt_ident")
                or props.get("ident")
            )
            airport_global_id = (
                props.get("global_id")
                or props.get("GLOBAL_ID")
                or props.get("globalid")
            )
            rwy_ident = props.get("designator") or props.get("ref") or props.get("name")
            surface = (
                props.get("surface") or props.get("surf") or props.get("comp_code")
            )
            width = props.get("width") or props.get("width_m")
            lighted = int(
                bool(
                    props.get("lightactv")
                    or props.get("lights")
                    or props.get("lighted")
                )
            )
            cur.execute(
                (
                    "INSERT INTO runways (airport_ident, airport_global_id, rwy_ident, "
                    "length_m, width_m, bearing_true, lat1, lon1, lat2, lon2, "
                    "min_lat, max_lat, min_lon, max_lon, surface, lighted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    (str(airport_ident).strip().upper() if airport_ident else None),
                    airport_global_id,
                    rwy_ident,
                    length_m,
                    width,
                    bearing,
                    lat1,
                    lon1,
                    lat2,
                    lon2,
                    min_lat,
                    max_lat,
                    min_lon,
                    max_lon,
                    surface,
                    lighted,
                ),
            )
            inserted += 1
        conn.commit()
        conn.close()
        self._runways_cache.cache_clear()
        return inserted

    def ingest_states(self, geojson_path: str, name_key: str = "name") -> int:
        conn = _connect(self.sqlite_path)
        cur = conn.cursor()
        inserted = 0
        for feat in _geojson_features_from_path(geojson_path):
            props = feat.get("properties") or {}
            name = props.get(name_key) or props.get(name_key.upper()) or None
            geom = feat.get("geometry") or {}
            coords_container = geom.get("coordinates")
            min_lat = min_lon = float("inf")
            max_lat = max_lon = float("-inf")

            def _walk(xs: Any) -> None:
                nonlocal min_lat, min_lon, max_lat, max_lon
                if isinstance(xs, list) and xs and isinstance(xs[0], (list, tuple)):
                    for e in xs:
                        _walk(e)
                elif (
                    isinstance(xs, (list, tuple))
                    and len(xs) >= 2
                    and isinstance(xs[0], (int, float))
                ):
                    lon, lat = float(xs[0]), float(xs[1])
                    min_lat = min(min_lat, lat)
                    max_lat = max(max_lat, lat)
                    min_lon = min(min_lon, lon)
                    max_lon = max(max_lon, lon)

            _walk(coords_container)
            if min_lat == float("inf"):
                continue
            cur.execute(
                (
                    "INSERT INTO states (name, geojson, min_lat, max_lat, "
                    "min_lon, max_lon) VALUES (?, ?, ?, ?, ?, ?)"
                ),
                (
                    name,
                    json.dumps(feat.get("geometry") or {}),
                    min_lat,
                    max_lat,
                    min_lon,
                    max_lon,
                ),
            )
            inserted += 1
        conn.commit()
        conn.close()
        self._states_cache.cache_clear()
        return inserted

    # Query API
    def get_airports_in_view(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> List[Dict[str, Any]]:
        return list(self._airports_cache(min_lat, max_lat, min_lon, max_lon))

    def _query_airports(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> List[Dict[str, Any]]:
        conn = _connect(self.sqlite_path)
        cur = conn.cursor()
        cur.execute(
            (
                "SELECT identifier, global_id, lat, lon FROM airports "
                "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
            ),
            (min_lat, max_lat, min_lon, max_lon),
        )
        rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        # Load user-configured extra airports (allowlist). This is intentionally
        # loaded on each query so changes to settings.json are picked up without
        # restarting the app.
        extra = _load_extra_airports()
        for r in rows:
            ident = (r["identifier"] or "").upper()
            if not ident:
                continue
            # Default: only show 3-letter identifiers OR those explicitly
            # configured in settings.extra_airports.
            if not (_is_three_letter_ident(ident) or ident in extra):
                continue
            out.append(
                {
                    "identifier": ident,
                    "global_id": r["global_id"],
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                }
            )
        conn.close()
        return out

    def get_runways_in_view(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> List[Dict[str, Any]]:
        return list(self._runways_cache(min_lat, max_lat, min_lon, max_lon))

    def _query_runways(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> List[Dict[str, Any]]:
        conn = _connect(self.sqlite_path)
        cur = conn.cursor()
        cur.execute(
            (
                "SELECT * FROM runways WHERE NOT (max_lat < ? OR min_lat > ? "
                "OR max_lon < ? OR min_lon > ?)"
            ),
            (min_lat, max_lat, min_lon, max_lon),
        )
        rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "airport_ident": r["airport_ident"],
                    "airport_global_id": r["airport_global_id"],
                    "rwy_ident": r["rwy_ident"],
                    "length_m": float(r["length_m"])
                    if r["length_m"] is not None
                    else None,
                    "width_m": float(r["width_m"])
                    if r["width_m"] is not None
                    else None,
                    "bearing_true": float(r["bearing_true"])
                    if r["bearing_true"] is not None
                    else None,
                    "lat1": float(r["lat1"]),
                    "lon1": float(r["lon1"]),
                    "lat2": float(r["lat2"]),
                    "lon2": float(r["lon2"]),
                    "surface": r["surface"],
                    "lighted": int(r["lighted"]),
                }
            )
        conn.close()
        return out

    def get_states_in_view(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> List[Dict[str, Any]]:
        return list(self._states_cache(min_lat, max_lat, min_lon, max_lon))

    def _query_states(
        self, min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> List[Dict[str, Any]]:
        conn = _connect(self.sqlite_path)
        cur = conn.cursor()
        cur.execute(
            (
                "SELECT id, name, geojson FROM states WHERE NOT (max_lat < ? "
                "OR min_lat > ? OR max_lon < ? OR min_lon > ?)"
            ),
            (min_lat, max_lat, min_lon, max_lon),
        )
        rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                geom = json.loads(r["geojson"]) if r["geojson"] else {}
            except Exception:
                geom = {}
            out.append({"id": int(r["id"]), "name": r["name"], "geometry": geom})
        conn.close()
        return out

    def clear_caches(self) -> None:
        self._airports_cache.cache_clear()
        self._runways_cache.cache_clear()
        self._states_cache.cache_clear()


# Compatibility helpers used by other modules/tests
def get_airports(sqlite_path: str) -> List[Dict[str, Any]]:
    try:
        conn = _connect(sqlite_path)
        cur = conn.cursor()
        cur.execute("SELECT identifier, global_id, lat, lon FROM airports")
        rows = cur.fetchall()
        out = [
            {
                "identifier": r["identifier"],
                "global_id": r["global_id"],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
            }
            for r in rows
        ]
        conn.close()
        return out
    except Exception:
        return []


def get_runways_for_airport(
    sqlite_path: str, airport_ident: str
) -> List[Dict[str, Any]]:
    if not airport_ident:
        return []
    ident = str(airport_ident).upper()
    conn = _connect(sqlite_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM runways WHERE airport_ident = ?", (ident,))
    rows = cur.fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({k: r[k] for k in r.keys()})
    conn.close()
    return out


def get_runways_for_airports(
    sqlite_path: str, airport_idents: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    idents = [str(i).upper() for i in airport_idents if i]
    out: Dict[str, List[Dict[str, Any]]] = {i: [] for i in idents}
    if not idents:
        return out
    conn = _connect(sqlite_path)
    cur = conn.cursor()
    placeholders = ",".join(["?" for _ in idents])
    cur.execute(
        f"SELECT * FROM runways WHERE airport_ident IN ({placeholders})", tuple(idents)
    )
    rows = cur.fetchall()
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        a = d.get("airport_ident")
        if not a:
            continue
        out.setdefault(a, []).append(d)
    conn.close()
    return out


class RunwayPrefetcher:
    """Background prefetcher for runway batch lookups.

    Submits batch lookups to a small ThreadPoolExecutor so the render thread
    can remain responsive. Each worker uses its own sqlite connection.
    """

    def __init__(self, sqlite_path: str, max_workers: int = 2) -> None:
        # Import locally to avoid the module-level import when the feature
        # is not used in tests or headless environments.
        import concurrent.futures
        from concurrent.futures import Future

        self.sqlite_path = sqlite_path
        self._exe = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        # Keep a reference to futures to avoid GC of running tasks.
        self._futures: List[Future[Any]] = []

    def prefetch(self, idents: List[str]) -> None:
        if not idents:
            return
        fut = self._exe.submit(get_runways_for_airports, self.sqlite_path, idents)
        self._futures.append(fut)

    def close(self) -> None:
        try:
            self._exe.shutdown(wait=False)
        except Exception:
            pass
