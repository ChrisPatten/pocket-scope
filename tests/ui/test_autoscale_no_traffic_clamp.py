from __future__ import annotations

import pytest

from pocketscope.core.events import EventBus
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView
from pocketscope.settings.schema import Settings
from pocketscope.settings.store import SettingsStore
from pocketscope.ui.controllers import UiConfig, UiController


@pytest.mark.asyncio
async def test_autoscale_no_traffic_clamps_to_autoscale_max(tmp_path, monkeypatch):
    """
    With no eligible aircraft the view must not zoom out to the controller
    ceiling (max_range_nm). It should respect autoscale_max_range_nm so the
    no-traffic render stays bounded (the expensive wide-range render that
    previously starved the frame loop and tripped the display watchdog).
    """
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    # User range above every cap; autoscale max well below the 80nm ceiling.
    SettingsStore.save(
        Settings(
            range_nm=100.0,
            autoscale_enabled=True,
            autoscale_min_range_nm=15.0,
            autoscale_max_range_nm=50.0,
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
        cfg=UiConfig(target_fps=5.0, range_nm=100.0),
    )

    try:
        # No eligible metrics -> must clamp to autoscale_max_range_nm (50nm),
        # not the 80nm controller ceiling.
        ui._apply_autoscale([])  # type: ignore[attr-defined]
        assert ui._cfg.range_nm == pytest.approx(50.0)
        assert ui._autoscale_range_nm == pytest.approx(50.0)
    finally:
        await ui.stop()
        await tracks.stop()
