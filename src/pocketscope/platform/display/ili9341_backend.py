"""ILI9341 SPI TFT display backend (RGB565 portrait 240x320).

Minimal driver using spidev + RPi.GPIO. Accepts drawing commands via a
Pillow-backed Canvas implementation and transmits packed RGB565 over SPI.
Safe to import without hardware; unit tests patch spidev/GPIO symbols.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence, Tuple, runtime_checkable

from PIL import Image, ImageDraw, ImageFont

from pocketscope.render.canvas import Canvas, Color, DisplayBackend

try:  # pragma: no cover
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

    def writebytes(self, data: list[int]) -> Any:
        ...


@dataclass(slots=True)
class _FontCache:
    fonts: dict[int, Any]

    def __init__(self) -> None:
        self.fonts = {}

    def get(self, size_px: int) -> Any:
        f = self.fonts.get(size_px)
        if f is None:
            try:
                # Try common scalable monospace TTFs first. These paths cover
                # Linux and macOS typical installs; fall back to a name-based
                # attempt and finally the Pillow default bitmap font.
                candidates = [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
                    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
                    "/Library/Fonts/Menlo.ttc",
                    "/Library/Fonts/Consolas.ttf",
                ]
                font = None
                for p in candidates:
                    try:
                        font = ImageFont.truetype(p, size_px)
                        break
                    except Exception:
                        continue
                if font is None:
                    # Try a generic family name (may work on some platforms)
                    try:
                        font = ImageFont.truetype("DejaVuSansMono.ttf", size_px)
                    except Exception:
                        font = ImageFont.load_default()
                f = font
            except Exception:
                f = ImageFont.load_default()
            self.fonts[size_px] = f
        return f


class _PillowCanvas(Canvas):
    def __init__(self, img: Image.Image, fonts: _FontCache) -> None:
        self._img = img
        self._draw = ImageDraw.Draw(img)
        self._fonts = fonts

    def clear(self, color: Color) -> None:
        r, g, b, a = color
        self._draw.rectangle([(0, 0), self._img.size], fill=(r, g, b, a))

    def line(
        self,
        p0: Tuple[int, int],
        p1: Tuple[int, int],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        self._draw.line([p0, p1], fill=color, width=width)

    def circle(
        self,
        center: Tuple[int, int],
        radius: int,
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        x, y = center
        bbox = [x - radius, y - radius, x + radius, y + radius]
        self._draw.ellipse(bbox, outline=color, width=max(1, width))

    def filled_circle(self, center: Tuple[int, int], radius: int, color: Color) -> None:
        x, y = center
        bbox = [x - radius, y - radius, x + radius, y + radius]
        self._draw.ellipse(bbox, fill=color)

    def polyline(
        self,
        pts: Sequence[Tuple[int, int]],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        if pts:
            self._draw.line(list(pts), fill=color, width=width)

    def text(
        self,
        pos: Tuple[int, int],
        s: str,
        size_px: int = 12,
        color: Color = (255, 255, 255, 255),
    ) -> None:
        font = self._fonts.get(size_px)
        self._draw.text(pos, s, fill=color, font=font)


class ILI9341DisplayBackend(DisplayBackend):
    def __init__(
        self,
        width: int = 240,
        height: int = 320,
        spi_bus: int = 0,
        spi_dev: int = 0,
        dc_pin: int = 25,
        rst_pin: int = 24,
        led_pin: int = 18,
        hz: int = 32_000_000,
    ) -> None:
        self._w = int(width)
        self._h = int(height)
        self._dc = dc_pin
        self._rst = rst_pin
        self._led = led_pin
        self._hz = hz
        self._spi: _SpiLike | None = None
        self._frame: Image.Image | None = None
        self._fonts = _FontCache()
        self._init_gpio()
        self._init_spi(spi_bus, spi_dev)
        self._init_panel()

    def _init_gpio(self) -> None:
        if GPIO is None:  # pragma: no cover
            return
        # Disable RPi.GPIO warnings about channels "already in use" when
        # reinitializing GPIO (common when restarting services or running
        # multiple processes). The warning is harmless; use this to keep
        # logs clean. See RuntimeWarning message suggesting this API.
        try:
            GPIO.setwarnings(False)
        except Exception:
            # Be defensive: some test/mocks may not implement setwarnings.
            pass
        GPIO.setmode(GPIO.BCM)
        for p in (self._dc, self._rst, self._led):
            GPIO.setup(p, GPIO.OUT)
        GPIO.output(self._led, 1)

    def _init_spi(self, bus: int, dev: int) -> None:
        if spidev is None:  # pragma: no cover
            return
        spi = spidev.SpiDev()
        spi.open(bus, dev)
        spi.max_speed_hz = self._hz
        spi.mode = 0
        self._spi = spi

    def _hw_reset(self) -> None:
        if GPIO is None:  # pragma: no cover
            return
        GPIO.output(self._rst, 0)
        time.sleep(0.02)
        GPIO.output(self._rst, 1)
        time.sleep(0.15)

    def _write_cmd(self, cmd: int, data: bytes | None = None) -> None:
        if self._spi is None:
            return
        if GPIO is not None:
            GPIO.output(self._dc, 0)
        self._spi.writebytes([cmd & 0xFF])
        if data:
            if GPIO is not None:
                GPIO.output(self._dc, 1)
            self._spi.writebytes(list(data))

    def _init_panel(self) -> None:
        self._hw_reset()
        seq: list[tuple[int, bytes | None]] = [
            (0x01, None),
            (0x28, None),
            (0xCF, b"\x00\x83\x30"),
            (0xED, b"\x64\x03\x12\x81"),
            (0xE8, b"\x85\x01\x79"),
            (0xCB, b"\x39\x2c\x00\x34\x02"),
            (0xF7, b"\x20"),
            (0xEA, b"\x00\x00"),
            (0xC0, b"\x26"),
            (0xC1, b"\x11"),
            (0xC5, b"\x35\x3e"),
            (0xC7, b"\xbe"),
            (0x36, b"\x48"),
            (0x3A, b"\x55"),
            (0xB1, b"\x00\x1b"),
            (0xB6, b"\x0a\x82\x27\x00"),
            (0xF2, b"\x00"),
            (0x26, b"\x01"),
            (0xE0, b"\x0f\x31\x2b\x0c\x0e\x08\x4e\xf1\x37\x07\x10\x03\x0e\x09\x00"),
            (0xE1, b"\x00\x0e\x14\x03\x11\x07\x31\xc1\x48\x08\x0f\x0c\x31\x36\x0f"),
            (0x11, None),
        ]
        for c, d in seq:
            self._write_cmd(c, d)
        time.sleep(0.12)
        self._write_cmd(0x29, None)

    def size(self) -> Tuple[int, int]:
        return (self._w, self._h)

    def begin_frame(self) -> Canvas:
        self._frame = Image.new("RGBA", (self._w, self._h), (0, 0, 0, 255))
        return _PillowCanvas(self._frame, self._fonts)

    def _set_addr_window(self) -> None:
        self._write_cmd(0x2A, bytes([0x00, 0x00, 0x00, (self._w - 1) & 0xFF]))
        self._write_cmd(
            0x2B, bytes([0x00, 0x00, (self._h - 1) >> 8, (self._h - 1) & 0xFF])
        )
        self._write_cmd(0x2C, None)

    def end_frame(self) -> None:
        if self._frame is None:
            return
        rgb = self._frame.convert("RGB")
        raw = rgb.tobytes()
        out = bytearray(2 * self._w * self._h)
        oi = 0
        for i in range(0, len(raw), 3):
            r = raw[i] & 0xF8
            g = raw[i + 1] & 0xFC
            b = raw[i + 2] & 0xF8
            val = (r << 8) | (g << 3) | (b >> 3)
            out[oi] = (val >> 8) & 0xFF
            out[oi + 1] = val & 0xFF
            oi += 2
        self._set_addr_window()
        if GPIO is not None:
            GPIO.output(self._dc, 1)
        if self._spi is not None:
            # Some SPI drivers / kernel builds have limits on a single write
            # argument size (observed 'Argument list size exceeds 4096 bytes').
            # Chunk large frame transfers to stay well under that threshold.
            CHUNK = 2048  # bytes per transfer (tunable)
            total = len(out)
            mv = memoryview(out)
            for i in range(0, total, CHUNK):
                # Convert only the slice needed for this transfer to a list[int]
                self._spi.writebytes(list(mv[i : i + CHUNK]))

    def save_png(self, path: str) -> None:
        if self._frame is None:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._frame.save(path)


__all__ = ["ILI9341DisplayBackend"]
