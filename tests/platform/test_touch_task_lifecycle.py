from __future__ import annotations

import asyncio
import types
import warnings

import pytest


class _SpiMock:
    def open(self, bus: int, dev: int) -> None:  # pragma: no cover - mock
        pass

    def xfer2(self, data):  # type: ignore[no-untyped-def]
        return [0, 0, 0]


class _GpioMock:
    BCM = 0
    IN = 1
    PUD_UP = 2

    def setmode(self, mode: int) -> None:  # pragma: no cover - mock
        pass

    def setup(self, pin: int, mode: int, pull_up_down=None):  # pragma: no cover - mock
        pass

    def input(self, pin: int) -> int:  # pragma: no cover - mock
        return 1


@pytest.mark.asyncio
async def test_touch_run_task_cancel(monkeypatch: pytest.MonkeyPatch):
    from pocketscope.platform.input import xpt2046_touch as mod

    mod.spidev = types.SimpleNamespace(SpiDev=lambda: _SpiMock())  # type: ignore
    mod.GPIO = _GpioMock()  # type: ignore
    t = mod.XPT2046Touch(width=10, height=10, poll_hz=5.0)
    coro = t.run()
    assert hasattr(coro, "__await__")
    task = asyncio.create_task(coro)
    t._task = task  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    with warnings.catch_warnings(record=True) as w:
        t.stop()
        await asyncio.sleep(0)  # let cancellation propagate
        await asyncio.gather(task, return_exceptions=True)
        runtime_warnings = [m for m in w if issubclass(m.category, RuntimeWarning)]
        assert not runtime_warnings
