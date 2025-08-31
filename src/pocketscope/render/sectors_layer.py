from __future__ import annotations

from typing import Sequence, Tuple

from pocketscope.core.geo import (
    ecef_to_enu,
    enu_to_screen,
    geodetic_to_ecef,
    haversine_nm,
)
from pocketscope.data.sectors import Sector
from pocketscope.render.canvas import Canvas


class SectorsLayer:
    def __init__(
        self, color: tuple[int, int, int, int] = (80, 120, 200, 100), width_px: int = 1
    ) -> None:
        self.color = (int(color[0]), int(color[1]), int(color[2]), int(color[3]))
        self.width_px = int(width_px)

    def draw(
        self,
        canvas: Canvas,
        center_lat: float,
        center_lon: float,
        range_nm: float,
        sectors: Sequence[Sector],
        screen_size: Tuple[int, int],
        rotation_deg: float = 0.0,
    ) -> None:
        """
        - For each sector polygon:
          * Cull if all vertices farther than 2×range_nm from center.
          * Convert each lat/lon to ENU then screen coords.
          * Draw polyline connecting vertices (closed).
          * Label sector name near centroid (monospaced white, size 10 px).
        """
        W, H = int(screen_size[0]), int(screen_size[1])
        cx, cy = W // 2, H // 2

        # Compute meters-per-pixel for mapping
        radius_px = max(10, min(W, H) // 2 - 6)
        m_per_px = (range_nm * 1852.0) / float(radius_px)

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

        # Deterministic draw order: by name
        for s in sorted(sectors, key=lambda s: s.name):
            if not s.points:
                continue
            # Cull: keep if any vertex within 2x range
            keep = False
            for lat, lon in s.points:
                d = haversine_nm(center_lat, center_lon, lat, lon)
                if d <= (2.0 * range_nm):
                    keep = True
                    break
            if not keep:
                continue

            # Convert to screen points, close polygon
            pts = [to_screen(lat, lon) for (lat, lon) in s.points]
            if pts and pts[0] != pts[-1]:
                pts.append(pts[0])

            # Outline
            canvas.polyline(pts, width=self.width_px, color=self.color)

            # Label at simple centroid (average of vertices) projected to screen
            lat_c = sum(p[0] for p in s.points) / float(len(s.points))
            lon_c = sum(p[1] for p in s.points) / float(len(s.points))
            lx, ly = to_screen(lat_c, lon_c)
            canvas.text(
                (lx + 2, ly + 2), s.name, size_px=10, color=(255, 255, 255, 255)
            )
