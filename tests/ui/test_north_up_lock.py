from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pocketscope.core.events import EventBus
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView
from pocketscope.ui.controllers import UiConfig, UiController


@pytest.mark.asyncio
async def test_north_up_lock_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=1e6)
    await tracks.run()
    display = PygameDisplayBackend(size=(200, 200))
    view = PpiView()
    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(target_fps=5.0, range_nm=10.0),
    )
    # Start controller loop
    task = asyncio.create_task(ui.run())
    # Allow a couple frames
    for _ in range(2):
        ts.advance(0.2)
        await asyncio.sleep(0)
    # Initially locked -> rotation stays zero
    ui.rotate_right()
    assert getattr(view, "rotation_deg", 0.0) in (0.0, 360.0)
    # Unlock and rotate
    ui.toggle_north_up_lock(persist=False)
    assert ui.north_up_lock is False
    ui.rotate_right()
    # Let a frame apply rotation
    ts.advance(0.2)
    await asyncio.sleep(0)
    assert getattr(view, "rotation_deg", 0.0) != 0.0
    # Re-lock -> rotation reset to zero
    ui.toggle_north_up_lock(persist=False)
    ts.advance(0.2)
    await asyncio.sleep(0)
    assert ui.north_up_lock is True
    assert getattr(view, "rotation_deg", 0.0) in (0.0, 360.0)
    await ui.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await tracks.stop()
