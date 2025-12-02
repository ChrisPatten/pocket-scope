"""
Pillow-backed canvas implementation with operation counting.

This module provides a reusable Canvas implementation backed by Pillow
(PIL.Image) with support for RGBA color space and operation counting for
heuristics and metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from pocketscope.render.canvas import Canvas, Color


@dataclass(slots=True)
class FontCache:
    """Font cache for efficient TrueType font loading."""

    fonts: dict[int, Any]

    def __init__(self) -> None:
        self.fonts = {}

    def get(self, size_px: int) -> Any:
        """Get or load a font at the specified size (pixels).

        Attempts to load TrueType fonts from standard system paths,
        falling back to the default Pillow font if necessary.

        Args:
            size_px: Font size in pixels.

        Returns:
            PIL ImageFont instance.
        """
        f = self.fonts.get(size_px)
        if f is None:
            try:
                candidates = [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
                    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
                    "/Library/Fonts/Menlo.ttc",
                    "/Library/Fonts/Consolas.ttf",
                ]
                font: Any = None
                for p in candidates:
                    try:
                        font = ImageFont.truetype(p, size_px)
                        break
                    except Exception:
                        continue
                if font is None:
                    try:
                        font = ImageFont.truetype("DejaVuSansMono.ttf", size_px)
                    except Exception:
                        font = ImageFont.load_default()
                f = font
            except Exception:
                f = ImageFont.load_default()
            self.fonts[size_px] = f
        return f


class PillowCanvas(Canvas):
    """
    RGBA canvas implementation backed by Pillow Image.

    Supports full RGBA color space with alpha blending. Tracks drawing
    operations for metrics and heuristics (e.g., blank frame detection).
    """

    def __init__(self, img: Image.Image, fonts: FontCache) -> None:
        """Initialize canvas from existing Pillow Image.

        Args:
            img: Pillow Image in RGBA mode.
            fonts: FontCache instance for text rendering.
        """
        self._img = img
        self._draw = ImageDraw.Draw(img)
        self._fonts = fonts
        self._ops = 0

    def clear(self, color: Color) -> None:
        """Clear canvas with solid color (supports alpha).

        Args:
            color: RGBA color tuple (0..255).
        """
        r, g, b, a = color
        w, h = self._img.size
        self._draw.rectangle((0, 0, w, h), fill=(r, g, b, a))
        self._ops += 1

    def line(
        self,
        p0: Tuple[int, int],
        p1: Tuple[int, int],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        """Draw line with optional transparency.

        Args:
            p0: Start point (x, y).
            p1: End point (x, y).
            width: Line width in pixels.
            color: RGBA color tuple.
        """
        self._draw.line([p0, p1], fill=color, width=width)
        self._ops += 1

    def circle(
        self,
        center: Tuple[int, int],
        radius: int,
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        """Draw circle outline with optional transparency.

        Args:
            center: Circle center (x, y).
            radius: Radius in pixels.
            width: Outline width in pixels.
            color: RGBA color tuple.
        """
        x, y = center
        bbox = [x - radius, y - radius, x + radius, y + radius]
        self._draw.ellipse(bbox, outline=color, width=max(1, width))
        self._ops += 1

    def filled_circle(self, center: Tuple[int, int], radius: int, color: Color) -> None:
        """Draw filled circle with optional transparency.

        Args:
            center: Circle center (x, y).
            radius: Radius in pixels.
            color: RGBA color tuple.
        """
        x, y = center
        bbox = [x - radius, y - radius, x + radius, y + radius]
        self._draw.ellipse(bbox, fill=color)
        self._ops += 1

    def polyline(
        self,
        pts: Sequence[Tuple[int, int]],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        """Draw polyline with optional transparency.

        Args:
            pts: Sequence of (x, y) points.
            width: Line width in pixels.
            color: RGBA color tuple.
        """
        if pts:
            self._draw.line(list(pts), fill=color, width=width)
            self._ops += 1

    def text(
        self,
        pos: Tuple[int, int],
        s: str,
        size_px: int = 12,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        """Draw text with optional transparency.

        Args:
            pos: Text position (x, y).
            s: Text string.
            size_px: Font size in pixels.
            color: RGBA color tuple.
        """
        font = self._fonts.get(size_px)
        self._draw.text(pos, s, fill=color, font=font)
        self._ops += 1

    def get_op_count(self) -> int:
        """Return total drawing operations since canvas creation.

        Returns:
            Operation count.
        """
        return self._ops

    def reset_op_count(self) -> None:
        """Reset operation counter to zero."""
        self._ops = 0
