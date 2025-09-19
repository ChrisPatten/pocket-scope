"""Small movement-aware cache for map frame queries."""
from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any


def key_for_center(lat: float, lon: float, radius_mi: float) -> str:
    grid = max(5.0, radius_mi / 8.0)
    lat_step = grid / 69.0
    lon_scale = 69.0 * max(math.cos(math.radians(lat)), 1e-6)
    lon_step = grid / lon_scale
    qlat = round(lat / lat_step, 3)
    qlon = round(lon / lon_step, 3)
    return f"{qlat}:{qlon}:{int(radius_mi)}"


class LRUCache:
    def __init__(self, max_entries: int = 64) -> None:
        self.max_entries = max_entries
        self._data: "OrderedDict[str, Any]" = OrderedDict()

    def get(self, key: str) -> Any | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
