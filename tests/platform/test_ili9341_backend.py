from __future__ import annotations

import types

import pytest


class _SpiMock:
    def __init__(self) -> None:
        self.writes: list[list[int]] = []
        self.max_speed_hz = 0
        self.mode = 0

    def open(self, bus: int, dev: int) -> None:  # pragma: no cover
        self.bus = bus
        self.dev = dev

    def writebytes(self, data: list[int]) -> None:
        self.writes.append(list(data))


class _GpioMock:
    BCM = 0
    OUT = 1
    IN = 2
    PUD_UP = 3

    def __init__(self) -> None:
        self.levels: dict[int, int] = {}

    def setmode(self, mode: int) -> None:  # pragma: no cover
        pass

    def setup(self, pin: int, mode: int, pull_up_down: int | None = None) -> None:
        self.levels[pin] = 1

    def output(self, pin: int, val: int) -> None:
        self.levels[pin] = val

    def input(self, pin: int) -> int:  # pragma: no cover
        return self.levels.get(pin, 1)


def test_init_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    from pocketscope.platform.display import ili9341_backend as mod

    mod.spidev = types.SimpleNamespace(SpiDev=_SpiMock)  # type: ignore
    mod.GPIO = _GpioMock()  # type: ignore
    backend = mod.ILI9341DisplayBackend()
    spi = backend._spi  # type: ignore[attr-defined]
    assert spi is not None
    # Filter command writes (single byte lists)
    cmds = [w[0] for w in spi.writes if len(w) == 1]
    expected_prefix = [0x01, 0x28, 0xCF, 0xED]
    assert cmds[:4] == expected_prefix
