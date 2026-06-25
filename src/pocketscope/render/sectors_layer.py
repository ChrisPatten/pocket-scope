from __future__ import annotations

import logging
from typing import Sequence, Tuple

from pocketscope.core.geo import (
    ecef_to_enu,
    enu_to_screen,
    geodetic_to_ecef,
    haversine_nm,
)
from pocketscope.data.sectors import Sector
from pocketscope.render.canvas import Canvas
from pocketscope.render.geo_cache import global_cache
from pocketscope.render.geom_np import _any_within_nm, _np, _simplify_ring_np
from pocketscope.theme import ThemeManager

logger = logging.getLogger(__name__)

# Pixel tolerance scale for sector-boundary RDP simplification. Sector polygons
# are sub-pixel-dense airspace outlines; a tolerance of ~base_px * 1.0 strips
# detail no smaller than a pixel without any visible change, while cutting the
# per-frame polyline draw cost dramatically. Mirrors the state-boundary path.
_SECTOR_SIMPLIFY_DYN_FACTOR = 1.0
_SECTOR_SIMPLIFY_BASE_PX = 0.7

# Module-level cache of (vertex_count, lat_col, lon_col) keyed by the identity
# of a sector's point list. ``SectorsLayer`` is re-instantiated every frame by
# the PPI view, so a per-instance cache would never survive; sector geometry is
# static for the process lifetime, making id()-keyed reuse safe and effective.
_NP_COLS: dict[int, tuple[int, object, object]] = {}


class SectorsLayer:
    def __init__(
        self,
        color: tuple[int, int, int, int] | None = None,
        width_px: int = 1,
        show_labels: bool = True,
    ) -> None:
        # Color resolved from theme (sector.line) if not explicitly provided
        self._color_override = color
        self.width_px = int(width_px)
        self.show_labels = bool(show_labels)

    def _sector_cols(self, s: Sector) -> tuple[object, object]:
        """Return cached (lat_col, lon_col) NumPy columns for a sector.

        ``s.points`` is a list of (lat, lon) tuples. The columns are built once
        and reused across frames; ``(None, None)`` when NumPy is unavailable.
        """
        if _np is None:
            return (None, None)
        n = len(s.points)
        cache_key = id(s.points)
        cached = _NP_COLS.get(cache_key)
        if cached is not None and cached[0] == n:
            return (cached[1], cached[2])
        arr = _np.asarray(s.points, dtype=_np.float64)
        lat_col = arr[:, 0]
        lon_col = arr[:, 1]
        _NP_COLS[cache_key] = (n, lat_col, lon_col)
        return (lat_col, lon_col)

    def draw(
        self,
        canvas: Canvas,
        center_lat: float,
        center_lon: float,
        range_nm: float,
        sectors: Sequence[Sector],
        screen_size: Tuple[int, int],
        rotation_deg: float = 0.0,
        *,
        ppi_center_px: tuple[int, int] | None = None,
        ppi_radius_px: int | None = None,
        ppi_m_per_px: float | None = None,
    ) -> None:
        """
        - For each sector polygon:
          * Cull if all vertices farther than 2×range_nm from center.
          * Convert each lat/lon to ENU then screen coords.
          * Draw polyline connecting vertices (closed).
          * Label sector name near centroid (monospaced white, size 10 px).
        """
        W, H = int(screen_size[0]), int(screen_size[1])
        if ppi_center_px is not None:
            cx, cy = int(ppi_center_px[0]), int(ppi_center_px[1])
        else:
            cx, cy = W // 2, H // 2
        if ppi_radius_px is not None and ppi_m_per_px is not None:
            radius_px = int(ppi_radius_px)
            m_per_px = float(ppi_m_per_px)
        else:
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

        # Resolve theme colors (live) each draw
        try:
            base_color = self._color_override if self._color_override is not None else ThemeManager.color("sector.line")
        except Exception:
            base_color = (128, 128, 128, 100)
        try:
            label_color = ThemeManager.color("sector.label")
        except Exception:
            label_color = (255, 255, 255, 220)

        cache = global_cache()
        total_sectors = len(sectors)
        drawn_sectors = 0
        cull_nm = 2.0 * range_nm
        for s in sorted(sectors, key=lambda s: s.name):
            if not s.points:
                continue
            # Obtain (cached) NumPy lat/lon columns for this sector.
            lat_col, lon_col = self._sector_cols(s)
            # Cull: keep if any vertex within 2x range. Vectorised when NumPy is
            # available; falls back to the original per-vertex scalar loop.
            if _np is not None and lat_col is not None:
                keep = _any_within_nm(lat_col, lon_col, center_lat, center_lon, cull_nm)
            else:
                keep = False
                for lat, lon in s.points:
                    if haversine_nm(center_lat, center_lon, lat, lon) <= cull_nm:
                        keep = True
                        break
            if not keep:
                continue
            drawn_sectors += 1

            def _build() -> list[list[tuple[int, int]]]:
                # Simplify the high-resolution outline (sub-pixel RDP) before
                # projecting, so the cached polyline has far fewer vertices to
                # draw each frame. Simplification only runs on a cache miss.
                ring = list(s.points)
                if _np is not None and lat_col is not None:
                    try:
                        ring = _simplify_ring_np(
                            ring,
                            lat_col,
                            lon_col,
                            _SECTOR_SIMPLIFY_DYN_FACTOR,
                            m_per_px,
                            _SECTOR_SIMPLIFY_BASE_PX,
                        )
                    except Exception:
                        ring = list(s.points)
                pts_local = [to_screen(lat, lon) for (lat, lon) in ring]
                if pts_local and pts_local[0] != pts_local[-1]:
                    pts_local.append(pts_local[0])
                return [pts_local]

            entry = cache.get_or_build(
                layer="sectors",
                obj_id=s.name,
                range_nm=float(range_nm),
                rotation_deg=float(rotation_deg),
                center_lat=float(center_lat),
                center_lon=float(center_lon),
                display_px=(W, H),
                m_per_px=m_per_px,
                build_fn=_build,
            )
            pts = entry.screen_pts[0]

            # Outline
            canvas.polyline(pts, width=self.width_px, color=base_color)

            # Label: simple centroid of screen points
            # (excluding duplicate last point)
            if self.show_labels:
                try:
                    core_pts = pts[:-1] if (len(pts) >= 2 and pts[0] == pts[-1]) else pts
                    if core_pts:
                        sx = sum(p[0] for p in core_pts) / float(len(core_pts))
                        sy = sum(p[1] for p in core_pts) / float(len(core_pts))
                        canvas.text(
                            (int(round(sx)), int(round(sy))),
                            s.name,
                            size_px=10,
                            color=label_color,
                        )  # label centroid
                except Exception:
                    # Non-critical; skip label if any math/render issue
                    pass

        logger.info(
            f"Drew {drawn_sectors} out of {total_sectors} sectors (center: {center_lat:.3f}, {center_lon:.3f}, range: {range_nm} nm)"  # noqa: E501
        )
