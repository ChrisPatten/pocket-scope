"""Framework-agnostic Canvas and DisplayBackend protocols.

Defines minimal drawing primitives and a display backend contract so
different frameworks (pygame, pillow, etc.) can be plugged in.
"""

from __future__ import annotations

from typing import Protocol, Sequence, Tuple

Color = Tuple[int, int, int, int]


class Canvas(Protocol):
    def clear(self, color: Color) -> None:
        ...

    def line(
        self,
        p0: Tuple[int, int],
        p1: Tuple[int, int],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        ...

    def circle(
        self,
        center: Tuple[int, int],
        radius: int,
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        ...

    def filled_circle(self, center: Tuple[int, int], radius: int, color: Color) -> None:
        ...

    def polyline(
        self,
        pts: Sequence[Tuple[int, int]],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        ...

    def text(
        self,
        pos: Tuple[int, int],
        s: str,
        size_px: int = 12,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        ...


class DisplayBackend(Protocol):
    def size(self) -> Tuple[int, int]:
        ...

    def begin_frame(self) -> Canvas:
        ...

    def end_frame(self) -> None:
        ...

    def save_png(self, path: str) -> None:
        ...
