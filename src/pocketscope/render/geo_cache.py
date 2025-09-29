"""Lightweight geometry projection cache for map/sectors rendering.

Design goals:
 - Pure stdlib (no third‑party deps).
 - Extremely low overhead when disabled (env var) or on miss.
 - Key based on *buckets* so small camera changes reuse work.
 - Additional *validity* check for sub‑bucket center drift (>2px) so
   we invalidate when translation would cause visible jitter.
 - Per‑layer ("sectors" | "states") stats for ui.perf logging.

The cache stores projected screen vertex lists. Each entry can represent
either a single polygon (sector) or a list of rings (state borders).
For simplicity ``screen_pts`` is typed as ``list[list[tuple[int,int]]]``.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Tuple

CacheKey = Tuple[str, str, int, int, float, float, Tuple[int, int]]


def _quant_center(lat: float, lon: float) -> tuple[float, float]:
    # Quantize to ~0.01° (about 0.6 nm at equator).
    return (round(lat, 2), round(lon, 2))


def _bucket_range(range_nm: float) -> int:
    return int(round(range_nm))


def _bucket_rotation(rot_deg: float) -> int:
    return int(round(rot_deg) % 360)


@dataclass(slots=True)
class GeometryCacheEntry:
    key: CacheKey
    screen_pts: list[list[tuple[int, int]]]
    ts: float
    # Raw (un‑quantized) center used for drift detection
    center_lat: float
    center_lon: float
    m_per_px: float
    meta: dict[str, Any] = field(default_factory=dict)


class GeometryCache:
    def __init__(self) -> None:
        self._entries: Dict[CacheKey, GeometryCacheEntry] = {}
        self.hits = 0
        self.misses = 0
        self.invalidations: Dict[str, int] = {}
        self.layer_hits: Dict[str, int] = {"sectors": 0, "states": 0}
        self.layer_misses: Dict[str, int] = {"sectors": 0, "states": 0}

    def _make_key(
        self,
        layer: str,
        obj_id: str,
        range_nm: float,
        rotation_deg: float,
        center_lat: float,
        center_lon: float,
        display_px: tuple[int, int],
    ) -> CacheKey:
        lat_q, lon_q = _quant_center(center_lat, center_lon)
        return (
            layer,
            obj_id,
            _bucket_range(range_nm),
            _bucket_rotation(rotation_deg),
            lat_q,
            lon_q,
            (int(display_px[0]), int(display_px[1])),
        )

    def get_or_build(
        self,
        *,
        layer: str,
        obj_id: str,
        range_nm: float,
        rotation_deg: float,
        center_lat: float,
        center_lon: float,
        display_px: tuple[int, int],
        m_per_px: float,
        build_fn: Callable[[], list[list[tuple[int, int]]]],
    ) -> GeometryCacheEntry:
        """Return cached entry or build a new one."""
        if os.getenv("POCKETSCOPE_DISABLE_GEO_CACHE"):
            pts = build_fn()
            return GeometryCacheEntry(
                key=self._make_key(
                    layer,
                    obj_id,
                    range_nm,
                    rotation_deg,
                    center_lat,
                    center_lon,
                    display_px,
                ),
                screen_pts=pts,
                ts=time.time(),
                center_lat=center_lat,
                center_lon=center_lon,
                m_per_px=m_per_px,
                meta={"bypass": True},
            )
        key = self._make_key(layer, obj_id, range_nm, rotation_deg, center_lat, center_lon, display_px)
        existing = self._entries.get(key)
        if existing is not None:
            # Compute pixel drift of raw center
            d_nm = _haversine_nm(existing.center_lat, existing.center_lon, center_lat, center_lon)
            if d_nm > 0:
                meters = d_nm * 1852.0
                px = meters / max(1e-9, m_per_px)
                if px <= 2.0:
                    self.hits += 1
                    self.layer_hits[layer] = self.layer_hits.get(layer, 0) + 1
                    return existing
                else:
                    self.invalidations["center_shift"] = self.invalidations.get("center_shift", 0) + 1
            else:
                self.hits += 1
                self.layer_hits[layer] = self.layer_hits.get(layer, 0) + 1
                return existing
        else:
            self.invalidations["key_miss"] = self.invalidations.get("key_miss", 0) + 1
        pts_new = build_fn()
        new_entry = GeometryCacheEntry(
            key=key,
            screen_pts=pts_new,
            ts=time.time(),
            center_lat=center_lat,
            center_lon=center_lon,
            m_per_px=m_per_px,
            meta={},
        )
        self._entries[key] = new_entry
        self.misses += 1
        self.layer_misses[layer] = self.layer_misses.get(layer, 0) + 1
        return new_entry

    def snapshot_and_reset(self) -> dict[str, Any]:
        out = {
            "hits": self.hits,
            "misses": self.misses,
            "invalidations": dict(self.invalidations),
            "layer_hits": dict(self.layer_hits),
            "layer_misses": dict(self.layer_misses),
        }
        self.hits = self.misses = 0
        self.invalidations.clear()
        self.layer_hits = {"sectors": 0, "states": 0}
        self.layer_misses = {"sectors": 0, "states": 0}
        return out


_GLOBAL: GeometryCache | None = None


def global_cache() -> GeometryCache:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = GeometryCache()
    return _GLOBAL


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R_m = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    meters = R_m * c
    return meters / 1852.0


__all__ = [
    "GeometryCacheEntry",
    "GeometryCache",
    "global_cache",
]
