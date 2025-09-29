"""Theme system providing named color palette access (relocated module).

This is the canonical location for theming; older imports from
``pocketscope.ui.theme`` should be updated to ``pocketscope.theme``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from importlib.abc import Traversable
from math import isfinite
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

import yaml

ColorTuple = Tuple[int, int, int, int]


@dataclass(slots=True)
class Color:
    r: int
    g: int
    b: int
    a: int = 255

    def clamp(self) -> "Color":  # pragma: no cover
        self.r = max(0, min(255, int(self.r)))
        self.g = max(0, min(255, int(self.g)))
        self.b = max(0, min(255, int(self.b)))
        self.a = max(0, min(255, int(self.a)))
        return self

    def as_tuple(self) -> ColorTuple:
        return (int(self.r), int(self.g), int(self.b), int(self.a))


def _lerp_color(a: Color, b: Color, fraction: float) -> Color:
    f = 0.0 if fraction < 0.0 else 1.0 if fraction > 1.0 else float(fraction)
    return Color(
        r=int(round(a.r + (b.r - a.r) * f)),
        g=int(round(a.g + (b.g - a.g) * f)),
        b=int(round(a.b + (b.b - a.b) * f)),
        a=int(round(a.a + (b.a - a.a) * f)),
    ).clamp()


@dataclass(slots=True)
class TrackSpeedScale:
    stops: Tuple[Tuple[float, Color], ...]

    def color_for_speed(self, value: float | None) -> Color:
        stops = self.stops
        if not stops:
            return Color(255, 255, 255, 255)
        if value is None:
            return stops[0][1]
        try:
            v = float(value)
        except Exception:
            return stops[0][1]
        if not isfinite(v):
            return stops[0][1]
        if v <= stops[0][0]:
            return stops[0][1]
        last_val, last_color = stops[-1]
        if v >= last_val:
            return last_color
        for (v0, c0), (v1, c1) in zip(stops[:-1], stops[1:]):
            if v1 <= v0:
                continue
            if v <= v1:
                frac = (v - v0) / (v1 - v0)
                return _lerp_color(c0, c1, frac)
        return last_color


@dataclass(slots=True)
class Theme:
    name: str
    palette: Dict[str, Color]
    track_speed_scale: TrackSpeedScale

    def color(self, key: str) -> Color:
        try:
            return self.palette[key]
        except KeyError as e:  # pragma: no cover
            raise KeyError(f"Theme '{self.name}' missing color key '{key}'") from e

    def track_speed_color(self, speed_kt: float | None) -> ColorTuple:
        return self.track_speed_scale.color_for_speed(speed_kt).as_tuple()


REQUIRED_KEYS = [
    "bg",
    "range.ring",
    "range.tick",
    "range.text",
    "map.border",
    "ac.level.fill",
    "ac.level.stroke",
    "ac.climb.fill",
    "ac.desc.fill",
    "ac.pinned.stroke",
    "ac.focus.stroke",
    "label.text.primary",
    "label.text.dim",
    "label.text.other",  # non-focused simple labels
    "label.halo",
    "track.head",
    "airport.marker",
    "airport.text",
    "status.bg",
    "status.text",
    "infobar.accent",
    # Vertical profile panel
    "vprof.bg",
    "vprof.border",
    "vprof.text",
    "vprof.muted",
    "vprof.axis",
    "vprof.trace",
    "vprof.vs.pos",
    "vprof.vs.neg",
    "vprof.vs.neutral",
    # Sectors layer
    "sector.line",
    "sector.label",
    # Status badges
    "status.badge.live.bg",
    "status.badge.live.text",
    "status.badge.delay.bg",
    "status.badge.delay.text",
    "status.badge.stale.bg",
    "status.badge.stale.text",
]


THEMES: Dict[str, Dict[str, str]] = {}
_THEME_DEFINITIONS: Dict[str, Dict[str, Any]] | None = None


def _theme_file_candidates() -> Sequence[Path | Traversable]:
    candidates: list[Path | Traversable] = []
    try:
        candidates.append(resources.files(__package__).joinpath("themes.yml"))
    except Exception:  # pragma: no cover - importlib.resources quirks
        pass
    candidates.append(Path(__file__).with_name("themes.yml"))
    home = os.environ.get("POCKETSCOPE_HOME")
    if home:
        user_dir = Path(home).expanduser()
    else:
        user_dir = Path.home() / ".pocketscope"
    candidates.append(user_dir / "themes.yml")
    return candidates


def _merge_theme_data(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge raw theme data dictionaries.

    Behaviour requirements:
    - Values from later (user) files monkeypatch / overlay earlier (builtin) ones.
    - For the ``themes`` mapping:
        * If a theme only exists in override, add it.
        * If a theme exists in both, perform a deep merge of its mapping.
          - ``palette`` mappings are *patched* key-by-key (missing keys retained).
          - Any list (sequence) value (e.g. ``track_speed_scale.stops``) is treated
            as an enumeration and replaced wholesale if the override supplies it.
          - Nested mappings (except palette special case) are merged recursively.
          - Scalar values are replaced.
    - Non-"themes" top-level keys are simply overridden.
    """

    def deep_merge_theme(base_theme: Mapping[str, Any], override_theme: Mapping[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = dict(base_theme)
        for k, v in override_theme.items():
            if k == "palette" and isinstance(v, Mapping):
                # Monkeypatch palette entries only; retain unspecified ones
                existing = result.get("palette")
                if isinstance(existing, Mapping):
                    patched = dict(existing)
                else:
                    patched = {}
                for pk, pv in v.items():
                    if isinstance(pk, str):
                        patched[pk] = pv
                result["palette"] = patched
            else:
                base_val = result.get(k)
                # Lists (enumerations) replaced wholesale
                if isinstance(v, list):
                    result[k] = list(v)
                # Recurse for mappings (unless palette handled above)
                elif isinstance(v, Mapping) and isinstance(base_val, Mapping):
                    result[k] = deep_merge_theme(base_val, v)
                else:
                    result[k] = v
        return result

    merged: Dict[str, Any] = dict(base)
    themes: Dict[str, Any] = {}
    base_themes = base.get("themes")
    if isinstance(base_themes, Mapping):
        for name, cfg in base_themes.items():
            if isinstance(name, str) and isinstance(cfg, Mapping):
                themes[name] = dict(cfg)
    override_themes = override.get("themes") if isinstance(override, Mapping) else None
    if isinstance(override_themes, Mapping):
        for name, cfg in override_themes.items():
            if not (isinstance(name, str) and isinstance(cfg, Mapping)):
                continue
            existing = themes.get(name)
            if isinstance(existing, Mapping):
                themes[name] = deep_merge_theme(existing, cfg)
            else:
                themes[name] = dict(cfg)
    merged["themes"] = themes
    # Top-level non-theme keys: straight override
    for k, v in override.items():
        if k == "themes":
            continue
        merged[k] = v
    return merged


def _load_raw_theme_data() -> Mapping[str, Any]:
    last_error: Exception | None = None
    merged: Dict[str, Any] = {}
    found = False
    for candidate in _theme_file_candidates():
        try:
            text = candidate.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except Exception as exc:  # pragma: no cover - propagate last error
            last_error = exc
            continue
        try:
            data = yaml.safe_load(text) or {}
        except Exception as exc:
            last_error = exc
            continue
        if isinstance(data, Mapping):
            merged = _merge_theme_data(merged, data)
            found = True
    if found:
        return merged
    if last_error is not None:
        raise RuntimeError("Failed to load theme definitions") from last_error
    raise FileNotFoundError("Theme definition file 'themes.yml' not found")


def _ensure_theme_definitions() -> Dict[str, Dict[str, Any]]:
    global _THEME_DEFINITIONS
    if _THEME_DEFINITIONS is not None:
        return _THEME_DEFINITIONS
    raw = _load_raw_theme_data()
    themes = raw.get("themes") if isinstance(raw, Mapping) else None
    if not isinstance(themes, Mapping):
        raise ValueError("Theme file missing 'themes' mapping")
    cleaned: Dict[str, Dict[str, Any]] = {}
    for name, cfg in themes.items():
        if isinstance(name, str) and isinstance(cfg, Mapping):
            cleaned[name] = dict(cfg)
    if not cleaned:
        raise ValueError("No theme definitions found in themes.yml")
    THEMES.clear()
    for name, cfg in cleaned.items():
        palette = cfg.get("palette")
        if isinstance(palette, Mapping):
            THEMES[name] = {str(k): str(v) for k, v in palette.items() if isinstance(k, str)}
    _THEME_DEFINITIONS = cleaned
    return cleaned


def _fallback_track_speed_scale(palette: Mapping[str, Color]) -> TrackSpeedScale:
    base = palette.get("track.head")
    if base is None:
        base = hex_color("#FFFFFF")
    return TrackSpeedScale(((0.0, base), (500.0, base)))


def _parse_track_speed_scale(cfg: Mapping[str, Any] | None, palette: Mapping[str, Color]) -> TrackSpeedScale:
    if not isinstance(cfg, Mapping):
        return _fallback_track_speed_scale(palette)

    def _parse_stop(data: Mapping[str, Any]) -> Tuple[float, Color]:
        if not isinstance(data, Mapping):
            raise ValueError
        value = data.get("value")
        color = data.get("color")
        if value is None or color is None:
            raise ValueError
        return float(value), hex_color(str(color))

    try:
        low_val = _parse_stop(cfg.get("low", {}))
        high_val = _parse_stop(cfg.get("high", {}))
    except Exception:
        return _fallback_track_speed_scale(palette)

    stops: list[Tuple[float, Color]] = [low_val]
    raw_stops = cfg.get("stops", [])
    if isinstance(raw_stops, Sequence):
        for entry in raw_stops:
            if not isinstance(entry, Mapping):
                continue
            try:
                stops.append(_parse_stop(entry))
            except Exception:
                continue
    stops.append(high_val)
    stops.sort(key=lambda item: item[0])
    deduped: list[Tuple[float, Color]] = []
    for value, color in stops:
        if not deduped or value != deduped[-1][0]:
            deduped.append((value, color))
        else:
            deduped[-1] = (value, color)
    if len(deduped) < 2:
        return _fallback_track_speed_scale(palette)
    return TrackSpeedScale(tuple(deduped))


def hex_color(value: str, a: int | None = None) -> Color:
    if not isinstance(value, str):
        raise ValueError("hex_color expects str input")
    s = value.strip()
    if not s:
        raise ValueError("empty color string")
    if s[0] == "#":
        s = s[1:]
    if len(s) in (3, 4):
        s = "".join(ch * 2 for ch in s)
    if len(s) not in (6, 8):
        raise ValueError(f"invalid hex color length for '{value}'")
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    alpha = int(s[6:8], 16) if len(s) == 8 else 255
    if a is not None:
        alpha = int(a)
    return Color(r, g, b, alpha).clamp()


def to_rgb565(c: Color | ColorTuple) -> int:
    if isinstance(c, tuple):
        r, g, b = int(c[0]), int(c[1]), int(c[2])
    else:
        r, g, b = int(c.r), int(c.g), int(c.b)
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


class ThemeManager:
    _theme: Theme | None = None
    _rgb565_cache: Dict[str, int] = {}

    @classmethod
    def load(cls, settings: Mapping[str, object] | None) -> "type[ThemeManager]":
        definitions = _ensure_theme_definitions()
        name = "atc_classic"
        overrides: Mapping[str, str] = {}
        if settings:
            tname = settings.get("theme")
            if isinstance(tname, str) and tname.strip():
                name = tname.strip()
            ov = settings.get("themeOverrides")
            if isinstance(ov, Mapping):
                overrides = {k: str(v) for k, v in ov.items() if isinstance(k, str)}
        base_def = definitions.get(name)
        if base_def is None:
            fallback_name = "atc_classic" if "atc_classic" in definitions else None
            if fallback_name is None:
                fallback_name = next(iter(definitions.keys()))
            base_def = definitions[fallback_name]
            name = fallback_name
        palette_def = base_def.get("palette") if isinstance(base_def, Mapping) else {}
        if not isinstance(palette_def, Mapping):
            palette_def = {}
        palette: Dict[str, Color] = {}
        for key, value in palette_def.items():
            if not isinstance(key, str):
                continue
            source = overrides.get(key, value)
            try:
                palette[key] = hex_color(str(source))
            except Exception:
                palette[key] = hex_color(str(value))
        scale_cfg = base_def.get("track_speed_scale") if isinstance(base_def, Mapping) else None
        scale = _parse_track_speed_scale(scale_cfg if isinstance(scale_cfg, Mapping) else None, palette)
        cls._theme = Theme(name=name, palette=palette, track_speed_scale=scale)
        cls._rgb565_cache.clear()
        return cls

    @classmethod
    def reload(cls, settings: Mapping[str, object] | None) -> None:
        global _THEME_DEFINITIONS
        _THEME_DEFINITIONS = None
        THEMES.clear()
        cls._theme = None
        cls._rgb565_cache.clear()
        cls.load(settings)

    @classmethod
    def theme(cls) -> Theme:
        if cls._theme is None:
            cls.load({})
        assert cls._theme is not None
        return cls._theme

    @classmethod
    def color(cls, key: str) -> ColorTuple:
        c = cls.theme().color(key)
        return c.as_tuple()

    @classmethod
    def track_speed_color(cls, speed_kt: float | None) -> ColorTuple:
        return cls.theme().track_speed_color(speed_kt)

    @classmethod
    def rgb565(cls, key: str) -> int:
        val = cls._rgb565_cache.get(key)
        if val is not None:
            return val
        packed = to_rgb565(cls.theme().color(key))
        cls._rgb565_cache[key] = packed
        return packed


# Populate theme registry on import for compatibility with legacy callers that
# inspect ``THEMES`` directly without invoking ``ThemeManager`` first.
_ensure_theme_definitions()


__all__ = [
    "Color",
    "ColorTuple",
    "Theme",
    "TrackSpeedScale",
    "ThemeManager",
    "hex_color",
    "to_rgb565",
    "REQUIRED_KEYS",
    "THEMES",
]
