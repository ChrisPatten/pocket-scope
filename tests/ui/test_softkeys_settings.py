from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pocketscope.core.events import EventBus
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView
from pocketscope.settings.store import SettingsStore
from pocketscope.tools.config_watcher import ConfigWatcher
from pocketscope.ui.controllers import UiConfig, UiController
from pocketscope.ui.softkeys import SoftKeyBar


@pytest.mark.asyncio
async def test_softkeys_and_hot_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=1e9)
    await tracks.run()

    display = PygameDisplayBackend(size=(200, 200))
    view = PpiView(show_data_blocks=False)
    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(target_fps=10.0, range_nm=10.0),
    )

    bar = SoftKeyBar(
        display.size(),
        actions={
            "Zoom-": ui.zoom_out,
            "Units": ui.cycle_units,
            "Tracks": ui.cycle_track_length,
            "Demo": ui.toggle_demo,
            "Settings": lambda: None,
            "Zoom+": ui.zoom_in,
        },
    )
    ui.set_softkeys(bar)

    watcher = ConfigWatcher(bus)
    watcher_task = asyncio.create_task(watcher.run())

    task = asyncio.create_task(ui.run())

    for _ in range(3):
        ts.advance(0.1)
        await asyncio.sleep(0)

    # Click Units
    rect = bar._rects[1]
    bar.on_mouse(rect[0] + 1, rect[1] + 1, True)
    await asyncio.sleep(0.4)
    data = json.loads(SettingsStore.settings_path().read_text())
    assert data["units"] != "nm_ft_kt"

    # Cycle Tracks to long
    rect = bar._rects[2]
    bar.on_mouse(rect[0] + 1, rect[1] + 1, True)
    assert ui.track_length_mode == "long"

    # Demo toggle persists
    rect = bar._rects[3]
    bar.on_mouse(rect[0] + 1, rect[1] + 1, True)
    await asyncio.sleep(0.4)
    data = json.loads(SettingsStore.settings_path().read_text())
    assert data["demo_mode"] is True
    assert ui.demo_mode is True

    # Zoom in persists
    rect = bar._rects[-1]
    bar.on_mouse(rect[0] + 1, rect[1] + 1, True)
    await asyncio.sleep(0.4)
    data = json.loads(SettingsStore.settings_path().read_text())
    assert data["range_nm"] != 10.0

    # Hot reload units
    s = SettingsStore.load()
    s.units = "km_m_kmh"
    SettingsStore.save(s)
    await asyncio.sleep(0.4)
    assert ui.units == "km_m_kmh"

    out_path = tmp_path / "ui_softkeys_smoke.png"
    display.save_png(str(out_path))
    assert out_path.exists() and out_path.stat().st_size > 0

    await ui.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await watcher.stop()
    watcher_task.cancel()
    await tracks.stop()
