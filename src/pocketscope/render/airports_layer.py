"""Airports overlay layer for the PPI view.

Draws compact diamond markers (rotated squares) and monospaced ident labels
for a list of airports. Coordinates are converted from WGS-84 (lat/lon) to ENU
relative to the PPI center, then mapped to screen pixels.

Rules
-----
- Marker: 5 px diameter diamond (cardinal points) centered at (x, y), dim gray.
- Label: airport ident rendered to the NE of marker with offset (+6, -8).
- Cull: Airports beyond the current range_nm are not drawn.
- On-screen: Labels are clamped to remain fully visible within the canvas.
"""

from __future__ import annotations

import os
from typing import Sequence

from pocketscope.core.geo import (
    ecef_to_enu,
    enu_to_screen,
    geodetic_to_ecef,
    haversine_nm,
)
from pocketscope.data.airports import Airport
from pocketscope.data.runways_store import get_runways_for_airport
from pocketscope.render.airport_icon import AirportIconRenderer
from pocketscope.render.canvas import Canvas, Color
from pocketscope.settings.values import THEME

_AL_THEME = (
    THEME.get("colors", {}).get("airports_layer", {}) if isinstance(THEME, dict) else {}
)


def _coerce_color(val: object, fallback: tuple[int, int, int, int]) -> Color:
    if (
        isinstance(val, (list, tuple))
        and len(val) == 4
        and all(isinstance(c, (int, float)) for c in val)
    ):
        r, g, b, a = (int(val[0]), int(val[1]), int(val[2]), int(val[3]))
        return (r, g, b, a)
    return fallback


MarkerColor: Color = _coerce_color(_AL_THEME.get("marker"), (160, 160, 160, 255))
LabelColor: Color = _coerce_color(_AL_THEME.get("label"), (255, 255, 255, 255))


class AirportsLayer:
    """Render airport markers (small diamonds) and labels (ident).

    A diamond improves legibility vs a filled square at small sizes by reducing
    perceived visual weight while remaining distinct from circular track dots.
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
        occupied: list[tuple[int, int, int, int]] | None = None,
    ) -> tuple[int, int] | None:
        """Find a non-overlapping label position near (sx, sy).

        Tries a set of candidate offsets (NE, SE, NW, SW, N, S, E, W). For each
        candidate, clamps to screen bounds and rejects any position that
        intersects with provided ``exclusions`` (e.g., range ring labels) or
        previously placed airport label rectangles in ``occupied``. If all
        direct candidates collide, performs a small spiral nudge around each
        candidate to find the first collision-free placement.

        Returns top-left (x, y) if a position is found; otherwise None to skip
        rendering the label.
        """
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

        avoid_rects: list[tuple[int, int, int, int]] = []
        if exclusions:
            avoid_rects.extend(exclusions)
        if occupied:
            avoid_rects.extend(occupied)

        # First pass: try canonical positions in order; return first that fits
        candidates: list[tuple[int, int]] = []
        for tx, ty in positions:
            txc, tyc = self._clamp_label(tx, ty, tw, label_h, W, H)
            candidates.append((txc, tyc))
            if not avoid_rects or not self._intersects_exclusions(
                txc, tyc, tw, label_h, avoid_rects
            ):
                return (txc, tyc)

        # Second pass: if all canonical positions collide, do a small spiral
        # around the first preferred candidate to find a nearby fit.
        if candidates:
            base_x, base_y = candidates[0]
            step = 6
            max_attempts = 48
            attempts = 0
            while attempts < max_attempts:
                r = 1 + attempts // 4
                dir_idx = attempts % 4
                dx = [step * r, 0, -step * r, 0][dir_idx]
                dy = [0, step * r, 0, -step * r][dir_idx]
                nx, ny = self._clamp_label(base_x + dx, base_y + dy, tw, label_h, W, H)
                if not avoid_rects or not self._intersects_exclusions(
                    nx, ny, tw, label_h, avoid_rects
                ):
                    return (nx, ny)
                attempts += 1

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
        runway_sqlite: str | None = None,
        runway_icons: bool = False,
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

        # Diamond marker (rotated square) using 4 cardinal points.
        # Using polyline keeps backend requirements identical to previous square.
        def draw_diamond(center: tuple[int, int], size: int = 5) -> None:
            x, y = center
            r = size // 2  # radius from center to a point
            pts = [
                (x, y - r),  # top
                (x + r, y),  # right
                (x, y + r),  # bottom
                (x - r, y),  # left
                (x, y - r),  # close
            ]
            canvas.polyline(pts, width=1, color=MarkerColor)

        # Conservative label measurement: assume monospace aspect
        char_w = max(6, int(round(self.font_px * 0.6)))
        label_h = self.font_px

        # Track placed label rectangles to avoid overlaps between airport labels
        placed_labels: list[tuple[int, int, int, int]] = []

        for ap in airports:
            # Range cull using haversine in NM
            if haversine_nm(center_lat, center_lon, ap.lat, ap.lon) > range_nm:
                continue

            sx, sy = to_screen(ap.lat, ap.lon)
            # Prefer runway icon rendering when a sqlite cache file exists.
            # Do NOT attempt to connect/create sqlite unless the file is present
            # to avoid accidental DB creation. Fall back to diamond otherwise.
            if runway_sqlite and os.path.exists(os.path.expanduser(runway_sqlite)):
                try:
                    rw = get_runways_for_airport(runway_sqlite, ap.ident)
                    renderer = AirportIconRenderer(canvas)
                    # estimate pixels_per_meter from m_per_px
                    radius_px = max(10, min(W, H) // 2 - 6)
                    meters_per_nm = 1852.0
                    m_per_px = (range_nm * meters_per_nm) / float(radius_px)
                    ppm = 1.0 / m_per_px
                    renderer.draw((sx, sy), rw, ppm)
                except Exception:
                    draw_diamond((sx, sy), size=5)
            else:
                draw_diamond((sx, sy), size=5)

            # Find best position for label avoiding exclusions and prior labels
            text = ap.ident
            label_pos = self._find_best_label_position(
                sx,
                sy,
                text,
                char_w,
                label_h,
                W,
                H,
                range_ring_exclusions,
                placed_labels,
            )

            if label_pos is not None:
                tx, ty = label_pos
                canvas.text((tx, ty), text, size_px=self.font_px, color=LabelColor)
                # Record occupied label rectangle (x, y, w, h)
                tw = max(0, len(text) * char_w)
                placed_labels.append((tx, ty, tw, label_h))
