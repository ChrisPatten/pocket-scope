"""
PPI (Plan Position Indicator) view rendering.

This module provides a simple north-up PPI view that renders range rings,
ownship, aircraft glyphs with optional course triangles, trails, and labels.

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
from typing import Iterable, List, Optional, Sequence, Tuple

from pocketscope.core.geo import ecef_to_enu, enu_to_screen, geodetic_to_ecef
from pocketscope.render.canvas import Canvas, Color

ColorBG: Color = (0, 0, 0, 255)
ColorRings: Color = (80, 80, 80, 255)
ColorOwnship: Color = (255, 255, 255, 255)
ColorTrails: Color = (0, 180, 255, 180)
ColorAircraft: Color = (255, 255, 0, 255)
ColorLabels: Color = (255, 255, 255, 255)


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


class PpiView:
    def __init__(self, *, range_nm: float = 10.0) -> None:
        self.range_nm = float(range_nm)

    def draw(
        self,
        canvas: Canvas,
        *,
        size_px: Tuple[int, int],
        center_lat: float,
        center_lon: float,
        tracks: Iterable[TrackSnapshot],
        airports: Optional[Sequence[Tuple[float, float, str]]] = None,
    ) -> None:
        """Draw the PPI view contents.

        Parameters
        ----------
        canvas: Target canvas
        center_lat, center_lon: Scene center in degrees
        tracks: Iterable of TrackSnapshot with positions in lat/lon and optional
            trail in ENU
        airports: Optional list of (lat, lon, ident)
        """

        # Use provided size for deterministic layout
        w, h = int(size_px[0]), int(size_px[1])

        cx, cy = int(w // 2), int(h // 2)
        radius_px = int(min(w, h) // 2) - 6  # small margin
        if radius_px < 10:
            radius_px = 10

        # Compute meters per pixel from range_nm
        meters_per_nm = 1852.0
        range_m = self.range_nm * meters_per_nm
        m_per_px = range_m / float(radius_px)

        # Clear background
        canvas.clear(ColorBG)

        # Range rings (2, 5, 10 nm or up to range)
        ring_ticks = [2.0, 5.0, 10.0]
        for nm in ring_ticks:
            if nm > self.range_nm:
                continue
            r_px = int((nm * meters_per_nm) / m_per_px)
            canvas.circle((cx, cy), r_px, width=1, color=ColorRings)
            # Label to the right of center line
            label = f"{int(nm)} nm"
            canvas.text(
                (cx + r_px + 4, cy - 8),
                label,
                size_px=12,
                color=ColorLabels,
            )

        # North tick at top
        top_y = cy - radius_px
        canvas.line((cx, top_y), (cx, top_y + 12), width=2, color=ColorRings)
        canvas.text((cx + 4, top_y + 2), "N", size_px=12, color=ColorLabels)

        # Ownship symbol at center
        canvas.filled_circle((cx, cy), 4, color=ColorOwnship)

        # Precompute origin ECEF for ENU conversion
        # We'll use ENU via ecef transforms for each track lat/lon
        ox, oy, oz = geodetic_to_ecef(center_lat, center_lon, 0.0)

        def to_screen(lat: float, lon: float) -> Tuple[int, int]:
            tx, ty, tz = geodetic_to_ecef(lat, lon, 0.0)
            e, n, _ = ecef_to_enu(tx, ty, tz, center_lat, center_lon, 0.0)
            x, y = enu_to_screen(e, n, m_per_px)
            return int(round(cx + x)), int(round(cy + y))

        # Draw tracks
        for t in tracks:
            # Glyph position
            gx, gy = to_screen(t.lat, t.lon)

            # Trail first (under glyph)
            if t.trail_enu:
                pts: List[Tuple[int, int]] = []
                for e, n in t.trail_enu:
                    x, y = enu_to_screen(e, n, m_per_px)
                    pts.append((int(round(cx + x)), int(round(cy + y))))
                if len(pts) >= 2:
                    canvas.polyline(pts, width=2, color=ColorTrails)

            # Aircraft glyph
            if t.course_deg is not None:
                # Triangle pointing to course
                rad = radians(t.course_deg)
                # Heading unit vector in screen coords (y down)
                dx = sin(rad)
                dy = -cos(rad)
                size = 8
                # Tip point
                tip = (gx + int(round(dx * size)), gy + int(round(dy * size)))

                # Base points rotated by +/- 130 degrees around tip direction
                def rot(x: float, y: float, ang: float) -> Tuple[float, float]:
                    ca, sa = cos(ang), sin(ang)
                    return (x * ca - y * sa, x * sa + y * ca)

                # Construct base relative to center with some width
                base_len = 6
                base_w = 5
                # Vector backwards along heading
                bx, by = -dx * base_len, -dy * base_len
                # Perp vectors
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
                # Simple dot if no course
                canvas.filled_circle((gx, gy), 3, color=ColorAircraft)

            # Label (callsign or ICAO)
            label_text = t.callsign or t.icao
            canvas.text((gx + 6, gy - 12), label_text, size_px=12, color=ColorLabels)
