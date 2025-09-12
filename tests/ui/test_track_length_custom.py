from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from pocketscope.core.events import EventBus, pack
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView
from pocketscope.settings.store import SettingsStore
from pocketscope.settings.schema import Settings
from pocketscope.ui.controllers import UiConfig, UiController


@pytest.mark.asyncio
async def test_custom_track_length_applied_on_start(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    # Persist custom long trail length before controller instantiation
    s = Settings()
    s.track_length_s = 300.0
    SettingsStore.save(s)

    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=1e9)
    await tracks.run()

    display = PygameDisplayBackend(size=(80, 80))
    view = PpiView(show_data_blocks=False)
    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(target_fps=5.0, range_nm=10.0),
    )

    # After initialization the TrackService windows should match custom value
    assert abs(tracks._trail_len_default_s - 300.0) < 1e-6
    # Pinned window should be >= default (next preset or same). With custom 300
    assert tracks._trail_len_pinned_s >= 300.0

    async def pub_points(n: int):
        for i in range(n):
            msg = {
                "ts": datetime.fromtimestamp(ts.wall_time(), tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "icao24": "abc123",
                "lat": 40.0 + i * 0.01,
                "lon": -70.0,
            }
            await bus.publish("adsb.msg", pack(msg))
            ts.advance(1.0)
            await asyncio.sleep(0)

    # Publish 250s worth of points; all should be retained ( < 300s window )
    await pub_points(250)
    tr = tracks.get("abc123")
    assert tr is not None
    assert 220 <= len(tr.history) <= 255  # allow slack for sampling rule

    # Advance another 100s to exceed 300s window -> oldest ~50 trimmed
    await pub_points(100)
    tr = tracks.get("abc123")
    assert tr is not None
    # Expect roughly 300 points retained (±10 tolerance)
    assert 280 <= len(tr.history) <= 305

    await tracks.stop()
