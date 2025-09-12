from __future__ import annotations

import types

import pytest


class _SpiMock:
    def __init__(self, seq: list[int]) -> None:
        self.seq = seq
        self.idx = 0

    def open(self, bus: int, dev: int) -> None:
        pass

    def xfer2(self, data):  # type: ignore[no-untyped-def]
        # Return 3 bytes; craft 12-bit value from sequence
        val = self.seq[self.idx % len(self.seq)]
        self.idx += 1
        # top 12 bits across high(8) low(4)
        return [data[0], (val >> 5) & 0xFF, (val & 0x1F) << 3]


class _GpioMock:
    BCM = 0
    OUT = 1
    IN = 2
    PUD_UP = 3

    def __init__(self) -> None:
        # Start inactive (high). Active low: 0 means pressed, 1 means not pressed.
        self._irq_level = 1  # inactive

    def setmode(self, mode: int) -> None:  # noqa: D401 - simple mock
        pass

    def setup(
        self, pin: int, mode: int, pull_up_down: int | None = None
    ) -> None:  # noqa: D401
        pass

    def input(self, pin: int) -> int:
        return self._irq_level


@pytest.mark.asyncio
async def test_touch_calibration(monkeypatch: pytest.MonkeyPatch):
    from pocketscope.platform.input import xpt2046_touch as mod

    mod.spidev = types.SimpleNamespace(
        SpiDev=lambda: _SpiMock([500, 2000, 3500])
    )  # type: ignore
    g = _GpioMock()
    mod.GPIO = g  # type: ignore
    t = mod.XPT2046Touch(width=240, height=320)
    # Run a single polling iteration by invoking internal sample logic
    # Force three median samples; emulate run loop manually
    pt = t._sample()
    assert pt is None  # since touch inactive (GPIO high)
    # Activate touch
    g._irq_level = 0
    pt2 = t._sample()
    assert pt2 is not None
    x, y = pt2
    assert 0 <= x < 240
    assert 0 <= y < 320
