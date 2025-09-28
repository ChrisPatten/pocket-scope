from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from pocketscope.core.events import EventBus, pack
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView
from pocketscope.settings.schema import Settings
from pocketscope.settings.store import SettingsStore
from pocketscope.ui.controllers import UiConfig, UiController, _TrackMetric


@pytest.mark.asyncio
async def test_autoscale_respects_user_altitude_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    SettingsStore.save(
        Settings(
            range_nm=10.0,
            autoscale_enabled=True,
            autoscale_target_visible=1,
            altitude_filter="All",
            altitude_max_ft=24000.0,
        )
    )

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

    async def publish_track(icao: str, alt_ft: float) -> None:
        msg = {
            "ts": datetime.fromtimestamp(ts.wall_time(), tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "icao24": icao,
            "lat": 40.0,
            "lon": -70.0,
            "geo_alt": alt_ft,
        }
        await bus.publish("adsb.msg", pack(msg))

    try:
        await publish_track("aaaaaa", 12000.0)
        await publish_track("bbbbbb", 27000.0)
        ts.advance(1.0)
        await asyncio.sleep(0)

        metrics, _ = ui._collect_track_metrics()  # type: ignore[attr-defined]
        assert len(metrics) == 2  # Sanity check: both tracks collected

        ui._apply_autoscale(metrics)  # type: ignore[attr-defined]

        assert ui._autoscale_alt_override is None
        lo, hi = ui.alt_filter
        assert lo is None
        assert hi == pytest.approx(24000.0)
    finally:
        await ui.stop()
        await tracks.stop()


@pytest.mark.asyncio
async def test_autoscale_caps_zoom_to_farthest_aircraft(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    SettingsStore.save(
        Settings(
            range_nm=10.0,
            autoscale_enabled=True,
            autoscale_target_visible=3,
            altitude_filter="All",
            autoscale_max_range_nm=35.0,
        )
    )

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

    metrics = [
        _TrackMetric(
            track=SimpleNamespace(
                state={
                    "track_deg": 90.0,
                    "ground_speed": 250.0,
                    "baro_alt": 12000.0,
                }
            ),
            last_point=None,
            lat=42.0,
            lon=-71.0,
            altitude_ft=12000.0,
            distance_nm=8.0,
        ),
        _TrackMetric(
            track=SimpleNamespace(
                state={
                    "track_deg": 45.0,
                    "ground_speed": 320.0,
                    "baro_alt": 15000.0,
                }
            ),
            last_point=None,
            lat=42.1,
            lon=-70.0,
            altitude_ft=15000.0,
            distance_nm=32.0,
        ),
    ]

    try:
        ui._apply_autoscale(metrics)  # type: ignore[attr-defined]

        assert ui._cfg.range_nm == pytest.approx(35.0)
        assert ui._autoscale_range_nm == pytest.approx(35.0)
    finally:
        await ui.stop()
        await tracks.stop()


@pytest.mark.asyncio
async def test_autoscale_ignores_partial_datablocks(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    SettingsStore.save(
        Settings(
            range_nm=10.0,
            autoscale_enabled=True,
            autoscale_target_visible=2,
            altitude_filter="All",
            autoscale_max_range_nm=20.0,
        )
    )

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

    metrics = [
        _TrackMetric(
            track=SimpleNamespace(
                state={
                    "track_deg": 90.0,
                    "ground_speed": 250.0,
                    "baro_alt": 12000.0,
                }
            ),
            last_point=None,
            lat=42.0,
            lon=-71.0,
            altitude_ft=12000.0,
            distance_nm=8.0,
        ),
        _TrackMetric(
            track=SimpleNamespace(state={"baro_alt": 18000.0}),
            last_point=None,
            lat=43.0,
            lon=-72.0,
            altitude_ft=18000.0,
            distance_nm=55.0,
        ),
    ]

    try:
        ui._apply_autoscale(metrics)  # type: ignore[attr-defined]
        # With partial datablocks now included, autoscale should consider the
        # second (more distant) aircraft when determining range. The user
        # requested range is 10nm (treated as lower-bound mode). Target=2
        # aircraft: we have 1 inside 10nm, 2 by 55nm. The max configured
        # autoscale_max_range_nm is 20.0 so autoscale should expand up to
        # that cap to try to include the second aircraft.
        assert ui._cfg.range_nm == pytest.approx(20.0)
        assert ui._autoscale_range_nm == pytest.approx(20.0)
    finally:
        await ui.stop()
        await tracks.stop()


@pytest.mark.asyncio
async def test_autoscale_honors_min_range_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    SettingsStore.save(
        Settings(
            range_nm=10.0,
            autoscale_enabled=True,
            autoscale_target_visible=1,
            altitude_filter="All",
            autoscale_min_range_nm=25.0,
        )
    )

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

    metrics = [
        _TrackMetric(
            track=SimpleNamespace(
                state={
                    "track_deg": 180.0,
                    "ground_speed": 210.0,
                    "baro_alt": 15000.0,
                }
            ),
            last_point=None,
            lat=41.9,
            lon=-70.9,
            altitude_ft=15000.0,
            distance_nm=12.0,
        )
    ]

    try:
        ui._apply_autoscale(metrics)  # type: ignore[attr-defined]

        assert ui._cfg.range_nm == pytest.approx(25.0)
    finally:
        await ui.stop()
        await tracks.stop()


@pytest.mark.asyncio
async def test_autoscale_exceeds_altitude_when_range_limited(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    SettingsStore.save(
        Settings(
            range_nm=10.0,
            autoscale_enabled=True,
            autoscale_target_visible=2,
            altitude_filter="All",
            altitude_max_ft=24000.0,
            autoscale_max_range_nm=60.0,
        )
    )

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
        cfg=UiConfig(target_fps=5.0, range_nm=10.0, max_range_nm=80.0),
    )

    metrics = [
        _TrackMetric(
            track=SimpleNamespace(
                state={
                    "track_deg": 90.0,
                    "ground_speed": 250.0,
                    "baro_alt": 12000.0,
                }
            ),
            last_point=None,
            lat=42.0,
            lon=-71.0,
            altitude_ft=12000.0,
            distance_nm=12.0,
        ),
        _TrackMetric(
            track=SimpleNamespace(
                state={
                    "track_deg": 45.0,
                    "ground_speed": 320.0,
                    "baro_alt": 30000.0,
                }
            ),
            last_point=None,
            lat=42.5,
            lon=-69.5,
            altitude_ft=30000.0,
            distance_nm=58.0,
        ),
    ]

    try:
        ui._apply_autoscale(metrics)  # type: ignore[attr-defined]

        assert ui._cfg.range_nm == pytest.approx(60.0)
        assert ui._autoscale_alt_override is not None
        lo, hi = ui.alt_filter
        assert hi is None
        assert lo is None
    finally:
        await ui.stop()
        await tracks.stop()
