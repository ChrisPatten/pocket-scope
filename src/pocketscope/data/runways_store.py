"""Lightweight runway SQLite cache built from a GeoJSON feature collection.

Provides simple sync query API and a tiny background prefetch helper.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, cast

from pocketscope.core.geo import haversine_nm, initial_bearing_deg

_LOG = logging.getLogger(__name__)


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _row_to_dict(row: Any) -> Dict[str, Any]:
    """Coerce a sqlite3.Row or mapping to a plain dict.

    Some callers may pass already-converted mappings; accept Any to make the
    helper tolerant for static checks and runtime use.
    """
    try:
        # sqlite3.Row supports .keys() and indexing
        if hasattr(row, "keys") and not isinstance(row, dict):
            return {k: row[k] for k in row.keys()}
        # fallback: assume mapping-like
        return dict(row)
    except Exception:
        return {}


def _safe_float(v: Any) -> Optional[float]:
    """Safely convert a value to float or return None on failure."""
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def build_sqlite_from_geojson(geojson_path: str, sqlite_path: str) -> None:
    """(Re)build a minimal runways SQLite DB from a GeoJSON file.

    The function is defensive about properties present in different runway
    datasets. It computes length/bearing when endpoints are present, and
    stores a sha256 and mtime in a meta table to avoid unnecessary rebuilds.
    """
    _ensure_dir(sqlite_path)
    geojson_path = os.path.expanduser(geojson_path)
    sqlite_path = os.path.expanduser(sqlite_path)

    # Read source and compute identity
    with open(geojson_path, "rb") as fh:
        data = fh.read()
    src_sha = hashlib.sha256(data).hexdigest()
    src_mtime = str(int(os.path.getmtime(geojson_path)))

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Ensure schema
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS runways (
          id INTEGER PRIMARY KEY,
          airport_ident TEXT NOT NULL,
          rwy_ident TEXT,
          length_m REAL NOT NULL,
          width_m REAL,
          bearing_true REAL,
          lat1 REAL, lon1 REAL,
          lat2 REAL, lon2 REAL,
          surface TEXT,
          lighted INTEGER DEFAULT 0
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runways_airport ON runways (airport_ident);
        """
    )

    # Check meta to skip rebuild
    cur.execute("SELECT value FROM meta WHERE key = 'src_sha'")
    prev = cur.fetchone()
    if prev and prev[0] == src_sha:
        # Also check mtime to be safe
        cur.execute("SELECT value FROM meta WHERE key = 'src_mtime'")
        pm = cur.fetchone()
        if pm and pm[0] == src_mtime:
            _LOG.debug("runways sqlite up-to-date: %s", sqlite_path)
            conn.close()
            return

    # Otherwise rebuild: clear table and re-insert
    cur.execute("DELETE FROM runways;")
    try:
        parsed = json.loads(data.decode("utf8"))
    except Exception:
        parsed = {}

    # Normalize features to a list (avoid None or unexpected types)
    features: List[Dict[str, Any]] = []
    if isinstance(parsed, dict):
        features = list(parsed.get("features") or [])
    elif isinstance(parsed, list):
        features = list(parsed)

    def _get_prop(f: Dict[str, Any], *keys: str) -> Any:
        p = f.get("properties") or {}
        for k in keys:
            if k in p:
                return p[k]
        return None

    inserted = 0
    # Load airports asset for GUID -> ICAO mapping by proximity when needed
    airports_asset_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "assets", "airports.json")
    )
    airports_list: List[Dict[str, Any]] = []
    try:
        with open(airports_asset_path, "r", encoding="utf8") as af:
            airports_list = json.load(af)
    except Exception:
        airports_list = []
    for feat in features:
        props = feat.get("properties") or {}
        # normalize property keys to lowercase for forgiving matching
        props = {
            str(k).lower(): v
            for k, v in (props.items() if isinstance(props, dict) else {})
        }
        geom = feat.get("geometry") or {}
        airport_ident = (
            str(
                props.get("airport_ident")
                or props.get("apt_ident")
                or props.get("ident")
                or props.get("site")
                or props.get("airport")
                or props.get("airport_id")
                or props.get("global_id")
            )
            if props
            else None
        )
        if not airport_ident:
            # try top-level tags
            airport_ident = feat.get("id") or feat.get("name")
        if not airport_ident:
            continue
        # Many datasets use GUIDs (contains '-') for airport ids. We'll
        # normalize to ICAO identifiers by spatially matching to our
        # airports.json asset when a GUID-like id is encountered.
        raw_ident = str(airport_ident)
        airport_ident = raw_ident.upper()

        # If looks like a GUID (contains hyphen) and we have an airports
        # asset, attempt to map by proximity.
        if (
            "-" in raw_ident or len(raw_ident) > 6 and not raw_ident.isalpha()
        ) and airports_list:
            # compute a representative point for the runway: midpoint of
            # endpoints if available, otherwise centroid of polygon ring
            rep_lat = rep_lon = None
            # try to use geometry coordinates first
            try:
                if isinstance(geom, dict):
                    gtype_local = (geom.get("type") or "").lower()
                    coords_local = geom.get("coordinates")
                    if (
                        gtype_local == "linestring"
                        and isinstance(coords_local, list)
                        and len(coords_local) >= 2
                    ):
                        a = coords_local[0]
                        b = coords_local[-1]
                        rep_lat = (a[1] + b[1]) / 2.0
                        rep_lon = (a[0] + b[0]) / 2.0
                    elif (
                        gtype_local == "polygon"
                        and isinstance(coords_local, list)
                        and coords_local
                    ):
                        ring = coords_local[0]
                        # centroid-like average of ring vertices
                        s_lat = s_lon = 0.0
                        cnt = 0
                        for v in ring:
                            if isinstance(v, (list, tuple)) and len(v) >= 2:
                                s_lon += v[0]
                                s_lat += v[1]
                                cnt += 1
                        if cnt:
                            rep_lat = s_lat / cnt
                            rep_lon = s_lon / cnt
            except Exception:
                rep_lat = rep_lon = None

            # fallback to any lat/lon props if present
            try:
                if rep_lat is None:
                    plat = _safe_float(props.get("lat1"))
                    plon = _safe_float(props.get("lon1"))
                    if plat is not None and plon is not None:
                        rep_lat = plat
                        rep_lon = plon
            except Exception:
                rep_lat = rep_lon = None

            # If we have a representative point, find nearest airport
            if rep_lat is not None and rep_lon is not None:
                best_dist = float("inf")
                best_ident = None
                for a in airports_list:
                    try:
                        aid = a.get("identifier")
                        alat = _safe_float(a.get("lat"))
                        alon = _safe_float(a.get("lon"))
                        if alat is None or alon is None:
                            continue
                        d_nm = haversine_nm(rep_lat, rep_lon, alat, alon)
                        if d_nm < best_dist:
                            best_dist = d_nm
                            best_ident = aid
                    except Exception:
                        continue
                # Accept mapping if within reasonable distance (20 km ~= 10.8 nm)
                if best_ident and best_dist < (20.0 / 1.852):
                    _LOG.debug(
                        "Mapped runway airport GUID %s -> %s (%.1f nm)",
                        raw_ident,
                        best_ident,
                        best_dist,
                    )
                    airport_ident = str(best_ident).upper()

        # Determine runway ident and attributes
        # prefer common lowercase keys in the normalized props dict
        rwy_ident = (
            props.get("rwy_ident")
            or props.get("ref")
            or props.get("name")
            or props.get("designator")
        )
        surface = props.get("surface") or props.get("surf")
        width = props.get("width") or props.get("width_m")
        lighted = int(
            bool(
                props.get("lighted")
                or props.get("lights")
                or props.get("has_lights")
                or props.get("lightactv")
            )
        )

        lat1 = lon1 = lat2 = lon2 = None
        bearing = None
        length_m = None

        coords = geom.get("coordinates") if isinstance(geom, dict) else None
        gtype = (geom.get("type") if isinstance(geom, dict) else None) or ""
        if (
            isinstance(gtype, str)
            and gtype.lower() == "linestring"
            and isinstance(coords, list)
            and len(coords) >= 2
        ):
            # Expect [lon, lat] pairs; take first/last
            try:
                lon1, lat1 = coords[0][0], coords[0][1]
                lon2, lat2 = coords[-1][0], coords[-1][1]
                # length (m)
                rng_nm = haversine_nm(lat1, lon1, lat2, lon2)
                length_m = float(rng_nm * 1852.0)
                bearing = float(initial_bearing_deg(lat1, lon1, lat2, lon2))
            except Exception:
                lat1 = lon1 = lat2 = lon2 = None
        elif (
            isinstance(gtype, str)
            and gtype.lower() == "polygon"
            and isinstance(coords, list)
            and coords
        ):
            # For polygon runway footprints, take exterior ring and pick the two
            # vertices that are farthest apart as runway endpoints.
            try:
                ring = coords[0]
                best = (None, None, 0.0)
                for i in range(len(ring)):
                    for j in range(i + 1, len(ring)):
                        a = ring[i]
                        b = ring[j]
                        # expect [lon, lat, ...]
                        lat_a, lon_a = a[1], a[0]
                        lat_b, lon_b = b[1], b[0]
                        d = haversine_nm(lat_a, lon_a, lat_b, lon_b)
                        if d > best[2]:
                            best = (a, b, d)
                if best[0] and best[1]:
                    lon1, lat1 = best[0][0], best[0][1]
                    lon2, lat2 = best[1][0], best[1][1]
                    rng_nm = best[2]
                    length_m = float(rng_nm * 1852.0)
                    bearing = float(initial_bearing_deg(lat1, lon1, lat2, lon2))
            except Exception:
                lat1 = lon1 = lat2 = lon2 = None
        else:
            # Some datasets may provide endpoints or length as properties
            try:
                lat1 = _safe_float(props.get("lat1"))
                lon1 = _safe_float(props.get("lon1"))
                lat2 = _safe_float(props.get("lat2"))
                lon2 = _safe_float(props.get("lon2"))
                if None not in (lat1, lon1, lat2, lon2):
                    # cast to satisfy static checker that values are floats
                    f_lat1 = cast(float, lat1)
                    f_lon1 = cast(float, lon1)
                    f_lat2 = cast(float, lat2)
                    f_lon2 = cast(float, lon2)
                    rng_nm = haversine_nm(f_lat1, f_lon1, f_lat2, f_lon2)
                    length_m = float(rng_nm * 1852.0)
                    bearing = float(initial_bearing_deg(f_lat1, f_lon1, f_lat2, f_lon2))
            except Exception:
                pass

        # Accept explicit length/bearing in properties
        if length_m is None:
            # accept length from properties (allow various key names)
            lm = None
            for lk in ("length_m", "length", "length_ft"):
                v = props.get(lk)
                if v is not None:
                    lm = v
                    break
            # Many sources use uppercase keys (e.g., "LENGTH"). We've normalized
            # property keys to lowercase earlier, so accept any of the common names.
            if lm is not None:
                try:
                    # Convert feet to meters when the provided value is likely feet.
                    parsed_l = _safe_float(lm)
                    if parsed_l is not None:
                        length_m = parsed_l
                        # Heuristic: if value > 1000 it's likely feet, convert to meters
                        if length_m > 1000:
                            length_m = length_m * 0.3048
                except Exception:
                    length_m = None

        if bearing is None:
            b = (
                props.get("bearing_true")
                or props.get("bearing")
                or props.get("heading")
            )
            if b is not None:
                try:
                    pb = _safe_float(b)
                    if pb is not None:
                        bearing = float(pb) % 360.0
                except Exception:
                    bearing = None

        if length_m is None:
            # skip if no length available
            continue

        # Only accept runways with concrete/asphalt-like surfaces.
        # Some datasets use a COMP_CODE or similar property; prefer explicit
        # surface field but fall back to COMP_CODE if present.
        surf_val = None
        try:
            if surface:
                surf_val = str(surface)
            else:
                # try common alternate property names
                for alt in ("comp_code", "compcode", "surf_type", "surface_type"):
                    v = props.get(alt)
                    if v:
                        surf_val = str(v)
                        break
        except Exception:
            surf_val = None

        if surf_val is None:
            # no surface info -> skip per new policy
            continue

        s = surf_val.lower()
        if ("conc" not in s) and ("asp" not in s):
            # not concrete/asphalt-like -> skip
            continue

        cur.execute(
            """
            INSERT INTO runways (
                airport_ident, rwy_ident, length_m, width_m, bearing_true,
                lat1, lon1, lat2, lon2, surface, lighted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                airport_ident,
                str(rwy_ident) if rwy_ident is not None else None,
                float(length_m),
                _safe_float(width) if width is not None else None,
                _safe_float(bearing) if bearing is not None else None,
                _safe_float(lat1) if lat1 is not None else None,
                _safe_float(lon1) if lon1 is not None else None,
                _safe_float(lat2) if lat2 is not None else None,
                _safe_float(lon2) if lon2 is not None else None,
                str(surface) if surface is not None else None,
                int(lighted),
            ),
        )
        inserted += 1

    # Update meta
    cur.execute("REPLACE INTO meta (key, value) VALUES ('src_sha', ?)", (src_sha,))
    cur.execute("REPLACE INTO meta (key, value) VALUES ('src_mtime', ?)", (src_mtime,))
    conn.commit()
    conn.close()
    _LOG.info("Built runways sqlite %s (%d rows)", sqlite_path, inserted)


def _connect(sqlite_path: str) -> sqlite3.Connection:
    sqlite_path = os.path.expanduser(sqlite_path)
    conn = sqlite3.connect(sqlite_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@functools.lru_cache(maxsize=512)
def get_runways_for_airport_cached(
    sqlite_path: str, airport_ident: str
) -> Tuple[str, Tuple[Dict[str, Any], ...]]:
    """Cached single-airport fetch. Returns tuple (ident, tuple(rows))."""
    if not airport_ident:
        return (airport_ident, tuple())
    ident = airport_ident.upper()
    conn = _connect(sqlite_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM runways WHERE airport_ident = ?", (ident,))
    rows = cur.fetchall()
    out = tuple(_row_to_dict(r) for r in rows)
    conn.close()
    return (ident, out)


def get_runways_for_airport(
    sqlite_path: str, airport_ident: str
) -> List[Dict[str, Any]]:
    """Return runways for an airport ident (case-insensitive). Uses an LRU cache."""
    return list(get_runways_for_airport_cached(sqlite_path, airport_ident)[1])


def get_runways_for_airports(
    sqlite_path: str, airport_idents: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Batch-fetch runways for multiple airport idents. Returns map ident->list.

    Uses a single SQL query with an IN (...) clause. Normalizes idents to
    upper-case before querying.
    """
    idents = [str(i).upper() for i in airport_idents if i]
    out: Dict[str, List[Dict[str, Any]]] = {i: [] for i in idents}
    if not idents:
        return out

    # Try cache first to avoid SQL when possible
    pending: List[str] = []
    for ident in idents:
        try:
            cached_ident, rows = get_runways_for_airport_cached(sqlite_path, ident)
            if rows:
                out[cached_ident] = [dict(r) for r in rows]
            else:
                # cached empty; still consider as hit
                out[ident] = []
        except Exception:
            # Cache miss or error -> query later
            pending.append(ident)

    if not pending:
        return out

    # Query remaining idents in one SQL
    conn = _connect(sqlite_path)
    cur = conn.cursor()
    placeholders = ",".join(["?" for _ in pending])
    sql = f"SELECT * FROM runways WHERE airport_ident IN ({placeholders})"
    cur.execute(sql, tuple(pending))
    db_rows = cur.fetchall()
    for r in db_rows:
        d = _row_to_dict(r)
        a = d.get("airport_ident")
        # Ensure we use a string key; skip malformed rows without ident
        if not a:
            continue
        a_key = str(a)
        if a_key not in out:
            out[a_key] = [d]
        else:
            out[a_key].append(d)
        # Prime single-item cache
        try:
            get_runways_for_airport_cached(sqlite_path, a)  # noqa: B018 - prime cache
        except Exception:
            pass
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
        # Submit a batch fetch; result discarded (cache is the DB + in-memory
        # callers may use explicit caching). Keeping futures prevents GC.
        fut = self._exe.submit(get_runways_for_airports, self.sqlite_path, idents)
        self._futures.append(fut)

    def close(self) -> None:
        try:
            self._exe.shutdown(wait=False)
        except Exception:
            pass
