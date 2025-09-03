"""Simple on-screen soft-key bar."""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from pocketscope.render.canvas import Canvas, Color

_COLOR_BG: Color = (32, 32, 32, 255)
_COLOR_TEXT: Color = (255, 255, 255, 255)
_PAD_X = 4
_PAD_Y = 2


class SoftKeyBar:
    """Bottom bar with fixed set of buttons.

    Parameters
    ----------
    size: ``(width, height)`` of the display in pixels.
    font_px: Height of the monospace font.
    actions: Mapping from button label to callback.
    """

    def __init__(
        self,
        size: Tuple[int, int],
        *,
        font_px: int = 12,
        actions: Dict[str, Callable[[], None]] | None = None,
    ) -> None:
        self.size = size
        self.font_px = int(font_px)
        self.actions = actions or {
            "Zoom-": lambda: None,
            "Units": lambda: None,
            "Tracks": lambda: None,
            "Demo": lambda: None,
            "Settings": lambda: None,
            "Zoom+": lambda: None,
        }
        self._rects: List[Tuple[int, int, int, int]] = []

    # Layout --------------------------------------------------------------
    def layout(self) -> None:
        """Compute button rectangles."""
        w, h = self.size
        bar_h = self.font_px + _PAD_Y * 2
        y = h - bar_h
        labels = list(self.actions.keys())
        n = len(labels)
        btn_w = w // max(1, n)
        rects: List[Tuple[int, int, int, int]] = []
        for i in range(n):
            rects.append((i * btn_w, y, btn_w, bar_h))
        self._rects = rects

    # Drawing -------------------------------------------------------------
    def draw(self, canvas: Canvas) -> None:
        if not self._rects:
            self.layout()
        for (x, y, w, h), label in zip(self._rects, self.actions.keys()):
            for dy in range(h):
                canvas.line((x, y + dy), (x + w - 1, y + dy), color=_COLOR_BG)
            canvas.text(
                (x + _PAD_X, y + _PAD_Y), label, size_px=self.font_px, color=_COLOR_TEXT
            )

    # Interaction --------------------------------------------------------
    def _hit(self, x: int, y: int) -> str | None:
        for (rx, ry, rw, rh), label in zip(self._rects, self.actions.keys()):
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return label
        return None

    def on_mouse(self, x: int, y: int, pressed: bool) -> None:
        """Handle mouse presses in display coordinates."""
        if not pressed:
            return
        label = self._hit(x, y)
        if label:
            cb = self.actions.get(label)
            if cb:
                cb()

    def on_key(self, key: str) -> None:
        """Map simple keyboard shortcuts to actions."""
        key = key.lower()
        mapping = {
            "[": "Zoom-",
            "-": "Zoom-",
            "]": "Zoom+",
            "=": "Zoom+",
            "u": "Units",
            "t": "Tracks",
            "d": "Demo",
        }
        label = mapping.get(key)
        if label:
            cb = self.actions.get(label)
            if cb:
                cb()
