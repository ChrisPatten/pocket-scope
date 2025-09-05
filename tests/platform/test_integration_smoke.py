from __future__ import annotations

import asyncio
import types

import pytest


class _SpiMock:
    def __init__(self) -> None:
        self.calls: int = 0

    def open(self, bus: int, dev: int) -> None:  # pragma: no cover
        pass

    def writebytes(self, data: list[int]) -> None:  # pragma: no cover
        self.calls += 1

    def xfer2(self, data: list[int]):  # pragma: no cover
        # Return a moving point raw value mid-calibration
        return [data[0], 0x7F, 0xF0]


class _GpioMock:
    BCM = 0
    OUT = 1
    IN = 2
    PUD_UP = 3

    def __init__(self) -> None:
        self.level = 0  # active (pressed)

    def setmode(self, mode: int) -> None:  # pragma: no cover
        pass

    def setup(
        self, pin: int, mode: int, pull_up_down: int | None = None
    ) -> None:  # pragma: no cover
        pass

    def input(self, pin: int) -> int:
        return self.level

    def output(self, pin: int, val: int) -> None:  # pragma: no cover
        pass


@pytest.mark.asyncio
async def test_integration_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    import pocketscope.platform.display.ili9341_backend as dmod
    import pocketscope.platform.input.xpt2046_touch as imod

    dmod.spidev = types.SimpleNamespace(SpiDev=_SpiMock)  # type: ignore
    dmod.GPIO = _GpioMock()  # type: ignore
    imod.spidev = types.SimpleNamespace(SpiDev=_SpiMock)  # type: ignore
    gpio_touch = _GpioMock()
    imod.GPIO = gpio_touch  # type: ignore
    disp = dmod.ILI9341DisplayBackend()
    canvas = disp.begin_frame()
    canvas.clear((0, 0, 0, 255))
    canvas.line((0, 0), (10, 10), color=(255, 0, 0, 255))
    disp.end_frame()
    touch = imod.XPT2046Touch(poll_hz=200.0)
    # Run briefly to gather events
    asyncio.create_task(touch.run())  # run in background
    await asyncio.sleep(0.02)
    touch.stop()
    await asyncio.sleep(0)  # allow cancel
    events = [e for e in touch.get_events() if e.type in {"down", "drag", "tap"}]
    # At least one down (and maybe tap if release occurred)
    assert any(e.type == "down" for e in events)
