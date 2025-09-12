"""Centralized value sets loaded from YAML.

This module provides a single place to access ordered enumerations and
numeric ladders that were previously hard-coded across the codebase.
The master source is ``values.yml`` in this package.

On import we attempt to load and parse the YAML. Failures fall back to
defensive hard-coded defaults so the application can still run. These
fallbacks intentionally mirror the historical literals to retain test
stability if the YAML is missing or corrupt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple, cast

try:  # Attempt to import PyYAML; tolerate absence (optional dependency)
    import yaml
except Exception:  # pragma: no cover - PyYAML optional until added to deps
    yaml = cast(Any, None)

_PKG_DIR = Path(__file__).parent
_YAML_PATH = _PKG_DIR / "values.yml"

# --- Fallback literals (legacy behavior) ---------------------------------
_FALLBACK_UNITS_ORDER = ["nm_ft_kt", "mi_ft_mph", "km_m_kmh"]
_FALLBACK_RANGE_LADDER = [2.0, 5.0, 10.0, 20.0, 40.0, 80.0]
_FALLBACK_TRACK_PRESETS_S = [15.0, 45.0, 120.0]
_FALLBACK_ALT_FILTER_ORDER = ["All", "0–5k", "5–10k", "10–20k", ">20k"]
_FALLBACK_ALT_FILTER_BANDS = {
    "All": (None, None),
    "0–5k": (0.0, 5000.0),
    "5–10k": (5000.0, 10000.0),
    "10–20k": (10000.0, 20000.0),
    ">20k": (20000.0, None),
}
_FALLBACK_AUTO_RING_CFG = {
    "nice_pattern": [1, 2, 5],
    "min_gap_fraction": 0.10,
    "max_inner_rings": 3,
    "legacy_special_cases": {"10.0": [2.0, 5.0, 10.0]},
    "min_exp": -2,
}

# New fallback domains -------------------------------------------------------
_FALLBACK_THEME = {
    "colors": {
        "ppi": {
            "background": [0, 0, 0, 255],
            "rings": [80, 80, 80, 255],
            "ownship": [255, 255, 255, 255],
            "trails": [0, 180, 255, 180],
            "aircraft": [255, 255, 0, 255],
            "labels": [255, 255, 255, 255],
            "datablock": [0, 255, 0, 255],
        },
        "settings_screen": {
            "bg": [0, 0, 0, 255],
            "hilite": [0, 120, 0, 255],
            "text": [255, 255, 255, 255],
            "title_bg": [24, 24, 24, 255],
            "title_fg": [255, 255, 255, 255],
        },
        "softkeys": {
            "bg": [32, 32, 32, 255],
            "text": [255, 255, 255, 255],
            "border": [255, 0, 0, 255],
        },
        "status_overlay": {
            "bg": [32, 32, 32, 180],
            "text": [255, 255, 255, 255],
            "border": [255, 255, 255, 255],
        },
        "airports_layer": {
            "marker": [160, 160, 160, 255],
            "label": [255, 255, 255, 255],
        },
    }
}
_FALLBACK_ZOOM_LIMITS = {"min_range_nm": 2.0, "max_range_nm": 80.0}
_FALLBACK_TRACK_SERVICE = {
    "trail_len_default_s": 60.0,
    "trail_len_pinned_s": 180.0,
    "expiry_s": 300.0,
}
_FALLBACK_PPI_FMT = {
    "range_ring_label": {
        "offset_x_px": 4,
        "offset_y_px": -8,
        "char_width_em": 0.6,
        "padding_px": 4,
    },
    "typography": {
        "label_font_px": 12,
        "line_gap_px": 2,
        "block_pad_px": 2,
    },
    "rotation_step_deg": 5.0,
}
_FALLBACK_SETTINGS_SCREEN = {"font_multiplier": 1.2, "base_font_px": 12}
_FALLBACK_STATUS_OVERLAY = {
    "elements": {
        "line1": ["GPS", "IMU", "DEC", "RNG"],
        "line2": ["CLOCK", "LAT", "LON"],
        "demo_line": "DEMO MODE",
    },
    "enabled": True,
}


# --- Dataclasses ---------------------------------------------------------
@dataclass(slots=True)
class AltitudeBand:
    name: str
    min_ft: float | None
    max_ft: float | None


# --- Load YAML -----------------------------------------------------------
_units_order: List[str] = list(_FALLBACK_UNITS_ORDER)
_range_ladder: List[float] = list(_FALLBACK_RANGE_LADDER)
_track_length_presets_s: List[float] = list(_FALLBACK_TRACK_PRESETS_S)
_altitude_bands: Dict[str, Tuple[float | None, float | None]] = dict(
    _FALLBACK_ALT_FILTER_BANDS
)
_altitude_cycle_order: List[str] = list(_FALLBACK_ALT_FILTER_ORDER)
_auto_ring_cfg: Dict[str, Any] = dict(_FALLBACK_AUTO_RING_CFG)
_theme: Dict[str, Any] = dict(_FALLBACK_THEME)
_zoom_limits: Dict[str, float] = dict(_FALLBACK_ZOOM_LIMITS)
_track_service_defaults: Dict[str, float] = dict(_FALLBACK_TRACK_SERVICE)
_ppi_cfg: Dict[str, Any] = dict(_FALLBACK_PPI_FMT)
_settings_screen_cfg: Dict[str, Any] = dict(_FALLBACK_SETTINGS_SCREEN)
_status_overlay_cfg: Dict[str, Any] = dict(_FALLBACK_STATUS_OVERLAY)

if yaml is not None and _YAML_PATH.exists():  # pragma: no branch - simple path
    try:
        with _YAML_PATH.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        # Units
        units = raw.get("units", {})
        order = units.get("order")
        if isinstance(order, list) and all(isinstance(x, str) for x in order):
            _units_order = list(order)
        # Ranges
        ranges = raw.get("ranges", {})
        ladder = ranges.get("default_ladder_nm")
        if isinstance(ladder, list):
            try:
                _range_ladder = [float(x) for x in ladder]
            except Exception:
                pass
        auto_cfg = ranges.get("auto_rings", {})
        if isinstance(auto_cfg, dict):
            _auto_ring_cfg.update(auto_cfg)
        # Tracks
        tracks = raw.get("tracks", {})
        presets = tracks.get("presets_s")
        if isinstance(presets, list):
            cleaned_p: List[float] = []
            for v in presets:
                try:
                    cleaned_p.append(float(v))
                except Exception:
                    continue
            if cleaned_p:
                _track_length_presets_s = cleaned_p
        # Altitude filters
        af = raw.get("altitude_filters", {})
        bands = af.get("bands")
        if isinstance(bands, list):
            temp: Dict[str, Tuple[float | None, float | None]] = {}
            for b in bands:
                if not isinstance(b, dict):
                    continue
                name = b.get("name")
                if not isinstance(name, str):
                    continue
                lo = b.get("min_ft")
                hi = b.get("max_ft")
                if lo is not None:
                    try:
                        lo = float(lo)
                    except Exception:
                        lo = None
                if hi is not None:
                    try:
                        hi = float(hi)
                    except Exception:
                        hi = None
                temp[name] = (lo, hi)
            if temp:
                _altitude_bands = temp
        cycle = af.get("cycle_order")
        if isinstance(cycle, list) and all(isinstance(x, str) for x in cycle):
            _altitude_cycle_order = list(cycle)
        # Theme
        theme = raw.get("theme")
        if isinstance(theme, dict):
            _theme = theme | {
                "colors": {
                    **_FALLBACK_THEME.get("colors", {}),
                    **theme.get("colors", {}),
                }
            }
        # Zoom
        zoom = raw.get("zoom")
        if isinstance(zoom, dict):
            _zoom_limits.update(
                {
                    k: float(v)
                    for k, v in zoom.items()
                    if k in {"min_range_nm", "max_range_nm"}
                    and isinstance(v, (int, float))
                }
            )
        # Tracks service defaults (under tracks.service_defaults)
        trk = raw.get("tracks", {})
        if isinstance(trk, dict):
            svc = trk.get("service_defaults")
            if isinstance(svc, dict):
                for k in ("trail_len_default_s", "trail_len_pinned_s", "expiry_s"):
                    v = svc.get(k)
                    if isinstance(v, (int, float)):
                        _track_service_defaults[k] = float(v)
        # PPI config
        ppi = raw.get("ppi")
        if isinstance(ppi, dict):
            # shallow merge per subsection
            rr = ppi.get("range_ring_label")
            if isinstance(rr, dict):
                _ppi_cfg["range_ring_label"].update(rr)
            ty = ppi.get("typography")
            if isinstance(ty, dict):
                _ppi_cfg["typography"].update(ty)
            if isinstance(ppi.get("rotation_step_deg"), (int, float)):
                _ppi_cfg["rotation_step_deg"] = float(ppi["rotation_step_deg"])
        # Settings screen
        ss = raw.get("settings_screen")
        if isinstance(ss, dict):
            for k in ("font_multiplier", "base_font_px"):
                v = ss.get(k)
                if isinstance(v, (int, float)):
                    _settings_screen_cfg[k] = float(v)
        # Status overlay
        so = raw.get("status_overlay")
        if isinstance(so, dict):
            els = so.get("elements")
            if isinstance(els, dict):
                _status_overlay_cfg["elements"].update(
                    {
                        k: v
                        for k, v in els.items()
                        if k in {"line1", "line2", "demo_line"}
                    }
                )
            en = so.get("enabled")
            if isinstance(en, bool):
                _status_overlay_cfg["enabled"] = en
    except Exception:  # pragma: no cover - defensive parse guard
        pass

# --- Public accessors ----------------------------------------------------
UNITS_ORDER: Sequence[str] = tuple(_units_order)
RANGE_LADDER_NM: Sequence[float] = tuple(_range_ladder)
TRACK_LENGTH_PRESETS_S: Sequence[float] = tuple(_track_length_presets_s)
ALTITUDE_FILTER_BANDS: Dict[str, Tuple[float | None, float | None]] = dict(
    _altitude_bands
)
ALTITUDE_FILTER_CYCLE_ORDER: Sequence[str] = tuple(_altitude_cycle_order)
AUTO_RING_CONFIG: Dict[str, Any] = dict(_auto_ring_cfg)
THEME: Dict[str, Any] = dict(_theme)
ZOOM_LIMITS: Dict[str, float] = dict(_zoom_limits)
TRACK_SERVICE_DEFAULTS: Dict[str, float] = dict(_track_service_defaults)
PPI_CONFIG: Dict[str, Any] = dict(_ppi_cfg)
SETTINGS_SCREEN_CONFIG: Dict[str, Any] = dict(_settings_screen_cfg)
STATUS_OVERLAY_CONFIG: Dict[str, Any] = dict(_status_overlay_cfg)

__all__ = [
    "UNITS_ORDER",
    "RANGE_LADDER_NM",
    "TRACK_LENGTH_PRESETS_S",
    "ALTITUDE_FILTER_BANDS",
    "ALTITUDE_FILTER_CYCLE_ORDER",
    "AUTO_RING_CONFIG",
    "THEME",
    "ZOOM_LIMITS",
    "TRACK_SERVICE_DEFAULTS",
    "PPI_CONFIG",
    "SETTINGS_SCREEN_CONFIG",
    "STATUS_OVERLAY_CONFIG",
]
