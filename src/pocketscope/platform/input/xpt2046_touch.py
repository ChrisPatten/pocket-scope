"""XPT2046 SPI touch driver (async polling, linear calibration).

Wiring (shared SPI0 with display): VCC, GND, SCK, MOSI, MISO, CS=CE1, PENIRQ.
Uses commands 0x90 (Y) then 0xD0 (X). Median-of-3 filtering then linear
scaling to screen coordinates.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import List, Protocol, Tuple, runtime_checkable

from pocketscope.platform.input.pygame_input import UiEvent

try:  # pragma: no cover - optional hardware
    import spidev
except Exception:  # pragma: no cover
    spidev = None
try:  # pragma: no cover
    import RPi.GPIO as GPIO
except Exception:  # pragma: no cover
    GPIO = None


@runtime_checkable
class _SpiLike(Protocol):  # pragma: no cover - typing only
    max_speed_hz: int
    mode: int

    def open(self, bus: int, dev: int) -> None:
        ...

    def xfer2(self, data: list[int]) -> list[int]:
        ...


@dataclass(slots=True)
class _Cal:
    x_min: int = 300
    x_max: int = 3700
    y_min: int = 300
    y_max: int = 3700


class XPT2046Touch:
    def __init__(
        self,
        spi_bus: int = 0,
        spi_dev: int = 1,
        irq_pin: int = 22,
        width: int = 240,
        height: int = 320,
        poll_hz: float = 60.0,
    ) -> None:
        self._irq = irq_pin
        self._w = int(width)
        self._h = int(height)
        # Allow requested poll_hz to be > 1.0 (e.g. 60 Hz). Only guard
        # against zero/negative to avoid division-by-zero.
        self._poll_dt = 1.0 / max(1e-6, float(poll_hz))
        self._spi: _SpiLike | None = None
        self._cal = _Cal()
        self._events: List[UiEvent] = []
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._init_gpio()
        self._init_spi(spi_bus, spi_dev)

    def _init_gpio(self) -> None:
        if GPIO is None:  # pragma: no cover
            return
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self._irq, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    def _init_spi(self, bus: int, dev: int) -> None:
        if spidev is None:  # pragma: no cover
            return
        spi = spidev.SpiDev()
        spi.open(bus, dev)
        spi.max_speed_hz = 2_000_000
        spi.mode = 0
        self._spi = spi

    def _read_raw_axis(self, cmd: int) -> int:
        if self._spi is None:  # pragma: no cover
            return 0
        r = self._spi.xfer2([cmd, 0x00, 0x00])
        val = ((r[1] << 8) | r[2]) >> 3
        return int(val & 0x0FFF)

    def _touch_active(self) -> bool:
        if GPIO is None:  # pragma: no cover
            return False
        return bool(GPIO.input(self._irq) == 0)  # active low

    def _sample(self) -> Tuple[int, int] | None:
        if not self._touch_active():
            return None
        xs: list[int] = []
        ys: list[int] = []
        for _ in range(3):
            y_raw = self._read_raw_axis(0x90)
            x_raw = self._read_raw_axis(0xD0)
            xs.append(x_raw)
            ys.append(y_raw)
        xs.sort()
        ys.sort()
        x = xs[1]
        y = ys[1]
        sx = int(
            (x - self._cal.x_min)
            * (self._w - 1)
            / max(1, self._cal.x_max - self._cal.x_min)
        )
        sy = int(
            (y - self._cal.y_min)
            * (self._h - 1)
            / max(1, self._cal.y_max - self._cal.y_min)
        )
        sx = max(0, min(self._w - 1, sx))
        sy = max(0, min(self._h - 1, sy))
        return (sx, sy)

    async def run(self) -> None:
        self._running = True
        prev = False
        last: Tuple[int, int] | None = None
        while self._running:
            now = time.time()
            pt = self._sample()
            if pt is not None:
                if not prev:
                    self._events.append(UiEvent("down", pt[0], pt[1], now))
                    prev = True
                else:
                    if last is not None and pt != last:
                        self._events.append(UiEvent("drag", pt[0], pt[1], now))
                last = pt
            else:
                if prev and last is not None:
                    self._events.append(UiEvent("tap", last[0], last[1], now))
                prev = False
                last = None
            await asyncio.sleep(self._poll_dt)

    def get_events(self) -> list[UiEvent]:
        out = list(self._events)
        self._events.clear()
        return out

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()


__all__ = ["XPT2046Touch"]
