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
from pocketscope.settings.store import SettingsStore
from pocketscope.settings.schema import Settings


@pytest.mark.asyncio
async def test_custom_altitude_bounds_override_band(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    # Persist settings with explicit bounds spanning only middle altitude
    custom_settings = Settings(
        altitude_filter="All", altitude_min_ft=6000.0, altitude_max_ft=9000.0
    )
    SettingsStore.save(custom_settings)

    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=1e9)
    await tracks.run()

    display = PygameDisplayBackend(size=(120, 120))
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

    async def pub(icao: str, alt: float):
        msg = {
            "ts": datetime.fromtimestamp(ts.wall_time(), tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "icao24": icao,
            "lat": 40.0,
            "lon": -70.0,
            "geo_alt": alt,
        }
        await bus.publish("adsb.msg", pack(msg))

    # Publish three aircraft across bands
    await pub("aaaaaa", 3000.0)  # below custom min
    await pub("bbbbbb", 8000.0)  # within custom bounds
    await pub("cccccc", 25000.0)  # above custom max
    ts.advance(1.0)
    await asyncio.sleep(0)

    snaps = ui._build_snapshots()  # type: ignore[attr-defined]
    icaos = sorted(s.icao for s in snaps)
    assert icaos == ["bbbbbb"]

    await ui.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await tracks.stop()
