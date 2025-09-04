from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from pocketscope.core.events import EventBus, pack
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService


@pytest.mark.asyncio
async def test_ui_smoke(tmp_path: Path) -> None:
    # Must set before importing pygame backend
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
    from pocketscope.render.view_ppi import PpiView
    from pocketscope.ui.controllers import UiConfig, UiController

    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=1e9)
    await tracks.run()

    # Publish a couple of ADS-B messages directly
    import datetime as dt

    for i in range(2):
        t = 0.0 + i * 0.1
        ts_wall = ts.wall_time() + t
        d = {
            "ts": dt.datetime.fromtimestamp(ts_wall, tz=dt.timezone.utc).isoformat(),
            "icao24": "abc123",
            "callsign": "TEST",
            "lat": 42.0 + 0.01 * i,
            "lon": -71.0 - 0.01 * i,
            "baro_alt": 30000,
            "ground_speed": 400,
            "track_deg": 90.0,
            "src": "PLAYBACK",
        }
        await bus.publish("adsb.msg", pack(d))

    display = PygameDisplayBackend(size=(320, 480))
    view = PpiView(show_data_blocks=False)
    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(target_fps=10.0, range_nm=10.0),
        center_lat=42.0,
        center_lon=-71.0,
    )

    # Run UI and advance sim time to drive frames
    task = asyncio.create_task(ui.run())

    # Drive ~0.3 simulated seconds at 10 Hz (0.1s per frame)
    for _ in range(4):
        ts.advance(0.1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # Save a frame PNG into pytest-provided tmp_path to avoid mutating repo
    out_path = tmp_path / "ui_smoke.png"
    display.save_png(str(out_path))

    # Zoom test: range should step down on zoom_in
    before = ui._cfg.range_nm
    ui.zoom_in()
    ui.zoom_in()
    after = ui._cfg.range_nm
    assert after <= before

    await ui.stop()
    # Allow loop to observe stop
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await tracks.stop()
