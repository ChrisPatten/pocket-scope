from __future__ import annotations

import types


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

    def __init__(self) -> None:
        self.levels: dict[int, int] = {}

    def setmode(self, mode: int) -> None:  # pragma: no cover
        pass

    def setup(self, pin: int, mode: int) -> None:
        self.levels[pin] = 1

    def output(self, pin: int, val: int) -> None:
        self.levels[pin] = val


def test_frame_write_chunking(monkeypatch) -> None:
    """Ensure large frame buffers are split into multiple SPI writes.

    Uses an over-threshold pixel count so RGB565 bytes exceed the chunk size.
    """
    from pocketscope.platform.display import ili9341_backend as mod

    mod.spidev = types.SimpleNamespace(SpiDev=_SpiMock)  # type: ignore
    mod.GPIO = _GpioMock()  # type: ignore

    # Width*Height*2 bytes > 2048 (chunk size) -> 60*40*2 = 4800 bytes
    backend = mod.ILI9341DisplayBackend(width=60, height=40)

    canvas = backend.begin_frame()
    canvas.clear((0, 0, 0, 255))
    backend.end_frame()

    spi = backend._spi  # type: ignore[attr-defined]
    assert spi is not None
    # Identify frame pixel data writes: these occur after the address window commands
    # Heuristic: the last consecutive series of large writes represents pixel data.
    large_writes = [w for w in spi.writes if len(w) > 32]
    assert large_writes, "Expected large pixel data writes"
    # Ensure chunking occurred
    assert (
        len(large_writes) > 1
    ), "Frame buffer should be split into multiple SPI writes"
    pixel_bytes = sum(len(w) for w in large_writes)
    expected = 60 * 40 * 2
    # Some command bytes may have been included if heuristic over-approximates;
    # allow small overhead
    assert expected <= pixel_bytes <= expected + 64
