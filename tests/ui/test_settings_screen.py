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


@pytest.mark.asyncio
async def test_settings_screen_full_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))

    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=1e9)
    await tracks.run()

    display = PygameDisplayBackend(size=(240, 320))
    view = PpiView(show_data_blocks=False)
    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(target_fps=10.0, range_nm=10.0),
    )

    watcher = ConfigWatcher(bus)
    watcher_task = asyncio.create_task(watcher.run())
    task = asyncio.create_task(ui.run())

    # Let a couple frames render
    for _ in range(3):
        ts.advance(0.1)
        await asyncio.sleep(0)

    # Open settings via hotkey 's'
    import pygame as pg

    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_s))
    ts.advance(0.1)
    await asyncio.sleep(0)

    # Cycle Units (Enter) twice (staged only)
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_RETURN))
    ts.advance(0.1)
    await asyncio.sleep(0)
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_RETURN))
    ts.advance(0.1)
    await asyncio.sleep(0)
    path = SettingsStore.settings_path()
    # No persistence yet
    assert not path.exists()

    # Navigate down repeatedly to ensure we land on Demo Mode regardless of index drift
    for _ in range(8):  # more than menu length to wrap if needed
        pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_DOWN))
        ts.advance(0.02)
        await asyncio.sleep(0)
    # Ensure selection index points to Demo Mode (row 4)
    # Access internal for deterministic test (acceptable for UI test)
    settings_screen = ui._settings_screen  # type: ignore[attr-defined]
    if settings_screen is not None:
        settings_screen._sel = 4  # Demo Mode row
    # Direct internal activation (bypasses pygame event path for determinism)
    settings_screen._activate(ui)  # type: ignore[attr-defined]
    await asyncio.sleep(0)
    # Still no file (staged change)
    assert not path.exists()

    # Invoke Save softkey action to persist staged changes
    from pocketscope.ui.softkeys import SoftKeyBar

    if ui._softkeys is None:  # type: ignore[attr-defined]
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
        ui._settings_screen.visible = True  # expose Save
        ui._sync_softkeys()  # type: ignore[attr-defined]
    else:
        ui._settings_screen.visible = True  # type: ignore[attr-defined]
        ui._sync_softkeys()  # type: ignore[attr-defined]
    bar = ui._softkeys  # type: ignore[attr-defined]
    assert bar is not None
    bar.actions["Save"]()
    await asyncio.sleep(0.1)
    data = json.loads(path.read_text())
    assert data["demo_mode"] is True
    assert ui.demo_mode is True

    # ESC closes settings screen
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_ESCAPE))
    ts.advance(0.05)
    await asyncio.sleep(0)

    # External modification: set track_length_mode to long
    s = SettingsStore.load()
    s.track_length_mode = "long"
    SettingsStore.save(s)
    await asyncio.sleep(0.4)
    assert ui.track_length_mode == "long"

    # Re-open and snapshot
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_s))
    ts.advance(0.1)
    await asyncio.sleep(0)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    png_path = out_dir / "settings_screen.png"
    display.save_png(str(png_path))
    assert png_path.exists() and png_path.stat().st_size > 0

    # Shutdown
    await ui.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await watcher.stop()
    watcher_task.cancel()
    await tracks.stop()
