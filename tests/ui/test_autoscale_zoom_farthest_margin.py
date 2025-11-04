from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pocketscope.core.events import EventBus
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView
from pocketscope.settings.schema import Settings
from pocketscope.settings.store import SettingsStore
from pocketscope.ui.controllers import UiConfig, UiController, _TrackMetric


@pytest.mark.asyncio
async def test_autoscale_sparse_traffic_zoom_to_farthest_plus_margin(tmp_path, monkeypatch):
    """
    When the number of visible aircraft is <= autoscale_target_visible the
    controller should zoom so the farthest eligible aircraft is within the
    edge plus a 5% margin. This applies regardless of prior ladder behaviour.
    """
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    # User starts zoomed far out; sparse traffic should shrink view.
    SettingsStore.save(
        Settings(
            range_nm=50.0,
            autoscale_enabled=True,
            autoscale_target_visible=5,
            altitude_filter="All",
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
        cfg=UiConfig(target_fps=5.0, range_nm=50.0),
    )

    metrics = [
        _TrackMetric(
            track=SimpleNamespace(state={"track_deg": 90.0, "ground_speed": 250.0, "baro_alt": 12000.0}),
            last_point=None,
            lat=42.0,
            lon=-71.0,
            altitude_ft=12000.0,
            distance_nm=8.0,
        ),
        _TrackMetric(
            track=SimpleNamespace(state={"track_deg": 45.0, "ground_speed": 320.0, "baro_alt": 15000.0}),
            last_point=None,
            lat=42.3,
            lon=-70.5,
            altitude_ft=15000.0,
            distance_nm=24.0,
        ),
        _TrackMetric(
            track=SimpleNamespace(state={"track_deg": 270.0, "ground_speed": 280.0, "baro_alt": 18000.0}),
            last_point=None,
            lat=42.5,
            lon=-70.2,
            altitude_ft=18000.0,
            distance_nm=32.0,
        ),
    ]

    try:
        ui._apply_autoscale(metrics)  # type: ignore[attr-defined]
        # 3 visible (<= target 5) but debounce not yet satisfied.
        # Advance time by 10+ seconds to trigger the sparse zoom rule.
        ts.advance(11.0)
        ui._apply_autoscale(metrics)  # type: ignore[attr-defined]
        
        # Now: farthest 32nm; expect 32 * 1.05 = 33.6nm
        assert ui._cfg.range_nm == pytest.approx(33.6)
        assert ui._autoscale_range_nm == pytest.approx(33.6)
    finally:
        await ui.stop()
        await tracks.stop()
