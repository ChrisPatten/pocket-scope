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
async def test_altitude_filter_cycle(tmp_path, monkeypatch):
    """End-to-end altitude filter cycles and filters snapshots.

    Creates three aircraft at representative altitudes and asserts that
    cycling the altitude filter band yields only aircraft inside the band.
    """
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

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

    # Publish three aircraft at different altitudes
    await pub("aaaaaa", 3000.0)  # 0-5k band
    await pub("bbbbbb", 8000.0)  # 5-10k band
    await pub("cccccc", 25000.0)  # >20k band
    ts.advance(1.0)
    await asyncio.sleep(0)

    # Helper to collect current snapshot ICAOs
    def snapshot_icaos():
        snaps = ui._build_snapshots()  # type: ignore[attr-defined]
        return sorted(s.icao for s in snaps)

    # All band shows all
    ui.altitude_filter = "All"
    ui._settings.altitude_filter = "All"  # type: ignore[attr-defined]
    assert snapshot_icaos() == ["aaaaaa", "bbbbbb", "cccccc"]

    # Cycle through bands and assert filtering
    for band, expected in [
        ("0–5k", ["aaaaaa"]),
        ("5–10k", ["bbbbbb"]),
        ("10–20k", []),  # none in this band
        (">20k", ["cccccc"]),
    ]:
        ui.altitude_filter = band
        ui._settings.altitude_filter = band  # type: ignore[attr-defined]
        icaos = snapshot_icaos()
        assert icaos == expected

    await ui.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await tracks.stop()
