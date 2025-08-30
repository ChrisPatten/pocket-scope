"""
Framework-agnostic Canvas and DisplayBackend protocols.

This module defines minimal drawing primitives and a display backend
contract so we can plug different frameworks (pygame, pillow, etc.).

Example usage:

	from pocketscope.render.canvas import Canvas, DisplayBackend

	def render_frame(backend: DisplayBackend) -> None:
		w, h = backend.size()
		canvas = backend.begin_frame()
		# Clear background
		canvas.clear((0, 0, 0, 255))
		# Draw a line
		canvas.line((10, 10), (w - 10, h - 10), width=2, color=(255, 255, 0, 255))
		# Draw text
		canvas.text((12, 24), "Hello", size_px=14, color=(255, 255, 255, 255))
		backend.end_frame()
		backend.save_png("/tmp/frame.png")

Notes
-----
All colors are RGBA tuples with 0..255 channels. Coordinates are pixel
coordinates with origin at top-left, x growing to the right, y growing down.
"""

from __future__ import annotations

from typing import Protocol, Sequence, Tuple

Color = Tuple[int, int, int, int]


class Canvas(Protocol):
    """Immediate-mode drawing surface for a single frame.

    Implementations draw into an offscreen buffer owned by the associated
    DisplayBackend. Instances should not be retained across frames.
    """

    def clear(self, color: Color) -> None:
        """Fill the entire surface with the given color.

        Parameters
        ----------
        color: RGBA color (0..255 per channel).
        """

    ...

    def line(
        self,
        p0: Tuple[int, int],
        p1: Tuple[int, int],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        """Draw a line segment between two points."""

    ...

    def circle(
        self,
        center: Tuple[int, int],
        radius: int,
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        """Draw a circle outline."""

    ...

    def filled_circle(self, center: Tuple[int, int], radius: int, color: Color) -> None:
        """Draw a filled circle."""

    ...

    def polyline(
        self,
        pts: Sequence[Tuple[int, int]],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        """Draw a connected polyline through a sequence of points."""

    ...

    def text(
        self,
        pos: Tuple[int, int],
        s: str,
        size_px: int = 12,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        """Draw a text string with top-left anchored at pos."""

    ...


class DisplayBackend(Protocol):
    """Display backend responsible for frame lifecycle and capture.

    Typical usage:

            backend = SomeDisplayBackend((320, 480))
            canvas = backend.begin_frame()
            canvas.clear((0,0,0,255))
            # draw...
            backend.end_frame()
            backend.save_png("/tmp/frame.png")
    """

    def size(self) -> Tuple[int, int]:
        """Return the (width, height) of the drawing surface in pixels."""

    ...

    def begin_frame(self) -> Canvas:
        """Start a new frame and return a Canvas to draw into."""

    ...

    def end_frame(self) -> None:
        """Finish the current frame (no-op for purely offscreen backends)."""

    ...

    def save_png(self, path: str) -> None:
        """Save the current frame buffer to a PNG file at path."""

    ...
