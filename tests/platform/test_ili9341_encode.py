from __future__ import annotations

import types

import pytest
from PIL import Image


class _SpiMock:
    def __init__(self) -> None:
        self.writes: list[list[int]] = []
        self.max_speed_hz = 0
        self.mode = 0

    def open(self, bus: int, dev: int) -> None:
        self.bus = bus
        self.dev = dev

    def writebytes(self, data: list[int]) -> None:
        self.writes.append(list(data))


def _manual_rgb565(img: Image.Image) -> bytes:
    """Reference per-pixel big-endian RGB565 pack (the slow fallback)."""
    rgb = img.convert("RGB")
    raw = rgb.tobytes()
    out = bytearray(len(raw) // 3 * 2)
    oi = 0
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        val = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[oi] = (val >> 8) & 0xFF
        out[oi + 1] = val & 0xFF
        oi += 2
    return bytes(out)


def _make_backend(monkeypatch: pytest.MonkeyPatch, w: int, h: int):
    from pocketscope.platform.display import ili9341_backend as mod

    monkeypatch.setattr(mod, "spidev", types.SimpleNamespace(SpiDev=_SpiMock))
    monkeypatch.setattr(mod, "GPIO", None)
    return mod.ILI9341DisplayBackend(width=w, height=h, enable_watchdog=False)


def test_numpy_fast_path_matches_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    w, h = 16, 12
    backend = _make_backend(monkeypatch, w, h)

    # A gradient so every channel exercises distinct bit patterns.
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 17) % 256, (y * 23) % 256, (x * y) % 256, 255)

    out = bytes(backend._encode_rgb565(img))
    assert backend._last_fast_used is True, "numpy fast path should be used"
    assert out == _manual_rgb565(img)


def test_numpy_fast_path_falls_back_without_numpy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pocketscope.platform.display import ili9341_backend as mod

    w, h = 8, 8
    backend = _make_backend(monkeypatch, w, h)
    # Simulate numpy being unavailable -> manual loop must still be correct.
    monkeypatch.setattr(mod, "_np", None)

    img = Image.new("RGBA", (w, h), (200, 100, 50, 255))
    out = bytes(backend._encode_rgb565(img))
    assert backend._last_fast_used is False
    assert out == _manual_rgb565(img)
