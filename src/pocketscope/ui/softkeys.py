"""Simple on-screen soft-key bar."""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from pocketscope.render.canvas import Canvas, Color
from pocketscope.settings.values import THEME

_SK_THEME = (
    THEME.get("colors", {}).get("softkeys", {}) if isinstance(THEME, dict) else {}
)


def _c(v: object, fb: tuple[int, int, int, int]) -> Color:
    if (
        isinstance(v, (list, tuple))
        and len(v) == 4
        and all(isinstance(c, (int, float)) for c in v)
    ):
        return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))
    return fb


_COLOR_BG: Color = _c(_SK_THEME.get("bg"), (32, 32, 32, 255))
_COLOR_TEXT: Color = _c(_SK_THEME.get("text"), (255, 255, 255, 255))
_COLOR_BORDER: Color = _c(_SK_THEME.get("border"), (255, 0, 0, 255))
_PAD_X_DEFAULT = 4
_PAD_Y_DEFAULT = 2


class SoftKeyBar:
    """Bottom bar with fixed set of buttons.

    Parameters
    ----------
    size: ``(width, height)`` of the display in pixels.
    font_px: Height of the monospace font.
    actions: Mapping from button label to callback.
    bar_height: Optional explicit height of the softkey bar. If provided
        the font size will be auto-scaled to fit within the bar while
        preserving vertical padding.
    pad_x / pad_y: Per-button internal padding (pixels). These are
        considered when auto-scaling the font to ensure labels fit both
        vertically (bar_height - 2*pad_y) and horizontally
        (btn_width - 2*pad_x).
    measure_fn: Optional callable used to measure rendered text size as
        (width, height) for a given (label, font_px). When provided it is
        used for exact horizontal centering and to refine auto-scaling so
        the largest possible font that fits the widest label is chosen.
        If not provided we attempt an internal measurement via pygame,
        falling back to a simple monospace approximation.
    """

    def __init__(
        self,
        size: Tuple[int, int],
        *,
        font_px: int = 12,
        actions: Dict[str, Callable[[], None]] | None = None,
        bar_height: int | None = None,
        pad_x: int = _PAD_X_DEFAULT,
        pad_y: int = _PAD_Y_DEFAULT,
        measure_fn: Callable[[str, int], Tuple[int, int]] | None = None,
        border_color: Color = _COLOR_BORDER,
        border_width: int = 0,
    ) -> None:
        self.size = size
        self.bar_height = int(bar_height) if bar_height is not None else None
        self._requested_font_px = int(font_px)
        self.pad_x = max(0, int(pad_x))
        self.pad_y = max(0, int(pad_y))
        # Resolved measurement function (never None after init)
        self.measure_fn: Callable[[str, int], Tuple[int, int]] = (
            measure_fn or self._measure_text_internal
        )
        # Actual resolved font size (final after layout). Start with request.
        self.font_px = self._requested_font_px
        self.actions = actions or {
            "Zoom-": lambda: None,
            "Units": lambda: None,
            "Tracks": lambda: None,
            "Demo": lambda: None,
            "Settings": lambda: None,
            "Zoom+": lambda: None,
        }
        self._rects: List[Tuple[int, int, int, int]] = []
        self.border_color = border_color
        self.border_width = border_width
        # Internal measurement cache: (text, size_px) -> (w,h)
        self._measure_cache: Dict[Tuple[str, int], Tuple[int, int]] = {}

        # Install internal measurement function if none supplied.
        if self.measure_fn is None:
            self.measure_fn = self._measure_text_internal

    # Layout --------------------------------------------------------------
    def layout(self) -> None:
        """Compute button rectangles."""
        w, h = self.size
        # Labels and counts (needed for measurements below)
        labels = list(self.actions.keys())
        n = len(labels)

        # Use explicit bar height if provided, else derive from measured text
        # height using the requested font size and the vertical padding.
        if self.bar_height is not None:
            bar_h = self.bar_height
        else:
            # Measure text heights for all labels at the requested font size
            measure = self.measure_fn
            max_text_h = 0
            for lbl in labels:
                try:
                    _w, th = measure(lbl, int(self._requested_font_px))
                except Exception:
                    th = int(self._requested_font_px)
                if th > max_text_h:
                    max_text_h = th
            # Fallback to requested font_px if measurement failed
            if max_text_h <= 0:
                max_text_h = int(self._requested_font_px)
            bar_h = max(1, int(max_text_h + 2 * self.pad_y))
        y = h - bar_h
        # Respect requested font size exactly (no autoscaling)
        self.font_px = max(1, int(self._requested_font_px))

        # Initial equal division
        btn_w = w // max(1, n)
        widths = [btn_w] * n

        # Auto-expand center button (if odd number of buttons) to fit its label
        # Measure using the requested font size so layout matches drawing.
        if n >= 3 and n % 2 == 1:
            center_idx = n // 2
            center_label = labels[center_idx]
            try:
                center_w, _ = self.measure_fn(center_label, self.font_px)
            except Exception:
                center_w = int(self.font_px * 0.6) * len(center_label)
            center_needed = max(1, center_w + 2 * self.pad_x)
            if center_needed > btn_w:
                extra = center_needed - btn_w
                # Available pixels to steal: total width minus 1 px per other button
                available = max(0, w - btn_w - (n - 1) * 1)
                take = min(extra, available)
                if take > 0:
                    per = take // (n - 1)
                    rem = take - per * (n - 1)
                    for i in range(n):
                        if i == center_idx:
                            continue
                        dec = per + (1 if rem > 0 else 0)
                        if rem > 0:
                            rem -= 1
                        widths[i] = max(1, widths[i] - dec)
                    widths[center_idx] = w - sum(
                        widths[i] for i in range(n) if i != center_idx
                    )

        # Build rects from final widths
        rects: List[Tuple[int, int, int, int]] = []
        x = 0
        for i in range(n):
            rects.append((x, y, widths[i], bar_h))
            x += widths[i]
        self._rects = rects

    # Drawing -------------------------------------------------------------
    def draw(self, canvas: Canvas) -> None:
        if not self._rects:
            self.layout()
        for (x, y, w, h), label in zip(self._rects, self.actions.keys()):
            # Button background fill (cheap vertical scanline fill).
            for dy in range(h):
                canvas.line((x, y + dy), (x + w - 1, y + dy), color=_COLOR_BG)
            # border
            canvas.line(
                (x, y),
                (x + w - 1, y),
                width=self.border_width,
                color=self.border_color,
            )  # top
            canvas.line(
                (x, y + h - 1),
                (x + w - 1, y + h - 1),
                width=self.border_width,
                color=self.border_color,
            )  # bottom
            canvas.line(
                (x, y),
                (x, y + h - 1),
                width=self.border_width,
                color=self.border_color,
            )  # left
            canvas.line(
                (x + w - 1, y),
                (x + w - 1, y + h - 1),
                width=self.border_width,
                color=self.border_color,
            )  # right

            # Center the label using measurement (pygame-backed if available)
            try:
                text_w, text_h = self.measure_fn(label, self.font_px)
            except Exception:
                text_w = int(self.font_px * 0.6) * len(label)
                text_h = self.font_px
            # Constrain center within padding bounds.
            inner_left = x + self.pad_x
            inner_right = x + w - self.pad_x
            avail_w = max(1, inner_right - inner_left)
            text_x = inner_left + max(0, (avail_w - text_w) // 2)
            # Use measured height for vertical centering (better than font_px)
            text_y = y + (h - text_h) // 2
            canvas.text(
                (text_x, text_y),
                label,
                size_px=self.font_px,
                color=_COLOR_TEXT,
            )

    # Measurement --------------------------------------------------------
    def _measure_text_internal(self, text: str, size_px: int) -> Tuple[int, int]:
        """Measure text using pygame if available; fallback to approximation.

        Caches results for performance. If pygame isn't present (e.g. in a
        minimal web backend) we approximate width with 0.6 * size_px * len.
        """
        key = (text, size_px)
        cached = self._measure_cache.get(key)
        if cached is not None:
            return cached
        w_h: Tuple[int, int]
        try:  # Attempt pygame measurement
            import pygame as _pg

            if not _pg.get_init():  # Defensive init (idempotent)
                _pg.init()
            if not _pg.font.get_init():
                _pg.font.init()
            font = _pg.font.Font(None, size_px)
            w_h = font.size(text)
        except Exception:
            w_h = (int(size_px * 0.6) * len(text), size_px)
        self._measure_cache[key] = w_h
        return w_h

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
            "s": "Settings",
        }
        label = mapping.get(key)
        if label:
            cb = self.actions.get(label)
            if cb:
                cb()
