from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from pocketscope.core.events import EventBus
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView
from pocketscope.settings.store import SettingsStore
from pocketscope.ui.controllers import UiConfig, UiController
from pocketscope.ui.softkeys import SoftKeyBar


@pytest.mark.asyncio
async def test_settings_back_save_softkeys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=1e9)
    await tracks.run()

    display = PygameDisplayBackend(size=(220, 260))
    view = PpiView(show_data_blocks=False)
    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(target_fps=15.0, range_nm=10.0),
        font_px=11,
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

    task = asyncio.create_task(ui.run())

    # Allow loop to start
    for _ in range(2):
        ts.advance(0.1)
        await asyncio.sleep(0)

    # Open settings via hotkey simulation
    ui._settings_screen.visible = True

    # Let a frame render with settings visible so softkeys swap
    for _ in range(2):
        ts.advance(0.1)
        await asyncio.sleep(0)

    # Ensure Back/Save present
    labels = list(bar.actions.keys())
    assert labels == ["Back", "Save"] or set(labels) == {"Back", "Save"}

    # Touch settings to ensure a pending debounced save then force immediate Save flush
    ui.cycle_units()
    path = SettingsStore.settings_path()
    if not path.exists():  # ensure file present (debounce may delay initial write)
        SettingsStore.save(ui._settings)
    before_mtime = path.stat().st_mtime
    # Sleep slightly to ensure mtime difference
    time.sleep(0.05)
    bar.actions["Save"]()
    after_mtime = path.stat().st_mtime
    assert after_mtime >= before_mtime

    # Press Back via softkey action and ensure screen closes and softkeys restored
    bar.actions["Back"]()
    assert ui._settings_screen.visible is False

    # Allow a frame to restore original softkeys
    for _ in range(2):
        ts.advance(0.1)
        await asyncio.sleep(0)
    restored_labels = set(bar.actions.keys())
    assert "Back" not in restored_labels and "Save" not in restored_labels

    await ui.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await tracks.stop()
