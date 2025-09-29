"""ILI9341 SPI TFT backend (RGB565 portrait 240x320) with recovery & blink mitigation.

Public API (unchanged):
    * size()
    * begin_frame() -> Canvas
    * end_frame()
    * apply_flip(bool)
    * save_png(path)
    * shutdown()

Added heuristics (configurable via attributes):
    * _blank_skip_threshold (brightness drop factor, default 8.0)
    * _blank_skip_ops_threshold (ops <= treated as blank, default 5)
    * _frame_hold_ms (minimum interval for idle identical frame, default 0)

Blink mitigation strategy:
    * Count draw operations per frame. If almost no ops and resulting frame
        is near-black or a large brightness drop relative to last frame, skip
        pushing and retain previous panel contents (reduces visible flashing).
    * Optional frame hold: if no operations and last push was < frame_hold_ms
        ago, skip transmit.
    * On recovery (after re-init) resend last good frame once when available.

Recovery features (existing):
    * Automatic re-init on transmit or status failures with exponential backoff.
    * Periodic 0x09 status ping every N frames.
    * Watchdog re-init if >2 s since last successful frame.

All hardware interactions are safely no-ops when spidev / GPIO not present.
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
                candidates = [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
                    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
                    "/Library/Fonts/Menlo.ttc",
                    "/Library/Fonts/Consolas.ttf",
                ]
                font: Any = None
                for p in candidates:
                    try:
                        font = ImageFont.truetype(p, size_px)
                        break
                    except Exception:
                        continue
                if font is None:
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
    """Simple Pillow-backed canvas counting drawing ops for heuristics."""

    def __init__(self, img: Image.Image, fonts: _FontCache) -> None:
        self._img = img
        self._draw = ImageDraw.Draw(img)
        self._fonts = fonts
        self._ops = 0

    def clear(self, color: Color) -> None:  # override
        r, g, b, a = color
        w, h = self._img.size
        self._draw.rectangle((0, 0, w, h), fill=(r, g, b, a))
        self._ops += 1

    def line(
        self,
        p0: Tuple[int, int],
        p1: Tuple[int, int],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:  # override
        self._draw.line([p0, p1], fill=color, width=width)
        self._ops += 1

    def circle(
        self,
        center: Tuple[int, int],
        radius: int,
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:  # override
        x, y = center
        bbox = [x - radius, y - radius, x + radius, y + radius]
        self._draw.ellipse(bbox, outline=color, width=max(1, width))
        self._ops += 1

    def filled_circle(self, center: Tuple[int, int], radius: int, color: Color) -> None:  # override
        x, y = center
        bbox = [x - radius, y - radius, x + radius, y + radius]
        self._draw.ellipse(bbox, fill=color)
        self._ops += 1

    def polyline(
        self,
        pts: Sequence[Tuple[int, int]],
        width: int = 1,
        color: Color = (255, 255, 255, 255),
    ) -> None:  # override
        if pts:
            self._draw.line(list(pts), fill=color, width=width)
            self._ops += 1

    def text(
        self,
        pos: Tuple[int, int],
        s: str,
        size_px: int = 12,
        color: Color = (255, 255, 255, 255),
    ) -> None:  # override
        font = self._fonts.get(size_px)
        self._draw.text(pos, s, fill=color, font=font)
        self._ops += 1


class ILI9341DisplayBackend(DisplayBackend):
    def __init__(
        self,
        width: int = 240,
        height: int = 320,
        spi_bus: int = 0,
        spi_dev: int = 0,
        # Default GPIO (BCM) pins updated to match physical wiring on Pi
        dc_pin: int = 22,
        rst_pin: int = 18,
        led_pin: int = 12,
        hz: int = 32_000_000,
        *,
        enable_watchdog: bool = True,
    ) -> None:
        # Geometry
        self._w = int(width)
        self._h = int(height)
        self._dc = dc_pin
        self._rst = rst_pin
        self._led = led_pin
        self._hz = hz
        self._spi: _SpiLike | None = None
        self._frame: Image.Image | None = None
        self._frame_canvas: _PillowCanvas | None = None
        self._flip = False
        self._fonts = _FontCache()

        # Backlight PWM state (initialized in _init_gpio)
        self._pwm = None
        self._backlight_pct = 100.0

        # Blink mitigation state
        self._prev_frame: Image.Image | None = None
        self._last_brightness = 0.0
        self._last_push_ms = 0.0
        self._last_frame_failed = False
        self._blank_skip_threshold = 8.0
        self._blank_skip_ops_threshold = 5
        self._frame_hold_ms = 0.0

        # Health / recovery
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
        # User-configurable: whether to run the background watchdog thread.
        self._enable_watchdog = bool(enable_watchdog)

        self._init_gpio()
        self._init_spi(spi_bus, spi_dev)
        self._init_panel()
        # Start watchdog only when enabled and hardware libs present.
        if self._enable_watchdog:
            self._start_watchdog()

        # Apply persisted/default backlight percentage at startup so the
        # hardware (when present) begins at the requested brightness.
        try:
            self.set_backlight_pct(self._backlight_pct)
        except Exception:
            pass

        # ------------------------------------------------------------------
        # Performance buffers / tuning
        # Persistent RGB565 framebuffer we reuse each frame to avoid new
        # allocations and enable future partial / dirty region updates.
        self._buf_rgb565 = bytearray(2 * self._w * self._h)
        # Reusable scratch references to reduce attribute lookups in hot path.
        self._encode_last_mode = None  # reserved for future fast-path detection
        # Tunable SPI payload chunk size (bytes).
        # NOTE: Increasing this too high can exceed the kernel / driver buffer (often 4096 on RPi)
        # resulting in IOErrors that trigger our recovery loop (observed as white flashing +
        # recurring "frame transmit error"). We attempt to detect a safe ceiling and fall back
        # adaptively on failures.
        self._spi_chunk_size = 2048  # safe baseline & preserves multi-chunk test expectations
        if (self._w * self._h) >= (240 * 320):  # full 240x320 panel or larger
            # Try to read kernel spidev bufsiz (optional)
            max_buf = 4096
            try:
                p = Path("/sys/module/spidev/parameters/bufsiz")
                if p.exists():
                    with p.open("r") as f:
                        val = int(f.read().strip() or "0")
                        if val >= 2048:
                            max_buf = val
            except Exception:
                pass
            # Heuristic: use 75% of reported max (or cap at 4096 if unknown)
            target = int(min(max_buf, 4096) * 0.75)
            # Round to nearest 512 for nicer chunk boundaries
            if target < 2048:
                target = 2048
            target = (target // 512) * 512
            self._spi_chunk_size = max(2048, target)
        # Runtime adaptive downgrade uses _spi_chunk_min to stop shrinking below baseline.
        self._spi_chunk_min = 1024
        self._spi_adaptive_enabled = True
        self._spi_last_error: str | None = None
        # Fast-path raw encoder detection state for RGB565 (Pillow raw modes)
        self._fast_rgb565_mode: str | None = None  # e.g. "BGR;16" or "RGB;16"
        self._fast_rgb565_swap = False  # whether produced 16-bit words need byte swap
        self._fast_rgb565_attempted = False  # only probe once
        # Per-frame performance instrumentation (ms); updated each end_frame
        self._last_encode_ms = 0.0
        self._last_tx_ms = 0.0
        self._last_fast_used = False
        self._fast_log_emitted = False

    # (backlight state variables are instance attributes set in __init__)

    # --------------------- Hardware / low-level ---------------------
    def _init_gpio(self) -> None:
        if GPIO is None:  # pragma: no cover
            return
        with suppress(Exception):
            GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        for p in (self._dc, self._rst, self._led):
            GPIO.setup(p, GPIO.OUT)
        # Default to full on; if led pin supports PWM we will set up PWM
        # and drive duty cycle instead of a static high/low.
        try:
            # Use PWM on the LED pin at 1kHz when possible
            self._pwm = None
            GPIO.output(self._led, 1)
            # Only attempt PWM on pins that typically support hardware PWM
            # (GPIO18 on Raspberry Pi is commonly used). Create PWM but
            # don't start until a valid percentage is applied.
            try:
                self._pwm = GPIO.PWM(self._led, 1000)
                # Do not start here; start when set_backlight_pct called
            except Exception:
                # Fall back to static on
                self._pwm = None
        except Exception:
            pass

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

    def _set_addr_window(self) -> None:
        self._write_cmd(0x2A, bytes([0x00, 0x00, 0x00, (self._w - 1) & 0xFF]))
        self._write_cmd(0x2B, bytes([0x00, 0x00, (self._h - 1) >> 8, (self._h - 1) & 0xFF]))
        self._write_cmd(0x2C, None)

    # ------------------------------ API ------------------------------
    def size(self) -> Tuple[int, int]:  # override
        return (self._w, self._h)

    def begin_frame(self) -> Canvas:  # override
        if self._prev_frame is not None:
            self._frame = self._prev_frame.copy()
        else:
            self._frame = Image.new("RGBA", (self._w, self._h), (0, 0, 0, 255))
        self._frame_canvas = _PillowCanvas(self._frame, self._fonts)
        return self._frame_canvas

    def end_frame(self) -> None:  # override
        if self._frame is None:
            return
        ops = getattr(self._frame_canvas, "_ops", 0)
        brightness = self._compute_brightness(self._frame)
        now_ms = time.monotonic() * 1000.0
        hold_elapsed = now_ms - self._last_push_ms
        if self._frame_hold_ms > 0 and hold_elapsed < self._frame_hold_ms and ops == 0 and self._prev_frame is not None:
            return
        if self._should_skip_push(ops, brightness):
            return
        img = self._frame
        try:
            if self._flip:
                # Use rotate(180) instead of transpose with ROTATE_180 to avoid
                # relying on a PIL attribute that typeshed may not expose.
                img = img.rotate(180)
        except Exception:
            img = self._frame
        self._push_raw(img)
        if not self._last_frame_failed:
            with suppress(Exception):
                self._prev_frame = self._frame.copy()
            self._last_brightness = brightness
            self._last_push_ms = now_ms

    def apply_flip(self, flip: bool) -> None:  # override
        try:
            new = bool(flip)
            if new != self._flip:
                with suppress(Exception):
                    print(f"[ILI9341] apply_flip -> {new}")
            self._flip = new
        except Exception:
            self._flip = False

    def set_backlight_pct(self, pct: float) -> None:
        """Set backlight brightness as percentage (0-100).

        When GPIO PWM is available this starts/updates the PWM duty cycle.
        Otherwise this falls back to toggling the LED pin on/off.
        """
        try:
            pct_f = float(pct)
        except Exception:
            return
        pct_f = max(0.0, min(100.0, pct_f))
        self._backlight_pct = pct_f
        if GPIO is None:  # pragma: no cover
            return
        try:
            if self._pwm is not None:
                # Start PWM if not already started
                try:
                    # PWM.start takes duty cycle (0-100)
                    self._pwm.start(pct_f)
                except Exception:
                    try:
                        self._pwm.ChangeDutyCycle(pct_f)
                    except Exception:
                        pass
            else:
                # No PWM support: treat >50% as on, else off but allow
                # intermediate values by simple duty approximation using
                # a blocking blink (not desirable) — instead map >0->on
                GPIO.output(self._led, 1 if pct_f > 0.0 else 0)
        except Exception:
            pass

    def save_png(self, path: str) -> None:  # override
        if self._frame is None:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            img = self._frame
            if self._flip:
                img = img.rotate(180)
            img.save(path)
        except Exception:
            with suppress(Exception):
                self._frame.save(path)

    def shutdown(self) -> None:  # override
        self._watchdog_stop.set()
        with suppress(Exception):
            if self._watchdog_thread and self._watchdog_thread.is_alive():
                self._watchdog_thread.join(timeout=0.2)
        # Stop PWM if active
        with suppress(Exception):
            if self._pwm is not None:
                try:
                    self._pwm.stop()
                except Exception:
                    pass
                self._pwm = None
        self._close_spi()

    # ------------------ Brightness / skip heuristics ------------------
    def _compute_brightness(self, img: Image.Image) -> float:
        try:
            thumb = img.resize((32, 32))
            pixels = list(thumb.getdata())
            total = 0
            for px in pixels:
                if not px:
                    continue
                r, g, b = px[0], px[1], px[2]
                total += int(r) + int(g) + int(b)
            return (total / (len(pixels) * 3)) if pixels else 0.0
        except Exception:
            return 0.0

    def _should_skip_push(self, ops: int, brightness: float) -> bool:
        if self._prev_frame is None:
            return False
        if ops > self._blank_skip_ops_threshold:
            return False
        near_black = brightness <= 2.0
        if near_black and self._last_brightness > 0:
            return True
        if self._last_brightness > 0 and brightness > 0:
            if (self._last_brightness / max(brightness, 0.001)) >= self._blank_skip_threshold:
                return True
        return False

    # ------------------------- Transmission ---------------------------
    def _encode_rgb565(self, img: Image.Image) -> bytearray:
        """Convert RGBA/Pillow image to in-place RGB565 in persistent buffer.

        NOTE: For now we still walk all pixels; later we can introduce
        dirty-rect detection and skip unchanged spans.
        """
        import time as _t

        t_enc_start = _t.perf_counter()
        fast_used = False
        try:  # Convert once to RGB (fast in Pillow, returns new image or view)
            rgb = img.convert("RGB")
        except Exception:
            self._last_encode_ms = (_t.perf_counter() - t_enc_start) * 1000.0
            self._last_fast_used = False
            return self._buf_rgb565  # leave previous contents

        # Attempt one-time discovery of a Pillow raw mode that already yields
        # RGB565 ordering matching our panel (big-endian: high byte first).
        if not self._fast_rgb565_attempted:
            self._fast_rgb565_attempted = True
            try:
                # Prepare expected bytes for first pixel using manual pack
                px_raw = rgb.getpixel((0, 0))  # (r,g,b) or single-band value
                # Normalize to a 3-tuple of ints so static types are clear
                if not isinstance(px_raw, (tuple, list)):
                    # single-band value -> replicate across channels
                    try:
                        if isinstance(px_raw, (int, float)):
                            r0 = int(px_raw)
                        else:
                            r0 = 0
                    except Exception:
                        r0 = 0
                    g0 = r0
                    b0 = r0
                else:
                    # Ensure at least 3 components and coerce safely to ints
                    px_t = tuple(px_raw)
                    a, b, c = (px_t + (0, 0, 0))[:3]
                    from typing import Any as _Any
                    from typing import cast as _cast

                    def _safe_int(v: _Any) -> int:
                        try:
                            # cast to Any to satisfy int() signature for mypy
                            return int(_cast(_Any, v))
                        except Exception:
                            return 0

                    r0, g0, b0 = _safe_int(a), _safe_int(b), _safe_int(c)
                val0 = ((r0 & 0xF8) << 8) | ((g0 & 0xFC) << 3) | (b0 >> 3)
                exp_hi = (val0 >> 8) & 0xFF
                exp_lo = val0 & 0xFF
                candidates = ["BGR;16", "RGB;16"]
                for mode in candidates:
                    try:
                        tb_obj = rgb.crop((0, 0, 1, 1)).tobytes("raw", mode)
                    except Exception:  # unsupported mode
                        continue
                    # Ensure we have a bytes-like object before indexing
                    if not isinstance(tb_obj, (bytes, bytearray, memoryview)):
                        continue
                    tb = bytes(tb_obj)
                    if len(tb) != 2:
                        continue
                    hi, lo = tb[0], tb[1]
                    if hi == exp_hi and lo == exp_lo:  # exact match
                        self._fast_rgb565_mode = mode
                        self._fast_rgb565_swap = False
                        break
                    if hi == exp_lo and lo == exp_hi:  # byte-swapped
                        self._fast_rgb565_mode = mode
                        self._fast_rgb565_swap = True
                        break
            except Exception:
                pass  # Leave fast path disabled

        # If we discovered a suitable raw encoder, use it and copy/adjust into persistent buffer.
        if self._fast_rgb565_mode is not None:
            try:
                raw16_obj = rgb.tobytes("raw", self._fast_rgb565_mode)
                if not isinstance(raw16_obj, (bytes, bytearray, memoryview)):
                    raise RuntimeError("unexpected raw encoder output")
                raw16 = bytes(raw16_obj)
                if len(raw16) == 2 * self._w * self._h:
                    out = self._buf_rgb565
                    if not self._fast_rgb565_swap:
                        # Direct copy
                        mv_out = memoryview(out)
                        mv_out[: len(raw16)] = raw16
                    else:
                        # Swap bytes per 16-bit word
                        mv_out = memoryview(out)
                        for i in range(0, len(raw16), 2):
                            mv_out[i] = raw16[i + 1]
                            mv_out[i + 1] = raw16[i]
                    fast_used = True
                    self._last_encode_ms = (_t.perf_counter() - t_enc_start) * 1000.0
                    self._last_fast_used = True
                    if not self._fast_log_emitted:
                        with suppress(Exception):
                            msg = (
                                "[ILI9341] rgb565 fast-path: "
                                f"mode={self._fast_rgb565_mode} "
                                f"swap={self._fast_rgb565_swap}"
                            )
                            print(msg)
                        self._fast_log_emitted = True
                    return out
            except Exception:
                # On any failure fall back to manual path for this frame; keep previous detection for future tries.
                pass

        # Manual per-pixel conversion fallback (baseline path)
        try:
            raw = rgb.tobytes()  # bytes of length 3*w*h
        except Exception:
            return self._buf_rgb565
        out = self._buf_rgb565
        mv_in = memoryview(raw)
        mv_out = memoryview(out)
        w3 = len(mv_in)
        oi = 0
        # Iterate over triplets in the input memoryview and write big-endian 16-bit words
        for i in range(0, w3, 3):
            r = mv_in[i]
            g = mv_in[i + 1]
            b = mv_in[i + 2]
            val = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            mv_out[oi] = (val >> 8) & 0xFF
            mv_out[oi + 1] = val & 0xFF
            oi += 2
        self._last_encode_ms = (_t.perf_counter() - t_enc_start) * 1000.0
        self._last_fast_used = fast_used
        if not fast_used and not self._fast_log_emitted and self._fast_rgb565_attempted:
            # Log once if fast path was attempted but not used
            with suppress(Exception):
                print("[ILI9341] rgb565 fast-path: disabled")
            self._fast_log_emitted = True
        return out

    def _push_raw(self, img: Image.Image) -> None:
        import time as _t

        out = self._encode_rgb565(img)  # returns persistent buffer
        t_tx_start = _t.perf_counter()
        attempt = 0
        chunk_size = self._spi_chunk_size
        last_exc: Exception | None = None
        while True:
            attempt += 1
            try:
                self._set_addr_window()
                if GPIO is not None:
                    GPIO.output(self._dc, 1)
                spi = self._spi
                if spi is not None:
                    mv = memoryview(out)
                    for i in range(0, len(out), chunk_size):
                        segment = mv[i : i + chunk_size]
                        with SPI_BUS_LOCK:
                            try:
                                wb = getattr(spi, "writebytes", None)
                                if wb is not None:
                                    wb(segment)
                                else:
                                    x2 = getattr(spi, "xfer2", None)
                                    if x2 is not None:
                                        x2(list(segment))
                                    else:
                                        raise RuntimeError("No valid SPI write method")
                            except TypeError:
                                # Driver expects list[int]
                                spi.writebytes(list(segment))
                # Success path
                self._frame_counter += 1
                if self._frame_counter % self._ping_interval_frames == 0:
                    self._status_ping()
                self._last_ok = time.monotonic()
                self._fail_streak = 0
                self._next_backoff_s = 0.05
                self._last_frame_failed = False
                # If we previously downgraded due to errors and sustained successes, consider gentle re-up later.
                self._last_tx_ms = (_t.perf_counter() - t_tx_start) * 1000.0
                return
            except Exception as exc:  # transmit failure
                last_exc = exc
                self._last_frame_failed = True
                # Adaptive downgrade: shrink chunk size and retry once or twice before giving up.
                if self._spi_adaptive_enabled and chunk_size > self._spi_chunk_min and attempt < 3:
                    # Halve the chunk size (down to min) and retry.
                    new_chunk = max(self._spi_chunk_min, chunk_size // 2)
                    if new_chunk != chunk_size:
                        with suppress(Exception):
                            print(
                                f"[ILI9341] spi error: {exc!r}; reducing chunk {chunk_size} -> {new_chunk} and retrying"
                            )
                        chunk_size = new_chunk
                        self._spi_chunk_size = new_chunk  # persist for future frames
                        continue
                # Give up: record failure and break to recovery.
                break
        # Failure after retries
        self._fail_streak += 1
        self._spi_last_error = type(last_exc).__name__ if last_exc else "unknown"
        self._attempt_recover("frame transmit error")
        self._last_tx_ms = (_t.perf_counter() - t_tx_start) * 1000.0
        return

        # Normal success path sets tx duration before return above

    # Exposed instrumentation accessors (lightweight)
    def last_encode_ms(self) -> float:
        return self._last_encode_ms

    def last_tx_ms(self) -> float:
        return self._last_tx_ms

    def last_fast_used(self) -> bool:
        return self._last_fast_used

    # ----------------------- Recovery / health -----------------------
    def _close_spi(self) -> None:
        with suppress(Exception):
            if self._spi is not None:
                with SPI_BUS_LOCK:
                    close = getattr(self._spi, "close", None)
                    if callable(close):
                        close()
                self._spi = None

    def reset_and_init(self) -> None:
        with self._recover_lock:
            with suppress(Exception):
                self._close_spi()
                self._init_spi(0, 0)
                self._init_panel()

    def _status_ping(self) -> None:
        if self._spi is None:
            return
        try:
            if GPIO is not None:
                GPIO.output(self._dc, 0)
            with SPI_BUS_LOCK:
                self._spi.writebytes([0x09])
            if GPIO is not None:
                GPIO.output(self._dc, 1)
            xfer2 = getattr(self._spi, "xfer2", None)
            if not callable(xfer2):
                return
            with SPI_BUS_LOCK:
                resp = xfer2([0x00, 0x00, 0x00, 0x00, 0x00])
            try:
                if not hasattr(resp, "__iter__"):
                    raise RuntimeError("status resp parse error")
                from typing import Iterable, cast

                data = list(cast(Iterable[int], resp))[1:5]
            except Exception:
                raise RuntimeError("status resp parse error")
            if not data:
                raise RuntimeError("empty status")
            if all(b == 0x00 for b in data) or all(b == 0xFF for b in data):
                raise RuntimeError("degenerate status bytes")
            crc = 0
            for b in data:
                crc = (crc ^ b) & 0xFF
            self._last_status_crc = crc
        except Exception:
            if self._fail_streak > 3:
                self._fail_streak += 1
                self._attempt_recover("status ping failure")

    def _attempt_recover(self, reason: str) -> None:
        now = time.monotonic()
        with self._recover_lock:
            delay = self._next_backoff_s
            self._next_backoff_s = min(self._max_backoff_s, self._next_backoff_s * 2.0)
            with suppress(Exception):
                print("[ILI9341] recover (reason=%s, streak=%s, backoff=%.2fs)" % (reason, self._fail_streak, delay))
            time.sleep(delay)
            try:
                self.reset_and_init()
                self._last_ok = now
                if self._prev_frame is not None:
                    img = self._prev_frame
                    if self._flip:
                        img = img.rotate(180)
                    self._push_raw(img)
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
        if spidev is None:  # no hardware libs
            return
        t = threading.Thread(target=self._watchdog_run, name="ili9341-watchdog", daemon=True)
        self._watchdog_thread = t
        with suppress(Exception):
            t.start()


__all__ = ["ILI9341DisplayBackend"]
