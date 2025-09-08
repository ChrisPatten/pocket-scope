"""ILI9341 SPI TFT display backend (RGB565 portrait 240x320) with recovery.

Adds prototype resilience:
    * Automatic re-init on SPI write failures.
    * Periodic status (0x09) read via MISO; failure triggers recovery.
    * Watchdog thread re-inits if no successful frame for >2 s.
    * Shared SPI bus lock with touch controller to avoid interleaved writes.

All extras become inert when ``spidev`` / ``RPi.GPIO`` are unavailable.
"""

from __future__ import annotations

import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence, Tuple, runtime_checkable

from PIL import Image, ImageDraw, ImageFont

from pocketscope.render.canvas import Canvas, Color, DisplayBackend

try:  # pragma: no cover
    from .spi_lock import SPI_BUS_LOCK
except Exception:  # pragma: no cover
    SPI_BUS_LOCK = threading.RLock()

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
        w, h = self._img.size
        self._draw.rectangle((0, 0, w, h), fill=(r, g, b, a))

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
        # Geometry & config
        self._w = int(width)
        self._h = int(height)
        self._dc = dc_pin
        self._rst = rst_pin
        self._led = led_pin
        self._hz = hz
        self._spi: _SpiLike | None = None
        self._frame: Image.Image | None = None
        self._flip: bool = False
        self._fonts = _FontCache()

        # Health / recovery state
        self._fail_streak = 0
        self._last_ok = time.monotonic()
        self._last_status_crc: int | None = None
        self._frame_counter = 0
        self._ping_interval_frames = 30
        self._recover_lock = threading.RLock()
        self._next_backoff_s = 0.05
        self._max_backoff_s = 2.0
        self._watchdog_interval_s = 0.5
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop = threading.Event()

        self._init_gpio()
        self._init_spi(spi_bus, spi_dev)
        self._init_panel()
        self._start_watchdog()

    def _init_gpio(self) -> None:
        if GPIO is None:  # pragma: no cover
            return
        try:
            GPIO.setwarnings(False)
        except Exception:
            pass
        GPIO.setmode(GPIO.BCM)
        for p in (self._dc, self._rst, self._led):
            GPIO.setup(p, GPIO.OUT)
        GPIO.output(self._led, 1)

    def _init_spi(self, bus: int, dev: int) -> None:
        if spidev is None:  # pragma: no cover
            return
        spi = spidev.SpiDev()
        with SPI_BUS_LOCK:
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
        with SPI_BUS_LOCK:
            self._spi.writebytes([cmd & 0xFF])
        if data:
            if GPIO is not None:
                GPIO.output(self._dc, 1)
            with SPI_BUS_LOCK:
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
        self._last_ok = time.monotonic()
        self._fail_streak = 0
        self._next_backoff_s = 0.05

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
        # Respect optional flip/rotate setting by operating on a transient
        # copy so we do not permanently mutate the stored frame buffer.
        img = self._frame
        try:
            if self._flip:
                img = img.transpose(Image.ROTATE_180)
        except Exception:
            img = self._frame
        rgb = img.convert("RGB")
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
        try:
            self._set_addr_window()
            if GPIO is not None:
                GPIO.output(self._dc, 1)
            if self._spi is not None:
                CHUNK = 2048
                total = len(out)
                mv = memoryview(out)
                for i in range(0, total, CHUNK):
                    with SPI_BUS_LOCK:
                        self._spi.writebytes(list(mv[i : i + CHUNK]))
            self._frame_counter += 1
            # Periodic status ping
            if self._frame_counter % self._ping_interval_frames == 0:
                self._status_ping()
            self._last_ok = time.monotonic()
            self._fail_streak = 0
            self._next_backoff_s = 0.05
        except Exception:
            self._fail_streak += 1
            self._attempt_recover("frame transmit error")

    def save_png(self, path: str) -> None:
        if self._frame is None:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            if self._flip:
                img = self._frame.transpose(Image.ROTATE_180)
                img.save(path)
            else:
                self._frame.save(path)
        except Exception:
            try:
                self._frame.save(path)
            except Exception:
                pass

    def apply_flip(self, flip: bool) -> None:
        """Opt-in backend hook: set whether final output should be flipped.

        This method is intentionally lightweight and idempotent. The actual
        transformation is applied during frame finalization in :meth:`end_frame`.
        """
        try:
            new = bool(flip)
            if new != self._flip:
                try:
                    print(f"[ILI9341] apply_flip -> {new}")
                except Exception:
                    pass
            self._flip = new
        except Exception:
            self._flip = False

    # ------------------------------------------------------------------
    # Recovery / health monitoring
    # ------------------------------------------------------------------
    def _close_spi(self) -> None:
        with suppress(Exception):
            if self._spi is not None:
                with SPI_BUS_LOCK:
                    try:
                        close = getattr(self._spi, "close", None)
                        if callable(close):
                            close()
                    finally:
                        self._spi = None

    def reset_and_init(self) -> None:
        """Public re-initialisation entry point.

        Safe to call repeatedly; errors are suppressed to avoid cascading
        failures in tight loops.
        """
        with self._recover_lock:
            try:
                self._close_spi()
                # Re-init SPI + panel only if libraries present
                self._init_spi(0, 0)
                self._init_panel()
            except Exception:
                pass

    def _status_ping(self) -> None:
        """Attempt a lightweight status read (0x09) and CRC it.

        ILI9341's 0x09 returns 4 status bytes plus dummy. We only care that
        the transfer succeeds and returned bytes are not all 0x00/0xFF.
        """
        if self._spi is None:
            return
        try:
            # Enter command mode
            if GPIO is not None:
                GPIO.output(self._dc, 0)
            with SPI_BUS_LOCK:
                self._spi.writebytes([0x09])
            # Switch to data (read) mode
            if GPIO is not None:
                GPIO.output(self._dc, 1)
            # Use xfer2 to clock out bytes (1 dummy + 4 data typical)
            xfer2 = getattr(self._spi, "xfer2", None)
            if not callable(xfer2):  # Fallback: cannot read
                return
            with SPI_BUS_LOCK:
                resp = xfer2([0x00, 0x00, 0x00, 0x00, 0x00])
            data = resp[1:5]
            if not data:
                raise RuntimeError("empty status")
            if all(b == 0x00 for b in data) or all(b == 0xFF for b in data):
                raise RuntimeError("degenerate status bytes")
            crc = 0
            for b in data:
                crc = (crc ^ b) & 0xFF
            if self._last_status_crc is not None and crc == self._last_status_crc:
                # Stable CRC is acceptable; only absence/failure triggers.
                pass
            self._last_status_crc = crc
        except Exception:
            self._fail_streak += 1
            self._attempt_recover("status ping failure")

    def _attempt_recover(self, reason: str) -> None:
        now = time.monotonic()
        with self._recover_lock:
            # Simple backoff to avoid hammering SPI hardware if unplugged.
            delay = self._next_backoff_s
            self._next_backoff_s = min(self._max_backoff_s, self._next_backoff_s * 2.0)
            with suppress(Exception):
                print(
                    f"[ILI9341] recover (reason={reason}, streak={self._fail_streak},"
                    f" backoff={delay:.2f}s)"
                )
            time.sleep(delay)
            try:
                self.reset_and_init()
                self._last_ok = now
            except Exception:
                pass

    def recover_from_error(self, exc: Exception | str) -> None:  # used by UI
        self._fail_streak += 1
        self._attempt_recover(str(exc))

    def _watchdog_run(self) -> None:
        while not self._watchdog_stop.is_set():
            try:
                if (time.monotonic() - self._last_ok) > 2.0:
                    self._attempt_recover("watchdog timeout")
            except Exception:
                pass
            self._watchdog_stop.wait(self._watchdog_interval_s)

    def _start_watchdog(self) -> None:
        if self._watchdog_thread is not None:
            return
        if spidev is None:  # skip if no hardware library
            return
        t = threading.Thread(
            target=self._watchdog_run, name="ili9341-watchdog", daemon=True
        )

        self._watchdog_thread = t
        try:
            t.start()
        except Exception:
            self._watchdog_thread = None

    def shutdown(self) -> None:
        self._watchdog_stop.set()
        with suppress(Exception):
            if self._watchdog_thread and self._watchdog_thread.is_alive():
                self._watchdog_thread.join(timeout=0.2)
        self._close_spi()


__all__ = ["ILI9341DisplayBackend"]
