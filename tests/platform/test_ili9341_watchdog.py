from __future__ import annotations

import time
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

    def close(self) -> None:  # pragma: no cover - exercised via recovery only
        pass


def _make_backend(monkeypatch: pytest.MonkeyPatch, **kwargs):
    from pocketscope.platform.display import ili9341_backend as mod

    monkeypatch.setattr(mod, "spidev", types.SimpleNamespace(SpiDev=_SpiMock))
    monkeypatch.setattr(mod, "GPIO", None)
    # Disable the background thread; we assert the idle predicate directly.
    return mod.ILI9341DisplayBackend(enable_watchdog=False, **kwargs)


def test_watchdog_timeout_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _make_backend(monkeypatch, watchdog_timeout_s=7.5)
    assert backend._watchdog_timeout_s == pytest.approx(7.5)


def test_present_refreshes_idle_timer_independent_of_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow-but-progressing render must keep the watchdog satisfied.

    The idle timer the watchdog reads (_last_activity) is stamped when a frame
    enters present(), so it advances even if the last *successful* push
    (_last_ok) is stale. This prevents a slow frame from being misread as a
    hardware hang and resetting the panel to white.
    """
    backend = _make_backend(monkeypatch, watchdog_timeout_s=5.0)

    # Simulate a long-stale "last successful push" while a frame is delivered.
    backend._last_ok = time.monotonic() - 100.0
    backend._last_activity = time.monotonic() - 100.0

    frame = Image.new("RGBA", backend.size(), (10, 20, 30, 255))
    backend.present(frame)

    idle = time.monotonic() - backend._last_activity
    assert idle < backend._watchdog_timeout_s
