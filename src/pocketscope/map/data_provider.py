"""High-level access to spatial data for map rendering."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, cast

from pocketscope.data import cache as cache_module
from pocketscope.data.db import (
    get_connection,
    query_airports,
    query_runways,
    query_states,
)
from pocketscope.data.spatial import approx_bbox_for_radius

RADIUS_MI = 150.0


@dataclass
class MapDataProvider:
    db_path: str
    cache: cache_module.LRUCache

    def __init__(self, db_path: str, cache: cache_module.LRUCache | None = None) -> None:
        self.db_path = db_path
        self.cache = cache or cache_module.LRUCache()

    def get_features_near(self, lat: float, lon: float, extra_airports: list[str] | None = None) -> Dict[str, Any]:
        # Include extra_airports in cache key to ensure proper cache isolation
        extra_key = tuple(sorted(extra_airports)) if extra_airports else ()
        key = cache_module.key_for_center(lat, lon, RADIUS_MI) + str(hash(extra_key))
        cached = self.cache.get(key)
        if cached is not None:
            # cached is stored as a plain dict-like structure; cast for mypy
            return cast(Dict[str, Any], cached)

        minx, miny, maxx, maxy = approx_bbox_for_radius(lat, lon, RADIUS_MI * 1.05)

        with get_connection(self.db_path) as conn:
            airports = query_airports(conn, lat, lon, minx, miny, maxx, maxy, RADIUS_MI, extra_airports)
            runways = query_runways(conn, lat, lon, minx, miny, maxx, maxy, RADIUS_MI)
            states = query_states(conn, minx, miny, maxx, maxy)

        result = {
            "airports": airports,
            "runways": runways,
            "states": states,
        }
        self.cache.put(key, result)
        return result
