from __future__ import annotations

import os
from pathlib import Path

import pytest

from pocketscope.ui.controllers import UiController, UiConfig
from pocketscope.core.events import EventBus


class _StubTimeSource:
    def monotonic(self):  # simple increasing value
        import time

        return time.monotonic()

    def wall_time(self):  # return epoch seconds
        import time

        return time.time()

    async def sleep(self, dt: float):  # pragma: no cover - unused
        import asyncio

        await asyncio.sleep(dt)


class _StubTracks:
    def __init__(self):
        self._trail_len_default_s = 60.0
        self._trail_len_pinned_s = 180.0
        self._expiry_s = 300.0

    def retrim_all(self):  # pragma: no cover - no-op
        return None


class _StubDisplay:
    def __init__(self) -> None:
        self._size = (100, 100)
        self._saved: str | None = None

    def size(self):  # pragma: no cover - trivial
        return self._size

    def begin_frame(self):  # pragma: no cover - minimal contract
        class _C:
            def clear(self, *_args, **_kw):
                return None

            def line(self, *_, **__):
                return None

            def circle(self, *_, **__):
                return None

            def filled_circle(self, *_, **__):
                return None

            def polyline(self, *_, **__):
                return None

            def text(self, *_, **__):
                return None

        return _C()

    def end_frame(self):  # pragma: no cover - unused in test
        return None

    def save_png(self, path: str):  # create tiny placeholder file
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")
        self._saved = path


@pytest.mark.asyncio
async def test_ui_controller_screenshot_creates_file(tmp_path):
    backend = _StubDisplay()
    # Minimal stand-ins for required collaborators (lightweight objects / dummies)
    class _Dummy:
        pass

    ui = UiController(  # type: ignore[arg-type]
        display=backend,
        view=_Dummy(),  # type: ignore[arg-type]
        bus=EventBus(),
        ts=_StubTimeSource(),  # type: ignore[arg-type]
        tracks=_StubTracks(),  # type: ignore[arg-type]
        cfg=UiConfig(range_nm=10.0),
        center_lat=0.0,
        center_lon=0.0,
    )
    # Force screenshot into tmp dir
    path = ui.screenshot(str(tmp_path / "shot.png"))
    assert path is not None
    assert os.path.exists(path)
    # Ensure stub backend recorded the save call
    assert backend._saved == path
    await ui.stop()
