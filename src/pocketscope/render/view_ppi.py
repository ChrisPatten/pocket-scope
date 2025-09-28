"""
PPI (Plan Position Indicator) view rendering.

This module provides a north-up PPI view that renders range rings, ownship,
aircraft glyphs with optional course triangles, trails, and labels. Labels
support two modes:

- Data blocks: ATC-style three-line labels with leader lines, formatted and
    laid out by ``pocketscope.render.labels``. This is the default in the live
    viewer and recommended for rich display.
- Simple labels: one-line callsign/ICAO text near the glyph (legacy mode used
    by golden tests for determinism).

Coordinates and units
---------------------
- center_lat/center_lon: Ownship or scene center in degrees (WGS-84)
- range_nm: Maximum range shown to the edge of the smallest screen dimension/2
- ENU to screen mapping: x = E / m_per_px, y = -N / m_per_px
- Colors are RGBA tuples (0..255)
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, isfinite, radians, sin
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pocketscope.core.geo import (
    ecef_to_enu,
    enu_to_screen,
    geodetic_to_ecef,
    haversine_nm,
    initial_bearing_deg,
)
from pocketscope.render.airports_layer import AirportsLayer
from pocketscope.render.canvas import Canvas, Color
from pocketscope.render.labels import DataBlockFormatter as LabelFormatter
from pocketscope.render.labels import DataBlockLayout as LabelLayout
from pocketscope.render.labels import OwnshipRef
from pocketscope.render.labels import TrackSnapshot as LabelTrack
from pocketscope.settings.values import AUTO_RING_CONFIG, PPI_CONFIG
from pocketscope.theme import ThemeManager

if TYPE_CHECKING:  # for type hints only
    from pocketscope.data.sectors import Sector

_PPI_THEME: dict[str, object] = {}

# ---------------------------------------------------------------------------
# Vertical rate color scale (mirrors vertical_profile gradient).
# Descents (negative FPM) trend toward red; climbs (positive FPM) toward green.
# The scale clamps symmetrically at +/- 3000 fpm to avoid oversaturation.
_VS_COLOR_NEG: Color = (215, 60, 60, 255)
_VS_COLOR_POS: Color = (60, 210, 90, 255)
_VS_COLOR_NEUTRAL: Color = (160, 160, 160, 255)
_VS_CLAMP_ABS_FPM: float = 3000.0


def _vs_color(vs_fpm: float | None) -> Color:
    """Map vertical rate (fpm) -> RGBA gradient color.

    The mapping linearly blends between red (descent) and green (climb) with
    neutral gray when unknown. Values are clamped at +/- _VS_CLAMP_ABS_FPM.
    """
    if not isinstance(vs_fpm, (int, float)) or not isfinite(float(vs_fpm)):
        return _VS_COLOR_NEUTRAL
    mag = max(-_VS_CLAMP_ABS_FPM, min(_VS_CLAMP_ABS_FPM, float(vs_fpm)))
    t = (mag + _VS_CLAMP_ABS_FPM) / (
        2.0 * _VS_CLAMP_ABS_FPM
    )  # [-clamp,+clamp] -> [0,1]
    r = int(round(_VS_COLOR_NEG[0] + t * (_VS_COLOR_POS[0] - _VS_COLOR_NEG[0])))
    g = int(round(_VS_COLOR_NEG[1] + t * (_VS_COLOR_POS[1] - _VS_COLOR_NEG[1])))
    b = int(round(_VS_COLOR_NEG[2] + t * (_VS_COLOR_POS[2] - _VS_COLOR_NEG[2])))
    return (r, g, b, 255)


_M_PER_NM = 1852.0
_FT_TO_M = 0.3048


def _ft_to_m(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) * _FT_TO_M
    except (TypeError, ValueError):
        return None


def _primary_ring(geometry: Any) -> List[List[float]]:
    if not isinstance(geometry, dict):
        return []
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Polygon":
        rings = coords or []
        return list(rings[0]) if rings else []
    if gtype == "MultiPolygon":
        polygons = coords or []
        if not polygons:
            return []
        first = polygons[0]
        return list(first[0]) if first else []
    return []


def _runway_length_bearing(geometry: Any) -> tuple[float | None, float | None]:
    ring = _primary_ring(geometry)
    if len(ring) < 2:
        return (None, None)
    best_pair: tuple[float, float, float, float] | None = None
    max_nm = 0.0
    points = [(float(pt[1]), float(pt[0])) for pt in ring]
    for idx, (lat1, lon1) in enumerate(points):
        for lat2, lon2 in points[idx + 1 :]:
            dist_nm = haversine_nm(lat1, lon1, lat2, lon2)
            if dist_nm > max_nm:
                max_nm = dist_nm
                best_pair = (lat1, lon1, lat2, lon2)
    if best_pair is None or max_nm <= 0.0:
        return (None, None)
    bearing = initial_bearing_deg(*best_pair)
    return (max_nm * _M_PER_NM, bearing)


def _prepare_runway_icons(
    runways: Sequence[Dict[str, Any]]
) -> dict[str, list[Dict[str, Any]]]:
    grouped: dict[str, list[Dict[str, Any]]] = {}
    for rw in runways:
        ident = rw.get("airport_ident")
        if not ident:
            continue
        length_m_prop = _ft_to_m(rw.get("length_ft"))
        width_m_prop = _ft_to_m(rw.get("width_ft"))
        geom = rw.get("geometry")
        geom_length_m, geom_bearing = _runway_length_bearing(geom)
        if length_m_prop is None:
            length_m_prop = geom_length_m
        entry = {
            "length_m": length_m_prop,
            "width_m": width_m_prop,
            "bearing_true": geom_bearing,
            "surface": rw.get("surface"),
            "light_actv": rw.get("light_actv"),
            "light_intns": rw.get("light_intns"),
        }
        key = str(ident).upper()
        grouped.setdefault(key, []).append(entry)
    return grouped


def _on_runtime_update(rc: Any) -> None:  # retained for compatibility
    # ThemeManager is reloaded by config.update_from_settings; nothing needed.
    return None


@dataclass(slots=True)
class TrackSnapshot:
    """Minimal snapshot used by the PPI view.

    Fields
    ------
    icao: ICAO24 hex string
    lat, lon: Latest position
    callsign: Optional callsign
    course_deg: Optional course over ground (degrees true)
    trail_enu: Optional trail as ENU points in meters (east, north)
    """

    icao: str
    lat: float
    lon: float
    callsign: Optional[str] = None
    course_deg: Optional[float] = None
    trail_enu: Optional[Sequence[Tuple[float, float]]] = None
    # Optional kinematics for labels (pass-through to DataBlockFormatter)
    geo_alt_ft: Optional[float] = None
    baro_alt_ft: Optional[float] = None
    ground_speed_kt: Optional[float] = None
    vertical_rate_fpm: Optional[float] = None
    focused: bool = False
    pinned: bool = False
    info_block_visible: bool = True


class PpiView:
    def __init__(
        self,
        *,
        range_nm: float = 10.0,
        rotation_deg: float = 0.0,
        show_data_blocks: bool = False,
        show_simple_labels: bool = True,
        show_text_annotations: bool = True,
        show_airports: bool = True,
        show_sector_labels: bool = True,
        label_font_px: int = 12,
        label_line_gap_px: int = 2,
        label_block_pad_px: int = 2,
        range_rings: Optional[Sequence[float]] = None,
    ) -> None:
        self.range_nm = float(range_nm)
        if rotation_deg == 0.0 and isinstance(PPI_CONFIG, dict):
            pass
        self.rotation_deg = float(rotation_deg) % 360.0
        self.show_data_blocks = bool(show_data_blocks)
        self.show_simple_labels = bool(show_simple_labels)
        self.show_text_annotations = bool(show_text_annotations)
        self.show_airports = bool(show_airports)
        self.show_sector_labels = bool(show_sector_labels)
        # Data-block typography (allow config overrides when not explicitly passed)
        ty_cfg = (
            PPI_CONFIG.get("typography", {}) if isinstance(PPI_CONFIG, dict) else {}
        )
        if label_font_px == 12:
            label_font_px = int(ty_cfg.get("label_font_px", label_font_px))
        if label_line_gap_px == 2:
            label_line_gap_px = int(ty_cfg.get("line_gap_px", label_line_gap_px))
        if label_block_pad_px == 2:
            label_block_pad_px = int(ty_cfg.get("block_pad_px", label_block_pad_px))
        self.label_font_px = int(label_font_px)
        self.label_line_gap_px = int(label_line_gap_px)
        self.label_block_pad_px = int(label_block_pad_px)
        # Optional explicit ring distances (NM). If not provided we auto-compute
        # a concise set of 2–5 "nice" rings terminating at the configured range.
        self._explicit_rings = [float(r) for r in range_rings] if range_rings else None

    # ---------------------------------------------------------------------
    def _auto_range_rings(self) -> List[float]:
        """Compute a small set of range ring distances (NM) for the view.

        Rules / Rationale
        -----------------
        - Use a 1–2–5 decade pattern to pick "nice" distances.
        - Always include the outermost ring at exactly ``self.range_nm``.
        - Limit to at most 4 rings (including outer) to reduce clutter.
        - Preserve legacy default for 10 NM => [2, 5, 10] (golden test stability).
        - Never include rings spaced closer than 10% of outer range.
        - Distances are strictly increasing and > 0.
        """
        rng = max(0.1, float(self.range_nm))
        # Legacy special cases from config (string keys for stable mapping)
        special = AUTO_RING_CONFIG.get("legacy_special_cases", {})
        try:
            if isinstance(special, dict):
                key = f"{rng:.1f}"
                val = special.get(key)
                if isinstance(val, list):
                    rings = [float(x) for x in val]
                    return rings
        except Exception:  # pragma: no cover - defensive
            pass

        ring_list: List[float] = []
        # Generate candidate nice numbers up to range using configured pattern.
        import math

        exp_min = int(AUTO_RING_CONFIG.get("min_exp", -2))
        exp_max = int(math.floor(math.log10(rng))) + 1
        candidates: List[float] = []
        for e in range(exp_min, exp_max + 1):
            scale = 10**e
            pattern = AUTO_RING_CONFIG.get("nice_pattern", [1, 2, 5])
            try:
                bases = [int(b) for b in pattern]
            except Exception:
                bases = [1, 2, 5]
            for base in bases:
                val = base * scale
                if 0 < val < rng * 0.9999:  # below outer ring
                    candidates.append(val)
        # Deduplicate and sort
        candidates = sorted({round(c, 6) for c in candidates})
        # Filter: remove candidates closer than configured fraction of
        # outer range to avoid visual clutter.
        filtered: List[float] = []
        min_gap = rng * float(AUTO_RING_CONFIG.get("min_gap_fraction", 0.10))
        last = 0.0
        for c in candidates:
            if c - last >= min_gap:
                filtered.append(c)
                last = c
        # Ensure we don't exceed configured number of inner rings; keep largest
        max_inner = int(AUTO_RING_CONFIG.get("max_inner_rings", 3))
        ring_list = filtered[-max_inner:]
        # Always append the exact outer range (if not already)
        if not ring_list or abs(ring_list[-1] - rng) > 1e-6:
            ring_list.append(rng)
        # Guarantee strictly increasing
        ring_list = [r for r in ring_list if r > 0]
        ring_list = sorted(ring_list)
        return ring_list

    def draw(
        self,
        canvas: Canvas,
        *,
        size_px: Tuple[int, int],
        center_lat: float,
        center_lon: float,
        tracks: Iterable[TrackSnapshot],
        map_data: Optional[Dict[str, Any]] = None,
        sectors: Optional[Sequence["Sector"]] = None,
        occlusions: Optional[Sequence[Tuple[int, int, int, int]]] = None,
    ) -> None:
        """Draw the PPI view contents.
        - Deterministic: draws tracks sorted by (callsign, icao), optional text
          annotations and simple labels can be disabled for golden tests.
        """

        # Use provided size for deterministic layout
        w, h = int(size_px[0]), int(size_px[1])

        # Layout adaptation: when occlusion rectangles include a top status
        # bar and/or a bottom vertical profile panel we treat the vertical
        # space between them as the *usable* PPI area. We then (a) center the
        # ownship within that free band and (b) choose the outer range ring
        # radius so that the full circle fits inside the band. This avoids
        # the visual impression (on the TFT) that ownship is “low” because a
        # bottom panel pushes it upward, and ensures the largest ring no
        # longer disappears under the vertical profile.
        top_clear_y = 0
        bottom_clear_y = h
        if occlusions:
            try:
                for (
                    ox,
                    oy,
                    ow_,
                    oh_,
                ) in occlusions:  # rectangles may include overlay + panels
                    # Heuristics: full‑width (>=90% display width) rectangle anchored
                    # at y==0 is the status overlay; full‑width rectangle whose bottom
                    # touches or is near the display bottom is a bottom panel (vertical
                    # profile or softkeys). We always keep the *highest* bottom for top
                    # bars and the *lowest* top for bottom bars.
                    if ow_ >= 0.9 * w:
                        if oy == 0:  # top band
                            if oy + oh_ > top_clear_y:
                                top_clear_y = oy + oh_
                        # bottom band: treat any band within last 60px OR whose
                        # lower edge is exactly h; vertical profile will be taller
                        # than the softkey bar but both are handled the same.
                        if (oy + oh_) >= h - 1:
                            if oy < bottom_clear_y:
                                bottom_clear_y = oy
            except Exception:
                pass
        usable_h = max(20, bottom_clear_y - top_clear_y)
        cx = int(w // 2)
        cy = int(round(top_clear_y + usable_h / 2.0))
        # Base radius limited by horizontal half‑width and half of usable vertical span
        radius_px = int(min(w // 2, usable_h / 2.0) - 6)
        if radius_px < 10:  # fallback safety for extremely small views
            radius_px = 10
        # Compute meters per pixel from range_nm
        meters_per_nm = 1852.0
        range_m = self.range_nm * meters_per_nm
        m_per_px = range_m / float(radius_px)

        # Active theme palette
        ThemeManager.theme()  # keep reference (may inspect name)
        # Clear background
        canvas.clear(ThemeManager.color("bg"))

        # Precompute range ring geometry and label bounding boxes so airports
        # (z-index 1) can avoid them even though rings (2) and their labels (3)
        # are drawn later.
        range_ring_exclusions: list[tuple[int, int, int, int]] = []
        ring_ticks = (
            list(self._explicit_rings)
            if self._explicit_rings is not None
            else self._auto_range_rings()
        )
        ring_radii: list[int] = []  # circles to draw at z-index 2
        ring_label_specs: list[tuple[int, int, str]] = []  # (x,y,text) for z-index 3
        if ring_ticks:
            for nm in ring_ticks:
                if nm > self.range_nm:
                    continue
                r_px = int((nm * meters_per_nm) / m_per_px)
                ring_radii.append(r_px)
                if self.show_text_annotations:
                    label_text = f"{int(nm)}nm"
                    rr_cfg = (
                        PPI_CONFIG.get("range_ring_label", {})
                        if isinstance(PPI_CONFIG, dict)
                        else {}
                    )
                    label_x = cx + r_px + int(rr_cfg.get("offset_x_px", 4))
                    label_y = cy + int(rr_cfg.get("offset_y_px", -8))
                    ring_label_specs.append((label_x, label_y, label_text))
                    # Add exclusion for airport labels
                    char_w = max(6, int(round(self.label_font_px * 0.6)))
                    label_w = len(label_text) * char_w
                    label_h = self.label_font_px
                    padding = int(rr_cfg.get("padding_px", 4))
                    range_ring_exclusions.append(
                        (
                            label_x - padding,
                            label_y - padding,
                            label_w + 2 * padding,
                            label_h + 2 * padding,
                        )
                    )

        map_airports = map_data.get("airports") if map_data else []
        runways_source = map_data.get("runways") if map_data else []
        runways_by_ident = (
            _prepare_runway_icons(runways_source) if runways_source else {}
        )

        # z-index 0: Sectors (drawn first so state borders can be drawn above).
        if sectors:
            try:
                from pocketscope.data.sectors import Sector as _Sector
                from pocketscope.render.sectors_layer import SectorsLayer

                _secs: list[_Sector] = list(sectors)
                SectorsLayer(show_labels=self.show_sector_labels).draw(
                    canvas,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    range_nm=self.range_nm,
                    sectors=_secs,
                    screen_size=(w, h),
                    rotation_deg=self.rotation_deg,
                    ppi_center_px=(cx, cy),
                    ppi_radius_px=radius_px,
                    ppi_m_per_px=m_per_px,
                )
            except Exception:
                pass

        # z-index 1: US state / regional boundaries (now drawn above sectors so
        # sector lines no longer completely mask borders when they coincide).
        # Only the exterior rings of Polygon / MultiPolygon geometries are
        # rendered (holes ignored). We keep the logic local but refactored
        # into a small helper for clarity.
        map_states = map_data.get("states") if map_data else []
        if map_states:
            try:
                try:
                    _border_color = ThemeManager.color("map.border")
                except Exception:  # pragma: no cover - defensive fallback
                    _border_color = (64, 96, 64, 255)

                from math import cos as _cos
                from math import radians as _radians
                from math import sin as _sin

                _phi = -_radians(self.rotation_deg % 360.0)
                _ce, _se = _cos(_phi), _sin(_phi)

                def _to_screen_state(lat: float, lon: float) -> tuple[int, int]:
                    tx, ty, tz = geodetic_to_ecef(lat, lon, 0.0)
                    e, n, _ = ecef_to_enu(tx, ty, tz, center_lat, center_lon, 0.0)
                    if (self.rotation_deg % 360.0) != 0.0:
                        er = e * _ce - n * _se
                        nr = e * _se + n * _ce
                        e2, n2 = er, nr
                    else:
                        e2, n2 = e, n
                    sx, sy = enu_to_screen(e2, n2, m_per_px)
                    return (int(round(cx + sx)), int(round(cy + sy)))

                def _exterior_rings(geom: Any) -> list[list[tuple[float, float]]]:
                    rings_out: list[list[tuple[float, float]]] = []
                    if not isinstance(geom, dict):
                        return rings_out
                    gtype = geom.get("type")
                    coords = geom.get("coordinates")
                    if gtype == "Polygon" and isinstance(coords, list):
                        if coords:
                            ring = coords[0]
                            if isinstance(ring, list):
                                pts: list[tuple[float, float]] = []
                                for pt in ring:
                                    if (
                                        isinstance(pt, (list, tuple))
                                        and len(pt) >= 2
                                        and isinstance(pt[0], (int, float))
                                        and isinstance(pt[1], (int, float))
                                    ):
                                        lon_f = float(pt[0])
                                        lat_f = float(pt[1])
                                        pts.append((lat_f, lon_f))
                                if len(pts) >= 3:
                                    rings_out.append(pts)
                    elif gtype == "MultiPolygon" and isinstance(coords, list):
                        for poly in coords:
                            if (
                                isinstance(poly, list)
                                and poly
                                and isinstance(poly[0], list)
                            ):
                                ring = poly[0]
                                if isinstance(ring, list):
                                    pts2: list[tuple[float, float]] = []
                                    for pt in ring:
                                        if (
                                            isinstance(pt, (list, tuple))
                                            and len(pt) >= 2
                                            and isinstance(pt[0], (int, float))
                                            and isinstance(pt[1], (int, float))
                                        ):
                                            lon_f = float(pt[0])
                                            lat_f = float(pt[1])
                                            pts2.append((lat_f, lon_f))
                                    if len(pts2) >= 3:
                                        rings_out.append(pts2)
                    return rings_out

                def _state_sort_key(s: Any) -> tuple[str, float, float]:
                    nm = str(s.get("name", "")).upper() if isinstance(s, dict) else ""
                    geom = s.get("geometry") if isinstance(s, dict) else None
                    lon_c = 0.0
                    lat_c = 0.0
                    try:
                        if isinstance(geom, dict):
                            rings = _exterior_rings(geom)
                            if rings:
                                rs = rings[0]
                                lon_c = sum(p[1] for p in rs) / len(rs)
                                lat_c = sum(p[0] for p in rs) / len(rs)
                    except Exception:  # pragma: no cover
                        lon_c = 0.0
                        lat_c = 0.0
                    return (nm, float(lon_c), float(lat_c))

                for st in sorted(map_states, key=_state_sort_key):
                    try:
                        geom = st.get("geometry") if isinstance(st, dict) else None
                        if not geom:
                            continue
                        rings = _exterior_rings(geom)
                        for ring in rings:
                            keep = False
                            for lat_pt, lon_pt in ring:
                                d_nm = haversine_nm(
                                    center_lat, center_lon, lat_pt, lon_pt
                                )
                                if d_nm <= (2.0 * self.range_nm):
                                    keep = True
                                    break
                            if not keep:
                                continue
                            pts_screen: list[tuple[int, int]] = []
                            for lat_pt, lon_pt in ring:
                                pts_screen.append(_to_screen_state(lat_pt, lon_pt))
                            if pts_screen and pts_screen[0] != pts_screen[-1]:
                                pts_screen.append(pts_screen[0])
                            canvas.polyline(pts_screen, width=1, color=_border_color)
                    except Exception:
                        continue
            except Exception:
                pass

        # z-index 2: Airports
        if self.show_airports and map_airports:
            try:
                AirportsLayer(font_px=self.label_font_px).draw(
                    canvas,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    range_nm=self.range_nm,
                    airports=map_airports,
                    screen_size=(w, h),
                    rotation_deg=self.rotation_deg,
                    range_ring_exclusions=range_ring_exclusions,
                    runways_by_ident=runways_by_ident if runways_by_ident else None,
                    ppi_center_px=(cx, cy),
                    ppi_radius_px=radius_px,
                    ppi_m_per_px=m_per_px,
                )
            except Exception:
                pass

        # z-index 3: Range rings (circles only)
        for r_px in ring_radii:
            canvas.circle(
                (cx, cy), r_px, width=1, color=ThemeManager.color("range.ring")
            )

        # z-index 4: Range ring labels
        if ring_label_specs:
            for label_x, label_y, label_text in ring_label_specs:
                canvas.text(
                    (label_x, label_y),
                    label_text,
                    size_px=self.label_font_px,
                    color=ThemeManager.color("range.text"),
                )

        # Cardinal ticks and labels: draw short pips at N/E/S/W bearings
        # and place labels adjacent along the tangential direction.
        # Text remains upright.
        from math import cos as _cos
        from math import radians as _radians
        from math import sin as _sin

        def _draw_cardinal(delta_deg: float, label: str) -> None:
            theta = _radians((self.rotation_deg + delta_deg) % 360.0)
            dir_x = _sin(theta)
            dir_y = -_cos(theta)
            outer = (
                cx + int(round(dir_x * radius_px)),
                cy + int(round(dir_y * radius_px)),
            )
            inner = (
                outer[0] - int(round(dir_x * 12)),
                outer[1] - int(round(dir_y * 12)),
            )
            canvas.line(inner, outer, width=2, color=ThemeManager.color("range.tick"))
            if self.show_text_annotations:
                tan_x = -dir_y
                tan_y = dir_x
                lx = outer[0] - int(round(dir_x * 8)) + int(round(tan_x * 6))
                ly = outer[1] - int(round(dir_y * 8)) + int(round(tan_y * 6))
                canvas.text(
                    (lx, ly),
                    label,
                    size_px=self.label_font_px,
                    color=ThemeManager.color("range.text"),
                )
                # Add exclusion zone around cardinal label
                char_w = max(6, int(round(self.label_font_px * 0.6)))
                label_w = len(label) * char_w
                label_h = self.label_font_px
                padding = 4
                range_ring_exclusions.append(
                    (
                        lx - padding,
                        ly - padding,
                        label_w + 2 * padding,
                        label_h + 2 * padding,
                    )
                )

        # z-index 4: Cardinal direction labels & ticks
        _draw_cardinal(0.0, "N")
        _draw_cardinal(90.0, "E")
        _draw_cardinal(180.0, "S")
        _draw_cardinal(270.0, "W")

        # z-index 5/6: ownship base marker (below trails/markers for consistency)
        try:
            canvas.filled_circle(
                (cx, cy), 2, color=ThemeManager.color("ac.level.stroke")
            )  # ownship center
        except Exception:
            # Fallback: smallest circle
            try:
                canvas.filled_circle(
                    (cx, cy), 1, color=ThemeManager.color("ac.level.stroke")
                )
            except Exception:
                pass

        # Origin for ENU conversion
        _ox, _oy, _oz = geodetic_to_ecef(center_lat, center_lon, 0.0)

        def _enu_to_screen_rot(e: float, n: float) -> Tuple[float, float]:
            """Rotate ENU by rotation_deg (clockwise positive) and map to screen."""
            if (self.rotation_deg % 360.0) == 0.0:
                return enu_to_screen(e, n, m_per_px)
            phi = -radians(self.rotation_deg)
            ce, se = cos(phi), sin(phi)
            xr = e * ce - n * se
            yr = e * se + n * ce
            return enu_to_screen(xr, yr, m_per_px)

        def to_screen(lat: float, lon: float) -> Tuple[int, int]:
            tx, ty, tz = geodetic_to_ecef(lat, lon, 0.0)
            e, n, _ = ecef_to_enu(tx, ty, tz, center_lat, center_lon, 0.0)
            x, y = _enu_to_screen_rot(e, n)
            return int(round(cx + x)), int(round(cy + y))

        # Optional data-block label machinery
        label_candidates: list[
            tuple[int, tuple[int, int], tuple[str, str, str], bool]
        ] = []
        label_formatter: LabelFormatter | None = None
        label_layout: LabelLayout | None = None
        if self.show_data_blocks:
            label_formatter = LabelFormatter(OwnshipRef(center_lat, center_lon))
            label_layout = LabelLayout(
                (w, h),
                font_px=self.label_font_px,
                line_gap_px=self.label_line_gap_px,
                block_pad_px=self.label_block_pad_px,
            )

        # Draw tracks deterministically. We now split trails (z=5) and aircraft
        # glyph markers (z=6) into separate passes so ALL markers render above
        # ALL trails regardless of per-track ordering. This satisfies the
        # requirement that aircraft markers are always on top of any tracks.
        _tracks = list(tracks)
        _tracks.sort(key=lambda t: ((t.callsign or ""), t.icao))

        # Precompute screen position and visibility for labels; store geometry
        from typing import List as _List
        from typing import TypedDict

        class _Precomp(TypedDict):
            track: TrackSnapshot
            gx: int
            gy: int
            visible_for_label: bool

        precomp: _List[_Precomp] = []
        for t in _tracks:
            gx, gy = to_screen(t.lat, t.lon)
            visible_for_label = True
            if self.show_data_blocks:
                if not (0 <= gx < w and 0 <= gy < h):
                    visible_for_label = False
                else:
                    dx_ = gx - cx
                    dy_ = gy - cy
                    if (dx_ * dx_ + dy_ * dy_) > (radius_px * radius_px):
                        visible_for_label = False
                    elif occlusions:
                        for ox, oy, ow, oh in occlusions:
                            if ox <= gx < ox + ow and oy <= gy < oy + oh:
                                visible_for_label = False
                                break
            precomp.append(
                {
                    "track": t,
                    "gx": gx,
                    "gy": gy,
                    "visible_for_label": visible_for_label,
                }
            )

        # Pass 1: trails (z=5)
        for pc in precomp:
            t = pc["track"]
            if not t.trail_enu:
                continue
            pts: List[Tuple[int, int]] = []
            for e, n in t.trail_enu:
                x, y = _enu_to_screen_rot(e, n)
                pts.append((int(round(cx + x)), int(round(cy + y))))
            # Fade trail oldest -> background, keeping the most recent (head-adjacent)
            # segment vivid. Previous implementation inverted this (making the segment
            # immediately behind the aircraft already blended to bg). We correct that
            # by reversing the interpolation factor.
            if len(pts) < 2:
                continue
            head_rgba = ThemeManager.track_speed_color(
                getattr(t, "ground_speed_kt", None)
            )
            bg_rgba = ThemeManager.color("bg")
            hr, hg, hb, ha = head_rgba
            br, bg_, bb, ba = bg_rgba
            segs = len(pts) - 1
            # To achieve a soft fade we also bias alpha toward the background's
            # alpha (usually opaque) while simultaneously reducing contrast by
            # blending RGB. If background alpha is 255 this essentially lerps
            # colors; if background alpha were translucent we respect that.
            trail_width = 1  # narrower trail for visual subtlety
            for i in range(segs):
                # i=0 oldest segment, i=segs-1 newest (adjacent to aircraft).
                # progress: 0 => oldest, 1 => newest. We lerp bg->head by progress
                denom = max(1, segs - 1)
                progress = i / denom
                r = int(br + (hr - br) * progress)
                g = int(bg_ + (hg - bg_) * progress)
                b = int(bb + (hb - bb) * progress)
                a = int(ba + (ha - ba) * progress)
                canvas.line(pts[i], pts[i + 1], width=trail_width, color=(r, g, b, a))

        # Pass 2: glyphs (z=6) & labels
        # Track vertical rate by glyph anchor so we can colorize info blocks
        # after layout without changing layout APIs.
        anchor_vs: Dict[Tuple[int, int], float | None] = {}
        # Collect simple labels for collision-aware post-pass rendering.
        # Each entry: (gx, gy, text, focused, arrow_symbol, arrow_color_key)
        simple_label_candidates: list[tuple[int, int, str, bool, str, str | None]] = []
        for pc in precomp:
            t = pc["track"]
            gx = pc["gx"]
            gy = pc["gy"]
            visible_for_label = pc["visible_for_label"]

            # Aircraft glyph fill: always use a single neutral level color.
            # (Previously varied by climb/desc status; simplified per request.)
            fill_color = ThemeManager.color("ac.level.fill")
            stroke_color = ThemeManager.color("ac.level.stroke")
            if t.pinned:
                stroke_color = ThemeManager.color("ac.pinned.stroke")
            if t.focused:
                stroke_color = ThemeManager.color("ac.focus.stroke")

            if t.course_deg is not None:
                if (self.rotation_deg % 360.0) == 0.0:
                    rad = radians(t.course_deg)
                else:
                    rad = radians((t.course_deg + self.rotation_deg) % 360.0)
                dx_f: float = sin(rad)
                dy_f: float = -cos(rad)
                size = 5
                tip = (gx + int(round(dx_f * size)), gy + int(round(dy_f * size)))
                base_len = 4
                base_w = 3
                bx, by = -dx_f * base_len, -dy_f * base_len
                px, py = -dy_f, dx_f
                p1 = (
                    gx + int(round(bx + px * base_w)),
                    gy + int(round(by + py * base_w)),
                )
                p2 = (
                    gx + int(round(bx - px * base_w)),
                    gy + int(round(by - py * base_w)),
                )
                # Outline
                canvas.polyline([p1, tip, p2], width=1, color=stroke_color)
                # Fill (simple vertical fill by drawing inner polyline with fill color)
                canvas.polyline([p1, tip, p2], width=0, color=fill_color)
            else:
                canvas.filled_circle((gx, gy), 3, color=fill_color)
                if stroke_color != fill_color:
                    canvas.circle((gx, gy), 3, width=1, color=stroke_color)

            if (
                self.show_data_blocks
                and label_formatter is not None
                and visible_for_label
                and t.info_block_visible
            ):
                ls = LabelTrack(
                    icao24=t.icao,
                    callsign=t.callsign,
                    lat=t.lat,
                    lon=t.lon,
                    geo_alt_ft=t.geo_alt_ft,
                    baro_alt_ft=t.baro_alt_ft,
                    ground_speed_kt=t.ground_speed_kt,
                    vertical_rate_fpm=t.vertical_rate_fpm,
                    emitter_type=None,
                    pinned=bool(t.pinned),
                    focused=bool(t.focused),
                )
                lines = label_formatter.format_standard(ls)
                dist2 = (gx - cx) * (gx - cx) + (gy - cy) * (gy - cy)
                label_candidates.append((dist2, (gx, gy), lines, False))
                anchor_vs[(gx, gy)] = getattr(t, "vertical_rate_fpm", None)
            else:
                if self.show_simple_labels:
                    # Collect simple one-line label candidates. Augment with
                    # a vertical speed arrow (colored) for non-focused aircraft.
                    label_text = t.callsign or t.icao
                    vs = getattr(t, "vertical_rate_fpm", None)
                    arrow_symbol = ""
                    arrow_color_key: str | None = None
                    # New behavior: suppress simple labels for non-focused
                    # aircraft that are effectively level (no significant
                    # climb/descent). We treat +/-200 fpm as the deadband
                    # consistent with arrow logic.
                    if not t.focused:
                        if not isinstance(vs, (int, float)) or -200 < float(vs) < 200:
                            # Skip adding a label for level, non-focused acft.
                            continue
                    if not t.focused and isinstance(vs, (int, float)):
                        try:
                            if vs > 200:
                                arrow_symbol = "▲"  # climb
                                arrow_color_key = "ac.climb.fill"
                            elif vs < -200:
                                arrow_symbol = "▼"  # descent
                                arrow_color_key = "ac.desc.fill"
                        except Exception:
                            pass
                    # Store extended tuple components:
                    # (gx, gy, text, focused, arrow_symbol, arrow_color_key)
                    simple_label_candidates.append(
                        (
                            gx,
                            gy,
                            label_text,
                            bool(t.focused),
                            arrow_symbol,
                            arrow_color_key,
                        )
                    )

        # ------------------------------------------------------------------
        # Simple label collision + halo rendering (z=7 just below data blocks)
        # ------------------------------------------------------------------
        if self.show_simple_labels and simple_label_candidates:
            # Deterministic ordering: sort by (focused desc so focused placed first
            # for minimal movement), then text, then anchor.
            # simple_label_candidates entries:
            # (ax, ay, text, focused, arrow_symbol, arrow_color_key)
            simple_label_candidates.sort(
                key=lambda it: (not it[3], it[2], it[0], it[1])
            )
            char_w = max(6, int(round(self.label_font_px * 0.6)))
            label_h = self.label_font_px
            occupied: list[tuple[int, int, int, int]] = []  # placed label rects
            # Seed occupied with small squares for aircraft glyphs so labels
            # avoid covering symbols (consistent with data block layout logic).
            glyph_half = 5
            glyph_size = glyph_half * 2
            seen_glyphs: set[tuple[int, int]] = set()
            for ax, ay, _text, _f, _as, _ac in simple_label_candidates:
                if (ax, ay) in seen_glyphs:
                    continue
                seen_glyphs.add((ax, ay))
                occupied.append(
                    (ax - glyph_half, ay - glyph_half, glyph_size, glyph_size)
                )

            def _intersects(
                a: tuple[int, int, int, int], b: tuple[int, int, int, int]
            ) -> bool:
                ax, ay, aw, ah = a
                bx, by, bw, bh = b
                return not (
                    ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay
                )

            placements_simple: list[
                tuple[int, int, int, int, str, bool, str, str | None]
            ] = []
            for (
                ax,
                ay,
                text,
                focused,
                arrow_symbol,
                arrow_color_key,
            ) in simple_label_candidates:
                # Initial candidate relative to glyph (matches legacy offset)
                init_x = ax + 6
                init_y = ay - label_h  # previously hard-coded -12 for 12px font
                extra_chars = 0
                if arrow_symbol:
                    # Space + arrow
                    extra_chars = 2
                w_txt = (len(text) + extra_chars) * char_w
                h_txt = label_h
                # Clamp initial
                if w_txt > 0 and h_txt > 0:
                    if init_x + w_txt > w:
                        init_x = w - w_txt
                    if init_y + h_txt > h:
                        init_y = h - h_txt
                    if init_x < 0:
                        init_x = 0
                    if init_y < 0:
                        init_y = 0
                x, y = init_x, init_y
                box = (x, y, w_txt, h_txt)
                collides = any(_intersects(box, ob) for ob in occupied)
                # Simple square spiral search (reuse concept from data blocks)
                step = 6
                max_attempts = 40
                attempts = 0
                while collides and attempts < max_attempts:
                    r = 1 + attempts // 4
                    dir_idx = attempts % 4  # R, D, L, U
                    dx = [step * r, 0, -step * r, 0][dir_idx]
                    dy = [0, step * r, 0, -step * r][dir_idx]
                    cx_try = init_x + dx
                    cy_try = init_y + dy
                    # Clamp
                    if cx_try + w_txt > w:
                        cx_try = w - w_txt
                    if cy_try + h_txt > h:
                        cy_try = h - h_txt
                    if cx_try < 0:
                        cx_try = 0
                    if cy_try < 0:
                        cy_try = 0
                    box_try = (cx_try, cy_try, w_txt, h_txt)
                    collides = any(_intersects(box_try, ob) for ob in occupied)
                    if not collides:
                        x, y = cx_try, cy_try
                        box = box_try
                        break
                    attempts += 1
                # Record and mark occupied
                placements_simple.append(
                    (x, y, w_txt, h_txt, text, focused, arrow_symbol, arrow_color_key)
                )
                occupied.append(box)

            # Draw halos and text
            halo_color = ThemeManager.color("label.halo")
            for (
                x,
                y,
                w_txt,
                h_txt,
                text,
                focused,
                arrow_symbol,
                arrow_color_key,
            ) in placements_simple:
                try:
                    for row in range(h_txt):
                        canvas.line(
                            (x, y + row),
                            (x + max(0, w_txt - 1), y + row),
                            width=1,
                            color=halo_color,
                        )
                except Exception:
                    pass
                color_key = "label.text.primary" if focused else "label.text.other"
                canvas.text(
                    (x + 1, y),
                    text,
                    size_px=self.label_font_px,
                    color=ThemeManager.color(color_key),
                )
                if arrow_symbol:
                    # Draw arrow after a space following the text.
                    arrow_x = x + 1 + len(text) * char_w + char_w  # space width
                    arrow_color = (
                        ThemeManager.color(arrow_color_key)
                        if arrow_color_key
                        else ThemeManager.color(color_key)
                    )
                    canvas.text(
                        (arrow_x, y),
                        arrow_symbol,
                        size_px=self.label_font_px,
                        color=arrow_color,
                    )

        # Draw data blocks last (leader lines z=7, blocks z=8)
        if self.show_data_blocks and label_layout is not None:
            # Ensure labels avoid overlapping a small radius around ownship.
            # Represent the circular exclusion as a conservative bounding box
            # so the existing rectangular occlusion API can be reused.
            ownship_excl_px = 20  # radius in pixels to avoid around ownship
            ow = ownship_excl_px * 2
            oh = ownship_excl_px * 2
            ox = int(cx - ownship_excl_px)
            oy = int(cy - ownship_excl_px)
            occl: list[tuple[int, int, int, int]] = []
            if occlusions:
                try:
                    occl.extend(
                        [
                            (int(x), int(y), int(w_), int(h_))
                            for (x, y, w_, h_) in occlusions
                        ]
                    )
                except Exception:
                    occl.extend(list(occlusions))
            occl.append((ox, oy, ow, oh))
            ordered_label_items = [
                (anchor, lines, expanded)
                for _range2, anchor, lines, expanded in sorted(
                    label_candidates, key=lambda entry: entry[0]
                )
            ]
            placements = label_layout.place_blocks(ordered_label_items, occlusions=occl)
            for p in placements:
                ax, ay = p.anchor_px
                w_b, h_b = label_layout.measure(p.lines)
                # Leader line (z=7)
                candidates = [
                    (p.x, p.y + h_b // 2),
                    (p.x + w_b, p.y + h_b // 2),
                    (p.x + w_b // 2, p.y),
                    (p.x + w_b // 2, p.y + h_b),
                ]
                cx2, cy2 = min(
                    candidates,
                    key=lambda q: (q[0] - ax) * (q[0] - ax) + (q[1] - ay) * (q[1] - ay),
                )
                canvas.line(
                    (ax, ay),
                    (int(cx2), int(cy2)),
                    width=1,
                    color=ThemeManager.color("label.text.primary"),
                )
                # Background (z=8) drawn after leader line (z=7) and before text (z=8+)
                try:
                    for dy in range(h_b):
                        y_row = p.y + dy
                        canvas.line(
                            (p.x, y_row),
                            (p.x + w_b - 1, y_row),
                            width=1,
                            color=ThemeManager.color("label.halo"),
                        )
                except Exception:
                    pass
                # Derive per-block text color from vertical rate (gradient).
                for i, s in enumerate(p.lines):
                    y = p.y + i * (self.label_font_px + self.label_line_gap_px)
                    color = (
                        ThemeManager.color("label.text.primary")
                        if i == 0
                        else ThemeManager.color("label.text.dim")
                    )
                    canvas.text(
                        (p.x + 2, y),
                        s,
                        size_px=self.label_font_px,
                        color=color,
                    )
