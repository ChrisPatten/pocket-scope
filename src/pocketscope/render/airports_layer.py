"""Airports overlay layer for the PPI view.

Draws small square markers and monospaced ident labels for a list of airports.
Coordinates are converted from WGS-84 (lat/lon) to ENU relative to the PPI
center, then mapped to screen pixels.

Rules
-----
- Marker: 5x5 px square centered at (x, y), color dim gray.
- Label: airport ident rendered to the NE of marker with offset (+6, -8).
- Cull: Airports beyond the current range_nm are not drawn.
- On-screen: Labels are clamped to remain fully visible within the canvas.
"""

from __future__ import annotations

from typing import Sequence

from pocketscope.core.geo import (
    ecef_to_enu,
    enu_to_screen,
    geodetic_to_ecef,
    haversine_nm,
)
from pocketscope.data.airports import Airport
from pocketscope.render.canvas import Canvas

MarkerColor = (160, 160, 160, 255)
LabelColor = (255, 255, 255, 255)


class AirportsLayer:
    """
    Draws airport markers (small squares) and labels (ident) on the Canvas.
    Color: dim gray markers, white text, monospaced font.
    """

    def __init__(self, font_px: int = 12) -> None:
        self.font_px = int(font_px)

    @staticmethod
    def _clamp_label(x: int, y: int, w: int, h: int, W: int, H: int) -> tuple[int, int]:
        x = max(0, min(W - w, x))
        y = max(0, min(H - h, y))
        return (x, y)

    @staticmethod
    def _intersects_exclusions(
        x: int, y: int, w: int, h: int, exclusions: list[tuple[int, int, int, int]]
    ) -> bool:
        """Check if a rectangle intersects with any exclusion zone."""
        if not exclusions:
            return False

        for ex, ey, ew, eh in exclusions:
            # Check if rectangles overlap
            if x < ex + ew and x + w > ex and y < ey + eh and y + h > ey:
                return True
        return False

    def _find_best_label_position(
        self,
        sx: int,
        sy: int,
        text: str,
        char_w: int,
        label_h: int,
        W: int,
        H: int,
        exclusions: list[tuple[int, int, int, int]] | None,
    ) -> tuple[int, int] | None:
        """Find the best position for an airport label, avoiding exclusions."""
        tw = max(0, len(text) * char_w)

        # Try different positions around the airport marker in order of preference
        positions = [
            (sx + 6, sy - 8),  # NE (original)
            (sx + 6, sy + 8),  # SE
            (sx - tw - 6, sy - 8),  # NW
            (sx - tw - 6, sy + 8),  # SW
            (sx, sy - label_h - 8),  # N
            (sx, sy + 8),  # S
            (sx + 8, sy),  # E
            (sx - tw - 8, sy),  # W
        ]

        for tx, ty in positions:
            # Clamp to screen bounds
            tx_clamped, ty_clamped = self._clamp_label(tx, ty, tw, label_h, W, H)

            # Check if it intersects with exclusions
            if exclusions and self._intersects_exclusions(
                tx_clamped, ty_clamped, tw, label_h, exclusions
            ):
                continue

            return (tx_clamped, ty_clamped)

        # If no position works, return None to skip the label
        return None

    def draw(
        self,
        canvas: Canvas,
        center_lat: float,
        center_lon: float,
        range_nm: float,
        airports: Sequence[Airport],
        screen_size: tuple[int, int],
        rotation_deg: float = 0.0,
        range_ring_exclusions: list[tuple[int, int, int, int]] | None = None,
    ) -> None:
        """Render airport markers and labels.

        Convert each airport lat/lon to ENU relative to center, then to screen.
        - Marker: 5x5 px square centered at (x, y).
        - Label: ident to the NE of the marker with offset (+6, -8).
        - Cull airports outside the current range_nm (haversine).
        - Keep labels on-screen by clamping to canvas bounds.
        - Avoid placing labels in range ring exclusion zones.

        Args:
            range_ring_exclusions: List of (x, y, width, height) rectangles
                                 where airport labels should not be placed.
        """

        W, H = int(screen_size[0]), int(screen_size[1])
        cx, cy = W // 2, H // 2

        # Compute meters-per-pixel based on range to smallest half-dimension
        radius_px = max(10, min(W, H) // 2 - 6)
        meters_per_nm = 1852.0
        m_per_px = (range_nm * meters_per_nm) / float(radius_px)

        def to_screen(lat: float, lon: float) -> tuple[int, int]:
            tx, ty, tz = geodetic_to_ecef(lat, lon, 0.0)
            e, n, _ = ecef_to_enu(tx, ty, tz, center_lat, center_lon, 0.0)
            if (rotation_deg % 360.0) == 0.0:
                x, y = enu_to_screen(e, n, m_per_px)
            else:
                from math import cos as _cos
                from math import radians as _radians
                from math import sin as _sin

                phi = -_radians(rotation_deg)
                ce, se = _cos(phi), _sin(phi)
                er = e * ce - n * se
                nr = e * se + n * ce
                x, y = enu_to_screen(er, nr, m_per_px)
            return int(round(cx + x)), int(round(cy + y))

        # Simple fixed-size square via polyline (pygame backend supports this well)
        def draw_square(center: tuple[int, int], size: int = 5) -> None:
            x, y = center
            half = size // 2
            pts = [
                (x - half, y - half),
                (x + half, y - half),
                (x + half, y + half),
                (x - half, y + half),
                (x - half, y - half),
            ]
            canvas.polyline(pts, width=1, color=MarkerColor)

        # Conservative label measurement: assume monospace aspect
        char_w = max(6, int(round(self.font_px * 0.6)))
        label_h = self.font_px

        for ap in airports:
            # Range cull using haversine in NM
            if haversine_nm(center_lat, center_lon, ap.lat, ap.lon) > range_nm:
                continue

            sx, sy = to_screen(ap.lat, ap.lon)
            draw_square((sx, sy), size=5)

            # Find best position for label avoiding exclusions
            text = ap.ident
            label_pos = self._find_best_label_position(
                sx, sy, text, char_w, label_h, W, H, range_ring_exclusions
            )

            if label_pos is not None:
                tx, ty = label_pos
                canvas.text((tx, ty), text, size_px=self.font_px, color=LabelColor)
