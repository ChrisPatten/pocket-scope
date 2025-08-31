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
from typing import TYPE_CHECKING, Iterable, List, Optional, Sequence, Tuple

from pocketscope.core.geo import ecef_to_enu, enu_to_screen, geodetic_to_ecef
from pocketscope.render.airports_layer import AirportsLayer
from pocketscope.render.canvas import Canvas, Color
from pocketscope.render.labels import DataBlockFormatter as LabelFormatter
from pocketscope.render.labels import DataBlockLayout as LabelLayout
from pocketscope.render.labels import OwnshipRef
from pocketscope.render.labels import TrackSnapshot as LabelTrack

if TYPE_CHECKING:  # for type hints only
    from pocketscope.data.sectors import Sector

ColorBG: Color = (0, 0, 0, 255)
ColorRings: Color = (80, 80, 80, 255)
ColorOwnship: Color = (255, 255, 255, 255)
ColorTrails: Color = (0, 180, 255, 180)
ColorAircraft: Color = (255, 255, 0, 255)
ColorLabels: Color = (255, 255, 255, 255)
ColorDataBlock: Color = (0, 255, 0, 255)


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
        label_font_px: int = 12,
        label_line_gap_px: int = 2,
        label_block_pad_px: int = 2,
    ) -> None:
        self.range_nm = float(range_nm)
        # Optional view rotation used only for the north tick; 0 keeps legacy behavior
        self.rotation_deg = float(rotation_deg) % 360.0
        self.show_data_blocks = bool(show_data_blocks)
        self.show_simple_labels = bool(show_simple_labels)
        self.show_text_annotations = bool(show_text_annotations)
        self.show_airports = bool(show_airports)
        # Data-block typography
        self.label_font_px = int(label_font_px)
        self.label_line_gap_px = int(label_line_gap_px)
        self.label_block_pad_px = int(label_block_pad_px)

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

        # Range rings
        ring_ticks = [2.0, 5.0, 10.0]
        for nm in ring_ticks:
            if nm > self.range_nm:
                continue
            r_px = int((nm * meters_per_nm) / m_per_px)
            canvas.circle((cx, cy), r_px, width=1, color=ColorRings)
            if self.show_text_annotations:
                canvas.text(
                    (cx + r_px + 4, cy - 8),
                    f"{int(nm)} nm",
                    size_px=12,
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
                canvas.text((lx, ly), label, size_px=12, color=ColorLabels)

        _draw_cardinal(0.0, "N")
        _draw_cardinal(90.0, "E")
        _draw_cardinal(180.0, "S")
        _draw_cardinal(270.0, "W")

        # Optional sectors overlay: draw beneath ownship/tracks but above background.
        if sectors:
            try:
                from pocketscope.data.sectors import Sector as _Sector
                from pocketscope.render.sectors_layer import SectorsLayer

                _secs: list[_Sector] = list(sectors)
                SectorsLayer().draw(
                    canvas,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    range_nm=self.range_nm,
                    sectors=_secs,
                    screen_size=(w, h),
                    rotation_deg=self.rotation_deg,
                )
            except Exception:
                # Defensive: ignore any rendering issues to keep PPI robust
                pass

        # Ownship
        canvas.filled_circle((cx, cy), 4, color=ColorOwnship)

        # Origin for ENU conversion
        _ox, _oy, _oz = geodetic_to_ecef(center_lat, center_lon, 0.0)

        def _enu_to_screen_rot(e: float, n: float) -> Tuple[float, float]:
            """Rotate ENU by rotation_deg (clockwise positive) and map to screen.

            When rotation is 0, this is identical to enu_to_screen for golden stability.
            """
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

        # Airports layer first (so tracks render on top as before)
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
                AirportsLayer(font_px=12).draw(
                    canvas,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    range_nm=self.range_nm,
                    airports=aps,
                    screen_size=(w, h),
                    rotation_deg=self.rotation_deg,
                )
            except Exception:
                pass

        # Draw tracks deterministically
        _tracks = list(tracks)
        _tracks.sort(key=lambda t: ((t.callsign or ""), t.icao))
        for t in _tracks:
            gx, gy = to_screen(t.lat, t.lon)

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
                dx = sin(rad)
                dy = -cos(rad)
                size = 8
                tip = (gx + int(round(dx * size)), gy + int(round(dy * size)))
                base_len = 6
                base_w = 5
                bx, by = -dx * base_len, -dy * base_len
                px, py = -dy, dx
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
            if self.show_data_blocks and label_formatter is not None:
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
                        (gx + 6, gy - 12), label_text, size_px=12, color=ColorLabels
                    )

        # Draw data blocks last
        if self.show_data_blocks and label_layout is not None:
            placements = label_layout.place_blocks(label_items)
            for p in placements:
                ax, ay = p.anchor_px
                w_b, h_b = label_layout.measure(p.lines)
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
                for i, s in enumerate(p.lines):
                    y = p.y + i * (self.label_font_px + self.label_line_gap_px)
                    canvas.text(
                        (p.x + 2, y),
                        s,
                        size_px=self.label_font_px,
                        color=ColorDataBlock,
                    )
