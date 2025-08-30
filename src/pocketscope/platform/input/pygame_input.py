"""Pygame InputBackend for mouse-to-tap event synthesis.

This lightweight module demonstrates mapping of pygame mouse events to
simple UI events suitable for touch-like interactions in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generator

pg: Any = None
try:  # pragma: no cover - optional dependency in CI
    import pygame as _pg

    pg = _pg
except Exception:  # pragma: no cover
    pg = None


@dataclass(slots=True)
class UiEvent:
    type: str  # "tap" | "long_press" | "down" | "up"
    x: int
    y: int
    ts: float


class PygameInputBackend:
    """Collects pygame events and emits UiEvent taps from mouse clicks.

    Use pump() in a loop to process events. In headless mode (dummy video),
    pygame may not deliver events; tests can synthesize by posting events.
    """

    def __init__(self) -> None:
        if pg is None:
            raise RuntimeError("pygame not available for input backend")

    def pump(self) -> Generator[UiEvent, None, None]:
        if pg is None:
            if False:
                yield UiEvent("down", 0, 0, 0.0)  # unreachable, keeps generator type
            return
        for ev in pg.event.get():
            if ev.type == pg.MOUSEBUTTONDOWN:
                yield UiEvent(
                    "down",
                    int(ev.pos[0]),
                    int(ev.pos[1]),
                    float(pg.time.get_ticks()) / 1000.0,
                )
            elif ev.type == pg.MOUSEBUTTONUP:
                yield UiEvent(
                    "tap",
                    int(ev.pos[0]),
                    int(ev.pos[1]),
                    float(pg.time.get_ticks()) / 1000.0,
                )
