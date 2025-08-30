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

from typing import Any, List, Tuple

from pocketscope.render.canvas import Canvas, Color
from pocketscope.render.fonts import get_mono

# Colors
_COLOR_BG: Color = (0, 0, 0, 160)
_COLOR_TEXT: Color = (255, 255, 255, 255)


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
    def __init__(self, font_px: int = 12) -> None:
        self._font_px = int(font_px)

    def draw(
        self,
        canvas: Canvas,
        *,
        fps_inst: float,
        fps_avg: float,
        range_nm: float,
        active_tracks: int,
        bus_summary: str,
        clock_utc: str,
    ) -> None:
        """
        Draw a semi-transparent panel with the provided metrics.

        Parameters
        ----------
        canvas: Target drawing surface for the current frame.
        fps_inst: Instantaneous frames-per-second.
        fps_avg: Exponential moving average FPS.
        range_nm: Current PPI range setting, nautical miles.
        active_tracks: Count of active tracks.
        bus_summary: Short EventBus metrics summary string.
        clock_utc: Wall-clock time (UTC) formatted as HH:MM:SSZ.
        """

        # Format text lines first to size the background
        lines: List[str] = [
            f"FPS {fps_inst:4.1f} ({fps_avg:4.1f})  RNG {range_nm:4.0f} nm",
            f"TRK {active_tracks:3d}  {bus_summary}",
            f"UTC {clock_utc}",
        ]

        pad_x, pad_y = 6, 4
        text_w, text_h = _measure_text_lines(lines, font_px=self._font_px)
        w = text_w + pad_x * 2
        h = text_h + pad_y * 2

        # Draw translucent background at top-left using scanlines
        # Canvas has no rect fill, so draw horizontal lines to simulate fill.
        for dy in range(h):
            canvas.line((0, dy), (w, dy), width=1, color=_COLOR_BG)

        # Draw text lines
        x = pad_x
        y = pad_y
        for s in lines:
            canvas.text((x, y), s, size_px=self._font_px, color=_COLOR_TEXT)
            y += self._font_px
