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
        # Prefer Pillow measurement so sizing matches the Pillow-backed
        # Canvas used by the ILI9341 backend. Try common TTF paths first.
        try:
            from PIL import ImageFont

            candidates = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
                "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
                "/Library/Fonts/Menlo.ttc",
                "/Library/Fonts/Consolas.ttf",
            ]
            font = None
            for p in candidates:
                try:
                    font = ImageFont.truetype(p, font_px)
                    break
                except Exception:
                    continue
            if font is None:
                try:
                    font = ImageFont.truetype("DejaVuSansMono.ttf", font_px)
                except Exception:
                    font = ImageFont.load_default()
            max_w = 0
            for s in lines:
                try:
                    m = font.getmask(s)
                    w = m.size[0]
                except Exception:
                    w = int(len(s) * font_px * 0.6)
                if w > max_w:
                    max_w = w
            total_h = font_px * len(lines)
            return max_w, total_h
        except Exception:
            # Fall back to pygame-based measurement if Pillow isn't present
            import pygame

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
        # Conservative fallback in environments without Pillow/pygame
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
        pad_x: int | None = None,
        pad_y: int | None = None,
        pad_top: int | None = None,
        pad_bottom: int | None = None,
        bg_color: Color = _COLOR_BG,
        text_color: Color = _COLOR_TEXT,
        measure_fn: Callable[[str, int], Tuple[int, int]] | None = None,
        width_px: int | None = None,
    ) -> None:
        self.font_px = int(font_px)
        # Compute sensible defaults scaled to the font size when caller
        # doesn't specify explicit padding values. This keeps the overlay
        # visually consistent across font sizes on different displays.
        if pad_x is None:
            self.pad_x = max(2, int(round(self.font_px * 0.3)))
        else:
            self.pad_x = max(0, int(pad_x))
        if pad_y is None:
            self.pad_y = max(1, int(round(self.font_px * 0.15)))
        else:
            self.pad_y = max(0, int(pad_y))
        # Top/bottom padding used to compute automatic panel height
        if pad_top is None:
            self.pad_top = max(2, int(round(self.font_px * 0.4)))
        else:
            self.pad_top = max(0, int(pad_top))
        if pad_bottom is None:
            self.pad_bottom = max(2, int(round(self.font_px * 0.25)))
        else:
            self.pad_bottom = max(0, int(pad_bottom))
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
        # Compute an automatic panel width based on measured text widths so
        # short info blocks produce narrower translucent backgrounds. For
        # each line we sum measured cell widths (text + 2*pad_x) and pick
        # the maximum across lines. Callers can still override with
        # self.width_px.
        measure = self._measure_fn
        line_widths: list[int] = []
        for cells in lines:
            total_w = 0
            for text in cells:
                try:
                    tw, _ = measure(text, self.font_px)
                except Exception:
                    tw = int(self.font_px * 0.6) * len(text)
                total_w += tw + 2 * self.pad_x
            # Ensure at least a tiny width to avoid zero / negative cases
            line_widths.append(max(1, int(total_w)))
        computed_width = max(line_widths) if line_widths else 1
        width = self.width_px or computed_width
        line_height = self.font_px + 2 * self.pad_y
        # Include top/bottom padding in total panel height so the overlay
        # visually separates from content above/below and scales with font.
        panel_h = self.pad_top + (line_height * len(lines)) + self.pad_bottom

        # --- Background fill ------------------------------------------
        for dy in range(panel_h):
            canvas.line((0, dy), (width - 1, dy), color=self.bg_color)

        # --- Draw each line's cells -----------------------------------
        y = self.pad_top
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

    # No border: overlay is a translucent band only

    # ------------------------------------------------------------------
    def _measure_text_internal(self, text: str, size_px: int) -> Tuple[int, int]:
        key = (text, size_px)
        cached = self._measure_cache.get(key)
        if cached is not None:
            return cached
        try:
            # Prefer Pillow measurement when available so measurements match
            # the Pillow-backed Canvas used by the ILI9341 backend.
            try:
                from PIL import ImageFont

                # Try to obtain a truetype font similar to the backend's
                # _FontCache; fall back to default ImageFont if not found.
                try:
                    # Use a common monospace name first; if it fails,
                    # ImageFont.load_default will provide a bitmap font.
                    font = ImageFont.truetype("DejaVuSansMono.ttf", size_px)
                except Exception:
                    try:
                        font = ImageFont.load_default()
                    except Exception:
                        font = None
                # Initialize wh to a conservative estimate; may be replaced
                # by precise measurement below. This avoids mypy thinking
                # the name is conditionally defined in multiple places.
                wh = (int(size_px * 0.6) * len(text), size_px)
                if font is not None:
                    # Use getmask to obtain rendered mask size which is
                    # consistent across Pillow font implementations.
                    try:
                        m = font.getmask(text)
                        wh = (m.size[0], m.size[1])
                    except Exception:
                        # keep conservative fallback
                        pass
            except Exception:
                # Fallback to pygame if Pillow not available / failed
                import pygame as _pg

                if not _pg.get_init():  # defensive init
                    _pg.init()
                if not _pg.font.get_init():
                    _pg.font.init()
                font = _pg.font.Font(None, size_px)
                # pygame's size returns a 2-tuple of ints (w,h)
                wh = font.size(text)
        except Exception:
            wh = (int(size_px * 0.6) * len(text), size_px)
        self._measure_cache[key] = wh
        return wh
