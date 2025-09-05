"""
Status overlay (HUD) rendering.

Draws a small translucent panel with FPS, range, active track count,
EventBus summary, and a UTC clock. Uses a monospaced font for alignment.

This module renders text via the framework-agnostic Canvas.text API.
For sizing the background panel, it uses pygame's font metrics obtained
through pocketscope.render.fonts.get_mono, but the returned handle is
not required by Canvas; it's only used for measuring text width/height.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from pocketscope.render.canvas import Canvas, Color
from pocketscope.render.fonts import get_mono
from pocketscope.settings.values import STATUS_OVERLAY_CONFIG, THEME

# Colors / defaults from theme
_SO_THEME = (
    THEME.get("colors", {}).get("status_overlay", {}) if isinstance(THEME, dict) else {}
)


def _c(v: object, fb: tuple[int, int, int, int]) -> Color:
    if (
        isinstance(v, (list, tuple))
        and len(v) == 4
        and all(isinstance(c, (int, float)) for c in v)
    ):
        return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))
    return fb


_COLOR_BG: Color = _c(_SO_THEME.get("bg"), (32, 32, 32, 180))
_COLOR_TEXT: Color = _c(_SO_THEME.get("text"), (255, 255, 255, 255))
_COLOR_BORDER: Color = _c(_SO_THEME.get("border"), (255, 255, 255, 255))


def _measure_text_lines(lines: List[str], *, font_px: int) -> Tuple[int, int]:
    """Measure max width and total height for a list of text lines.

    Uses pygame's Font.size when available (via get_mono), otherwise
    estimates width assuming ~0.6em per character which is sufficient
    to size the translucent background. Height is font_px per line.
    """

    try:
        import pygame

        # Ensure font subsystem is ready
        if not pygame.get_init():
            pygame.init()
        if not pygame.font.get_init():
            pygame.font.init()

        fh = get_mono(font_px)
        font_obj: Any = getattr(fh, "obj", None)
        max_w = 0
        for s in lines:
            if font_obj is not None:
                w, _ = font_obj.size(s)
            else:  # pragma: no cover - fallback
                w = int(len(s) * font_px * 0.6)
            if w > max_w:
                max_w = w
        total_h = font_px * len(lines)
        return max_w, total_h
    except Exception:
        # Conservative fallback in environments without pygame
        max_len = max((len(s) for s in lines), default=0)
        return int(max_len * font_px * 0.6), font_px * len(lines)


class StatusOverlay:
    """Two-line element-based status panel (no ASCII borders).

    Layout philosophy matches ``SoftKeyBar``: each line is divided into
    equal-width cells; content of each cell is centered using measured
    text widths. All values are provided as separate *elements* (no big
    concatenated strings) making future styling / per-element coloring
    straightforward.

    Public configuration mirrors softkeys: font size, per-cell padding,
    optional outer border, colors. Height automatically derives from
    font + padding (two lines by default; DEMO flag adds a third line).
    """

    def __init__(
        self,
        font_px: int = 12,
        *,
        pad_x: int = 4,
        pad_y: int = 2,
        border_width: int = 0,
        border_color: Color = _COLOR_BORDER,
        bg_color: Color = _COLOR_BG,
        text_color: Color = _COLOR_TEXT,
        measure_fn: Callable[[str, int], Tuple[int, int]] | None = None,
        width_px: int | None = None,
    ) -> None:
        self.font_px = int(font_px)
        self.pad_x = max(0, int(pad_x))
        self.pad_y = max(0, int(pad_y))
        self.border_width = max(0, int(border_width))
        self.border_color = border_color
        self.bg_color = bg_color
        self.text_color = text_color
        self.width_px = width_px  # if None we compute dyn based on content
        self._measure_cache: Dict[Tuple[str, int], Tuple[int, int]] = {}
        self._measure_fn = measure_fn or self._measure_text_internal

    # ------------------------------------------------------------------
    def draw(
        self,
        canvas: Canvas,
        *,
        range_nm: float,
        clock_utc: str,
        center_lat: float,
        center_lon: float,
        gps_ok: bool = True,
        imu_ok: bool = True,
        decoder_ok: bool = True,
        units: str = "nm_ft_kt",
        demo_mode: bool = False,
    ) -> None:
        # --- Build element arrays (no concatenation) ------------------
        if units == "mi_ft_mph":
            rng = range_nm * 1.15078
            rng_units = "mi"
        elif units == "km_m_kmh":
            rng = range_nm * 1.852
            rng_units = "km"
        else:
            rng = range_nm
            rng_units = "nm"

        def mark(ok: bool) -> str:
            return "ok" if ok else "x"

        lat_dir = "N" if center_lat >= 0 else "S"
        lon_dir = "E" if center_lon >= 0 else "W"
        lat_el = f"Lat {abs(center_lat):.2f}{lat_dir}"
        lon_el = f"Lon {abs(center_lon):.2f}{lon_dir}"
        cfg_elems = STATUS_OVERLAY_CONFIG.get("elements", {})
        # Build line1
        line1_keys = cfg_elems.get("line1", ["GPS", "IMU", "DEC", "RNG"])
        line1: List[str] = []
        for key in line1_keys:
            k = key.upper()
            if k == "GPS":
                line1.append(f"GPS {mark(gps_ok)}")
            elif k == "IMU":
                line1.append(f"IMU {mark(imu_ok)}")
            elif k == "DEC":
                line1.append(f"DEC {mark(decoder_ok)}")
            elif k == "RNG":
                line1.append(f"RNG {rng:.0f}{rng_units}")
        line2_keys = cfg_elems.get("line2", ["CLOCK", "LAT", "LON"])
        line2: List[str] = []
        for key in line2_keys:
            k = key.upper()
            if k == "CLOCK":
                line2.append(clock_utc)
            elif k == "LAT":
                line2.append(lat_el)
            elif k == "LON":
                line2.append(lon_el)
        lines: List[List[str]] = [line1, line2]
        if demo_mode:
            demo_line = cfg_elems.get("demo_line", "DEMO MODE")
            lines.append([demo_line])  # third single-cell line

        # --- Determine per-line cell counts & widths ------------------
        # For auto-width we take max of total rendered widths among lines
        # treating each line's width as sum(cell_width) where cell_width is
        # the max(measured text width + 2*pad_x) for its cell distribution.
        # Simpler: choose width = max number of cells * widest cell.
        measure = self._measure_fn
        widest_cell = 1
        max_cells = 1
        for cells in lines:
            max_cells = max(max_cells, len(cells))
            for text in cells:
                try:
                    tw, _ = measure(text, self.font_px)
                except Exception:
                    tw = int(self.font_px * 0.6) * len(text)
                widest_cell = max(widest_cell, tw + 2 * self.pad_x)
        width = self.width_px or (max_cells * widest_cell)
        line_height = self.font_px + 2 * self.pad_y
        panel_h = line_height * len(lines)

        # --- Background fill ------------------------------------------
        for dy in range(panel_h):
            canvas.line((0, dy), (width - 1, dy), color=self.bg_color)

        # --- Draw each line's cells -----------------------------------
        y = 0
        for cells in lines:
            n = max(1, len(cells))
            cell_w = width // n
            for i, text in enumerate(cells):
                x0 = i * cell_w
                try:
                    tw, th = measure(text, self.font_px)
                except Exception:
                    tw, th = (int(self.font_px * 0.6) * len(text), self.font_px)
                inner_left = x0 + self.pad_x
                inner_right = x0 + cell_w - self.pad_x
                avail_w = max(1, inner_right - inner_left)
                tx = inner_left + max(0, (avail_w - tw) // 2)
                ty = y + (line_height - th) // 2
                canvas.text((tx, ty), text, size_px=self.font_px, color=self.text_color)
            y += line_height

        # --- Optional outer border ------------------------------------
        if self.border_width > 0:
            bw = self.border_width
            for i in range(bw):
                # top
                canvas.line((0, i), (width - 1, i), color=self.border_color)
                # bottom
                canvas.line(
                    (0, panel_h - 1 - i),
                    (width - 1, panel_h - 1 - i),
                    color=self.border_color,
                )
                # left
                canvas.line((i, 0), (i, panel_h - 1), color=self.border_color)
                # right
                canvas.line(
                    (width - 1 - i, 0),
                    (width - 1 - i, panel_h - 1),
                    color=self.border_color,
                )

    # ------------------------------------------------------------------
    def _measure_text_internal(self, text: str, size_px: int) -> Tuple[int, int]:
        key = (text, size_px)
        cached = self._measure_cache.get(key)
        if cached is not None:
            return cached
        try:
            import pygame as _pg

            if not _pg.get_init():  # defensive init
                _pg.init()
            if not _pg.font.get_init():
                _pg.font.init()
            font = _pg.font.Font(None, size_px)
            # pygame's size returns a 2-tuple of ints (w,h)
            wh: Tuple[int, int] = font.size(text)
        except Exception:
            wh = (int(size_px * 0.6) * len(text), size_px)
        self._measure_cache[key] = wh
        return wh
