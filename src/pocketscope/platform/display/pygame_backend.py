"""Pygame-based DisplayBackend with headless (offscreen) support.

This module implements a minimal Canvas and DisplayBackend using pygame.
It's suitable for deterministic, headless tests by setting the environment
variable SDL_VIDEODRIVER=dummy before importing pygame.

Example:
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from pocketscope.platform.display.pygame_backend import PygameDisplayBackend

    backend = PygameDisplayBackend(size=(320, 480))
    canvas = backend.begin_frame()
    canvas.clear((0, 0, 0, 255))
    canvas.line((10, 10), (310, 10), width=2, color=(255, 255, 0, 255))
    backend.end_frame()
    backend.save_png("/tmp/frame.png")
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple

from pocketscope.render.canvas import Canvas, Color, DisplayBackend

pg: Any = None
try:  # pragma: no cover - import guard for environments without SDL
    import pygame as _pg

    pg = _pg
except Exception:  # pragma: no cover
    pg = None


def _pygame_color(c: Color) -> Tuple[int, int, int, int]:
    r, g, b, a = c
    return int(r), int(g), int(b), int(a)


@dataclass(slots=True)
class _FontCache:
    fonts: Dict[int, Any]

    def __init__(self) -> None:
        self.fonts = {}

    def get(self, size_px: int) -> Any:
        f = self.fonts.get(size_px)
        if f is None:
            # Default system font for determinism across platforms
            local_pg = pg
            if (
                local_pg is None
            ):  # Defensive: should never happen if backend constructed
                raise RuntimeError("pygame is not available")
            f = local_pg.font.Font(None, size_px)
            self.fonts[size_px] = f
        return f


class _PygameCanvas(Canvas):
    def __init__(self, surface: Any, font_cache: _FontCache) -> None:
        self._surface = surface
        self._font_cache = font_cache

    def clear(self, color: Color) -> None:
        self._surface.fill(_pygame_color(color))

    def line(
        self,
        p0: Tuple[int, int],
        p1: Tuple[int, int],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        pg.draw.line(self._surface, _pygame_color(color), p0, p1, width)

    def circle(
        self,
        center: Tuple[int, int],
        radius: int,
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        pg.draw.circle(self._surface, _pygame_color(color), center, radius, width)

    def filled_circle(self, center: Tuple[int, int], radius: int, color: Color) -> None:
        pg.draw.circle(self._surface, _pygame_color(color), center, radius, 0)

    def polyline(
        self,
        pts: Sequence[Tuple[int, int]],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        if not pts:
            return
        if len(pts) == 1:
            # Draw a dot for a single point
            pg.draw.circle(
                self._surface, _pygame_color(color), pts[0], max(1, width // 2), 0
            )
            return
        pg.draw.lines(self._surface, _pygame_color(color), False, list(pts), width)

    def text(
        self,
        pos: Tuple[int, int],
        s: str,
        size_px: int = 12,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        font = self._font_cache.get(size_px)
        # Antialiased rendering for consistent appearance
        surf = font.render(s, True, _pygame_color(color))
        self._surface.blit(surf, pos)

    def text_size(self, s: str, size_px: int = 12) -> Tuple[int, int]:
        font = self._font_cache.get(size_px)
        w, h = font.size(s)
        return int(w), int(h)


class PygameDisplayBackend(DisplayBackend):
    """Pygame implementation of DisplayBackend with offscreen surface.

    Automatically initializes pygame with an offscreen display if the
    environment variable SDL_VIDEODRIVER is set to "dummy". Otherwise, a
    regular window may be created depending on the platform.
    """

    def __init__(
        self, size: Tuple[int, int] = (320, 480), *, create_window: bool = False
    ) -> None:
        local_pg = pg
        if local_pg is None:
            raise RuntimeError(
                "pygame is not available. "
                "Ensure it is installed and that SDL is configured."
            )

        # Ensure headless if requested
        if os.environ.get("SDL_VIDEODRIVER") == "dummy":
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

        # Initialize pygame
        if not local_pg.get_init():
            local_pg.init()
        if not local_pg.font.get_init():
            local_pg.font.init()

        self._width, self._height = int(size[0]), int(size[1])
        self._window_surface = None
        if create_window and os.environ.get("SDL_VIDEODRIVER") != "dummy":
            try:
                self._window_surface = local_pg.display.set_mode(
                    (self._width, self._height)
                )
            except Exception:
                # Fallback to offscreen and provide a hint for diagnostics
                print(
                    "[PygameDisplayBackend] Window creation failed; "
                    "falling back to offscreen. Check SDL_VIDEODRIVER and "
                    "display permissions."
                )
                self._window_surface = None

        # Create offscreen surface; use SRCALPHA for per-pixel alpha
        # We avoid creating a display window to be headless/deterministic
        self._surface = local_pg.Surface(
            (self._width, self._height), flags=local_pg.SRCALPHA
        )
        self._font_cache = _FontCache()

    def size(self) -> Tuple[int, int]:
        return (self._width, self._height)

    def begin_frame(self) -> Canvas:
        # Simply return a canvas wrapping the offscreen surface
        return _PygameCanvas(self._surface, self._font_cache)

    def end_frame(self) -> None:
        # If we have a window, blit the offscreen buffer and flip
        local_pg = pg
        if self._window_surface is not None and local_pg is not None:
            self._window_surface.blit(self._surface, (0, 0))
            local_pg.display.flip()
        return None

    def save_png(self, path: str) -> None:
        # Save current offscreen surface to PNG
        local_pg = pg
        if local_pg is None:  # pragma: no cover - should not happen at runtime
            raise RuntimeError("pygame is not available")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        local_pg.image.save(self._surface, path)
