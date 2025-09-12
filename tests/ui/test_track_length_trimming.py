from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from pocketscope.core.events import EventBus, pack
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView
from pocketscope.ui.controllers import UiConfig, UiController


@pytest.mark.asyncio
async def test_track_length_cycle_retrim(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=1e9)
    await tracks.run()

    display = PygameDisplayBackend(size=(100, 100))
    view = PpiView(show_data_blocks=False)
    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(target_fps=5.0, range_nm=10.0),
    )

    task = asyncio.create_task(ui.run())

    # Helper to publish position messages every second
    async def pub_points(n: int, start_lat=40.0):
        for i in range(n):
            msg = {
                "ts": datetime.fromtimestamp(ts.wall_time(), tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "icao24": "abc123",
                "lat": start_lat + i * 0.01,
                "lon": -70.0,
            }
            await bus.publish("adsb.msg", pack(msg))
            ts.advance(1.0)
            await asyncio.sleep(0)

    # Build 100s of history (will be trimmed later)
    await pub_points(100)
    tr = tracks.get("abc123")
    assert tr is not None
    # Default length ~45s window (approx 45 points due to 1Hz)
    medium_len = len(tr.history)
    assert 30 <= medium_len <= 50

    # Cycle to 120s -> more points retained as new ones added
    ui.cycle_track_length(persist=False)
    assert int(ui.track_length_s) == 120
    await pub_points(30, start_lat=50.0)  # extend another 30s
    tr = tracks.get("abc123")
    assert tr is not None
    long_len = len(tr.history)
    assert long_len >= medium_len  # should not shrink
    assert long_len <= 130  # sanity upper bound

    # Cycle twice: 120 -> 15 -> 45
    ui.cycle_track_length(persist=False)  # 120 -> 15
    assert int(ui.track_length_s) == 15
    tr = tracks.get("abc123")
    assert tr is not None
    # After immediate retrim expect about 15 points
    assert 5 <= len(tr.history) <= 20

    await ui.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await tracks.stop()
