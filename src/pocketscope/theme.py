"""Theme system providing named color palette access (relocated module).

This is the canonical location for theming; older imports from
``pocketscope.ui.theme`` should be updated to ``pocketscope.theme``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Tuple

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


@dataclass(slots=True)
class Theme:
    name: str
    palette: Dict[str, Color]

    def color(self, key: str) -> Color:
        try:
            return self.palette[key]
        except KeyError as e:  # pragma: no cover
            raise KeyError(f"Theme '{self.name}' missing color key '{key}'") from e


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


THEMES: Dict[str, Dict[str, str]] = {
    "atc_classic": {
        # Background + grid
        "bg": "#0A0D0A",
        "range.ring": "#1E4A1E",
        "range.tick": "#2B6A2B",
        "range.text": "#2B6A2B",
        # Minimal cartography
        "map.border": "#2F742F",
        # Aircraft symbology
        "ac.level.fill": "#7CFF7C",
        "ac.level.stroke": "#C8FFC8",
        "ac.climb.fill": "#9AFF9A",
        "ac.desc.fill": "#66E966",
        "ac.pinned.stroke": "#FFFFFF",
        "ac.focus.stroke": "#D6FFD6",
        # Labels
        "label.text.primary": "#C8FFC8",
        "label.text.dim": "#86D986",
        # Non-focused simple labels
        "label.text.other": "#86D986",
        "label.halo": "#051005",  # dark halo to boost contrast
        # Track: tail now implicitly fades toward bg color; only head color needed
        "track.head": "#6EFF6E",
        # Airports (kept subdued to avoid competing with traffic)
        "airport.marker": "#2B6A2B",
        "airport.text": "#2B6A2B",
        # Status + Info bar (infobar currently only uses accent as a generic border)
        "status.bg": "#051305",
        "status.text": "#B9FFB9",
        "infobar.accent": "#FFFFFF",
        # Vertical profile (harmonized to classic green theme)
        "vprof.bg": "#121212EB",
        "vprof.border": "#2B6A2B",
        "vprof.text": "#E0FFE0",
        "vprof.muted": "#8AD88A",
        "vprof.axis": "#3A6A3A",
        "vprof.trace": "#6EFF6E",
        "vprof.vs.pos": "#7CFF7C",
        "vprof.vs.neg": "#D73C3C",
        "vprof.vs.neutral": "#86D986",
        # Sectors
        "sector.line": "#80808064",
        "sector.label": "#FFFFFFDC",
        # Status badges
        "status.badge.live.bg": "#00A000",
        "status.badge.live.text": "#FFFFFF",
        "status.badge.delay.bg": "#FFC857",  # match alert amber
        "status.badge.delay.text": "#000000",
        "status.badge.stale.bg": "#C80000",
        "status.badge.stale.text": "#FFFFFF",
    },
    "monokai": {
        # Background + grid (Monokai base #272822, softened contrast on rings/ticks)
        "bg": "#272822",
        "range.ring": "#4B4D44",  # between #75715E (comments) and bg
        "range.tick": "#5B5D54",
        "range.text": "#75715E",  # Monokai "comment" tone
        # Minimal cartography
        "map.border": "#5B5D54",
        # Aircraft symbology
        "ac.level.fill": "#A6E22E",  # Monokai green
        "ac.level.stroke": "#F8F8F2",  # editor fg for crisp outlines
        "ac.climb.fill": "#66D9EF",  # cyan
        "ac.desc.fill": "#FD971F",  # orange (reads as caution)
        "ac.pinned.stroke": "#F8F8F2",
        "ac.focus.stroke": "#E6DB74",  # yellow accent
        # Labels
        "label.text.primary": "#F8F8F2",
        "label.text.dim": "#CFCFC2",
        "label.text.other": "#A1A190",
        "label.halo": "#141411",
        # Track (tail fades toward bg)
        "track.head": "#AE81FF",  # purple track—distinct from symbology hues
        # Airports (subdued)
        "airport.marker": "#5B5D54",
        "airport.text": "#5B5D54",
        # Status + Info bar
        "status.bg": "#1F201C",
        "status.text": "#E6DB74",
        "infobar.accent": "#66D9EF",  # thin cyan border/readout accents
        # Vertical profile
        "vprof.bg": "#1E1F1CEB",
        "vprof.border": "#5B5D54",
        "vprof.text": "#F8F8F2",
        "vprof.muted": "#A1A190",
        "vprof.axis": "#5B5D54",
        "vprof.trace": "#66D9EF",
        "vprof.vs.pos": "#A6E22E",
        "vprof.vs.neg": "#F92672",  # Monokai pink/red for descent
        "vprof.vs.neutral": "#CFCFC2",
        # Sectors
        "sector.line": "#75715E64",
        "sector.label": "#F8F8F2DC",
        # Status badges
        "status.badge.live.bg": "#A6E22E",
        "status.badge.live.text": "#1B1C19",
        "status.badge.delay.bg": "#FD971F",
        "status.badge.delay.text": "#1B1C19",
        "status.badge.stale.bg": "#F92672",
        "status.badge.stale.text": "#FFFFFF",
    },
    "light_chart": {
        # Background + grid (warm light gray background, mid-gray grid for visibility)
        "bg": "#E6E6E6",
        "range.ring": "#B0B0B0",
        "range.tick": "#A0A0A0",
        "range.text": "#606060",
        # Minimal cartography
        "map.border": "#909090",
        # Aircraft symbology (dark outlines, saturated fills so they don’t wash out)
        "ac.level.fill": "#005F99",  # strong blue for level
        "ac.level.stroke": "#000000",
        "ac.climb.fill": "#228B22",  # green for climb
        "ac.desc.fill": "#B22222",  # red for descent
        "ac.pinned.stroke": "#FF8C00",  # orange outline
        "ac.focus.stroke": "#000000",  # black outline for crispness
        # Labels
        "label.text.primary": "#000000",
        "label.text.dim": "#404040",
        "label.text.other": "#404040",
        "label.halo": "#F0F0F0",  # subtle halo so text stands off
        # Track
        "track.head": "#444444",  # dark gray, fades naturally toward bg
        # Airports
        "airport.marker": "#808080",
        "airport.text": "#606060",
        # Status + Info bar
        "status.bg": "#D0D0D0",
        "status.text": "#000000",
        "infobar.accent": "#005F99",  # blue accent
        # Vertical profile
        "vprof.bg": "#F0F0F0EB",
        "vprof.border": "#A0A0A0",
        "vprof.text": "#000000",
        "vprof.muted": "#606060",
        "vprof.axis": "#808080",
        "vprof.trace": "#005F99",
        "vprof.vs.pos": "#228B22",
        "vprof.vs.neg": "#B22222",
        "vprof.vs.neutral": "#606060",
        # Sectors
        "sector.line": "#A0A0A064",
        "sector.label": "#000000DC",
        # Status badges
        "status.badge.live.bg": "#228B22",
        "status.badge.live.text": "#FFFFFF",
        "status.badge.delay.bg": "#FF8C00",
        "status.badge.delay.text": "#000000",
        "status.badge.stale.bg": "#B22222",
        "status.badge.stale.text": "#FFFFFF",
    },
    "vfr_sectional": {
        # Background + range grid (chart paper feel)
        "bg": "#EDE3CC",
        "range.ring": "#8A8F8F",
        "range.tick": "#717679",
        "range.text": "#5A5F61",
        # Minimal cartography (muted sectional blue)
        "map.border": "#6B7EA4",
        # Aircraft symbology (dark inks + sectional accents)
        "ac.level.fill": "#003E7E",  # deep navy
        "ac.level.stroke": "#000000",  # crisp outline
        "ac.climb.fill": "#2E7D32",  # sectional green
        "ac.desc.fill": "#C62828",  # sectional red
        "ac.pinned.stroke": "#C2185B",  # sectional magenta
        "ac.focus.stroke": "#F2C84B",  # sectional "yellow" highlight
        # Labels
        "label.text.primary": "#1A1A1A",
        "label.text.dim": "#4A4A4A",
        "label.text.other": "#4A4A4A",
        "label.halo": "#F6EFE0",
        # Track (subdued ink line)
        "track.head": "#5B6D7C",
        # Airports (sectional blue)
        "airport.marker": "#2F5DAA",
        "airport.text": "#2F5DAA",
        # Status + Info bar
        "status.bg": "#D8D2B8",
        "status.text": "#1A1A1A",
        "infobar.accent": "#C2185B",  # magenta accent/border
        # Vertical profile
        "vprof.bg": "#F5EBD5EB",
        "vprof.border": "#6B7EA4",
        "vprof.text": "#1A1A1A",
        "vprof.muted": "#6B6B6B",
        "vprof.axis": "#8A8F8F",
        "vprof.trace": "#2F5DAA",
        "vprof.vs.pos": "#2E7D32",
        "vprof.vs.neg": "#C62828",
        "vprof.vs.neutral": "#6B6B6B",
        # Sectors
        "sector.line": "#6B7EA466",
        "sector.label": "#000000DC",
        # Status badges
        "status.badge.live.bg": "#2E7D32",
        "status.badge.live.text": "#FFFFFF",
        "status.badge.delay.bg": "#F2C84B",
        "status.badge.delay.text": "#1A1A1A",
        "status.badge.stale.bg": "#C62828",
        "status.badge.stale.text": "#FFFFFF",
    },
}


class ThemeManager:
    _theme: Theme | None = None
    _rgb565_cache: Dict[str, int] = {}

    @classmethod
    def load(cls, settings: Mapping[str, object] | None) -> "type[ThemeManager]":
        name = "atc_classic"
        overrides: Mapping[str, str] = {}
        if settings:
            tname = settings.get("theme")
            if isinstance(tname, str) and tname.strip():
                name = tname.strip()
            ov = settings.get("themeOverrides")
            if isinstance(ov, Mapping):
                overrides = {k: str(v) for k, v in ov.items() if isinstance(k, str)}
        base = THEMES.get(name) or THEMES["atc_classic"]
        if base is None:  # pragma: no cover
            base = THEMES["atc_classic"]
            name = "atc_classic"
        palette: Dict[str, Color] = {}
        for k, v in base.items():
            try:
                palette[k] = hex_color(overrides.get(k, v))
            except Exception:
                palette[k] = hex_color(v)
        cls._theme = Theme(name=name, palette=palette)
        cls._rgb565_cache.clear()
        return cls

    @classmethod
    def reload(cls, settings: Mapping[str, object] | None) -> None:
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
    def rgb565(cls, key: str) -> int:
        val = cls._rgb565_cache.get(key)
        if val is not None:
            return val
        packed = to_rgb565(cls.theme().color(key))
        cls._rgb565_cache[key] = packed
        return packed


__all__ = [
    "Color",
    "ColorTuple",
    "Theme",
    "ThemeManager",
    "hex_color",
    "to_rgb565",
    "REQUIRED_KEYS",
    "THEMES",
]
