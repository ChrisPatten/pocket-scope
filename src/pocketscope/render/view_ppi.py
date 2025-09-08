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
from math import cos, radians, sin
from typing import TYPE_CHECKING, Any, Iterable, List, Optional, Sequence, Tuple

from pocketscope import config as _config
from pocketscope.core.geo import ecef_to_enu, enu_to_screen, geodetic_to_ecef
from pocketscope.render.airports_layer import AirportsLayer
from pocketscope.render.canvas import Canvas, Color
from pocketscope.render.labels import DataBlockFormatter as LabelFormatter
from pocketscope.render.labels import DataBlockLayout as LabelLayout
from pocketscope.render.labels import OwnshipRef
from pocketscope.render.labels import TrackSnapshot as LabelTrack
from pocketscope.settings.values import AUTO_RING_CONFIG, PPI_CONFIG, THEME

if TYPE_CHECKING:  # for type hints only
    from pocketscope.data.sectors import Sector

_PPI_THEME = THEME.get("colors", {}).get("ppi", {}) if isinstance(THEME, dict) else {}


def _col(v: object, fb: tuple[int, int, int, int]) -> Color:
    if (
        isinstance(v, (list, tuple))
        and len(v) == 4
        and all(isinstance(c, (int, float)) for c in v)
    ):
        return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))
    return fb


ColorBG: Color = _col(_PPI_THEME.get("background"), (0, 0, 0, 255))
ColorRings: Color = _col(_PPI_THEME.get("rings"), (80, 80, 80, 255))
ColorOwnship: Color = _col(_PPI_THEME.get("ownship"), (255, 255, 255, 255))
ColorTrails: Color = _col(_PPI_THEME.get("trails"), (0, 180, 255, 180))
ColorAircraft: Color = _col(_PPI_THEME.get("aircraft"), (255, 255, 0, 255))
ColorLabels: Color = _col(_PPI_THEME.get("labels"), (255, 255, 255, 255))
ColorDataBlock: Color = _col(_PPI_THEME.get("datablock"), (0, 255, 0, 255))
ColorDataBlockBG: Color = _col(_PPI_THEME.get("datablock_bg"), (0, 0, 0, 140))


def _on_runtime_update(rc: Any) -> None:
    """Update module-level color constants when runtime theme changes.

    This callback is scheduled via the event loop by config.update_from_settings
    so it must be quick and idempotent.
    """
    try:
        if isinstance(rc, dict):
            theme = rc
        else:
            theme = getattr(rc, "theme", {}) or {}
        ppi_theme = (
            theme.get("colors", {}).get("ppi", {}) if isinstance(theme, dict) else {}
        )
        # Update colors in-place
        global ColorBG, ColorRings, ColorOwnship, ColorTrails
        global ColorAircraft, ColorLabels, ColorDataBlock, ColorDataBlockBG
        ColorBG = _col(ppi_theme.get("background"), ColorBG)
        ColorRings = _col(ppi_theme.get("rings"), ColorRings)
        ColorOwnship = _col(ppi_theme.get("ownship"), ColorOwnship)
        ColorTrails = _col(ppi_theme.get("trails"), ColorTrails)
        ColorAircraft = _col(ppi_theme.get("aircraft"), ColorAircraft)
        ColorLabels = _col(ppi_theme.get("labels"), ColorLabels)
        ColorDataBlock = _col(ppi_theme.get("datablock"), ColorDataBlock)
        ColorDataBlockBG = _col(ppi_theme.get("datablock_bg"), ColorDataBlockBG)
    except Exception:
        pass


# Register listener (no-op if already registered elsewhere)
try:
    _config.register_listener(_on_runtime_update)
except Exception:
    pass


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
        airports: Optional[Sequence[Tuple[float, float, str]]] = None,
        sectors: Optional[Sequence["Sector"]] = None,
        occlusions: Optional[Sequence[Tuple[int, int, int, int]]] = None,
    ) -> None:
        """Draw the PPI view contents.
        - Deterministic: draws tracks sorted by (callsign, icao), optional text
          annotations and simple labels can be disabled for golden tests.
        """

        # Use provided size for deterministic layout
        w, h = int(size_px[0]), int(size_px[1])
        cx, cy = int(w // 2), int(h // 2)
        radius_px = int(min(w, h) // 2) - 6
        if radius_px < 10:
            radius_px = 10
        # Compute meters per pixel from range_nm
        meters_per_nm = 1852.0
        range_m = self.range_nm * meters_per_nm
        m_per_px = range_m / float(radius_px)

        # Clear background
        canvas.clear(ColorBG)

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

        # z-index 0: Sectors
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
                )
            except Exception:
                pass

        # z-index 1: Airports
        if self.show_airports and airports:
            try:
                from pocketscope.data.airports import Airport

                aps: list[Airport] = []
                for lat, lon, ident in airports:
                    aps.append(
                        Airport(
                            ident=str(ident).upper(), lat=float(lat), lon=float(lon)
                        )
                    )
                AirportsLayer(font_px=self.label_font_px).draw(
                    canvas,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    range_nm=self.range_nm,
                    airports=aps,
                    screen_size=(w, h),
                    rotation_deg=self.rotation_deg,
                    range_ring_exclusions=range_ring_exclusions,
                )
            except Exception:
                pass

        # z-index 2: Range rings (circles only)
        for r_px in ring_radii:
            canvas.circle((cx, cy), r_px, width=1, color=ColorRings)

        # z-index 3: Range ring labels
        if ring_label_specs:
            for label_x, label_y, label_text in ring_label_specs:
                canvas.text(
                    (label_x, label_y),
                    label_text,
                    size_px=self.label_font_px,
                    color=ColorLabels,
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
            canvas.line(inner, outer, width=2, color=ColorRings)
            if self.show_text_annotations:
                tan_x = -dir_y
                tan_y = dir_x
                lx = outer[0] - int(round(dir_x * 8)) + int(round(tan_x * 6))
                ly = outer[1] - int(round(dir_y * 8)) + int(round(tan_y * 6))
                canvas.text(
                    (lx, ly), label, size_px=self.label_font_px, color=ColorLabels
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
        canvas.filled_circle((cx, cy), 4, color=ColorOwnship)

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
        label_items: list[tuple[Tuple[int, int], Tuple[str, str, str], bool]] = []
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

        # Draw tracks deterministically (trails z=5, markers z=6)
        _tracks = list(tracks)
        _tracks.sort(key=lambda t: ((t.callsign or ""), t.icao))
        for t in _tracks:
            gx, gy = to_screen(t.lat, t.lon)

            # When rendering data blocks we only produce label anchor items
            # for aircraft whose glyph location is actually visible to the
            # user. "Visible" means:
            #   1. Within the circular PPI range (inside radius_px)
            #   2. Within the framebuffer bounds
            #   3. Not inside any occlusion rectangle (status overlay, softkeys)
            # We still draw the glyph/trail regardless (legacy behavior) so
            # range ring proximity and off-screen clipping remain unchanged.
            visible_for_label = True
            if self.show_data_blocks:
                # Bounds check
                if not (0 <= gx < w and 0 <= gy < h):
                    visible_for_label = False
                else:
                    # Circular range check using squared distance to avoid sqrt
                    dx = gx - cx
                    dy = gy - cy
                    if (dx * dx + dy * dy) > (radius_px * radius_px):
                        visible_for_label = False
                    else:
                        # Occlusion rectangles (x,y,w,h) measured from top-left
                        if occlusions:
                            for ox, oy, ow, oh in occlusions:
                                if ox <= gx < ox + ow and oy <= gy < oy + oh:
                                    visible_for_label = False
                                    break

            # Trail under glyph
            if t.trail_enu:
                pts: List[Tuple[int, int]] = []
                for e, n in t.trail_enu:
                    x, y = _enu_to_screen_rot(e, n)
                    pts.append((int(round(cx + x)), int(round(cy + y))))
                if len(pts) >= 2:
                    canvas.polyline(pts, width=2, color=ColorTrails)

            # Glyph
            if t.course_deg is not None:
                if (self.rotation_deg % 360.0) == 0.0:
                    rad = radians(t.course_deg)
                else:
                    rad = radians((t.course_deg + self.rotation_deg) % 360.0)
                # Trig results are float; ensure downstream math uses float
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
                canvas.polyline([p1, tip, p2], width=1, color=ColorAircraft)
            else:
                canvas.filled_circle((gx, gy), 3, color=ColorAircraft)

            # Labels
            if (
                self.show_data_blocks
                and label_formatter is not None
                and visible_for_label
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
                    pinned=False,
                    focused=False,
                )
                lines = label_formatter.format_standard(ls)
                label_items.append(((gx, gy), lines, False))
            else:
                if self.show_simple_labels:
                    label_text = t.callsign or t.icao
                    canvas.text(
                        (gx + 6, gy - 12),
                        label_text,
                        size_px=self.label_font_px,
                        color=ColorLabels,
                    )

        # Draw data blocks last (leader lines z=7, blocks z=8)
        if self.show_data_blocks and label_layout is not None:
            placements = label_layout.place_blocks(label_items, occlusions=occlusions)
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
                    (ax, ay), (int(cx2), int(cy2)), width=1, color=ColorDataBlock
                )
                # Background (z=8) drawn after leader line (z=7) and before text (z=8+)
                try:
                    for dy in range(h_b):
                        y_row = p.y + dy
                        canvas.line(
                            (p.x, y_row),
                            (p.x + w_b - 1, y_row),
                            width=1,
                            color=ColorDataBlockBG,
                        )
                except Exception:
                    pass
                for i, s in enumerate(p.lines):
                    y = p.y + i * (self.label_font_px + self.label_line_gap_px)
                    canvas.text(
                        (p.x + 2, y),
                        s,
                        size_px=self.label_font_px,
                        color=ColorDataBlock,
                    )
