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
from pocketscope.ui.controllers import UiConfig, UiController


@pytest.mark.asyncio
async def test_settings_mouse_click_activate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open settings screen and activate first two rows via mouse clicks.

    With the deferred persistence model the clicks modify in-memory
    settings but do not create / write the settings file until the
    Save softkey is invoked. The test verifies staging (no file), then
    triggers an explicit save via softkey actions and validates that
    the updated values are persisted.
    """
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
        cfg=UiConfig(target_fps=15.0, range_nm=10.0),
    )

    # Attach softkey bar to expose Save action when settings visible
    from pocketscope.ui.softkeys import SoftKeyBar

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

    # Allow a couple frames
    for _ in range(2):
        ts.advance(0.1)
        await asyncio.sleep(0)

    # Open settings screen
    ui._settings_screen.visible = True  # type: ignore[attr-defined]

    # Let layout frame render
    for _ in range(1):
        ts.advance(0.1)
        await asyncio.sleep(0)

    # Compute click positions based on geometry in SettingsScreen.draw
    font_px = ui._settings_screen.font_px  # type: ignore[attr-defined]
    title_h = int(font_px + 8)
    row_h = int(font_px + 6)
    start_y = title_h + 2

    import pygame as pg

    # Click first row (Units) twice to cycle value away from default
    y_units = start_y + row_h // 2
    for _ in range(2):
        pg.event.post(pg.event.Event(pg.MOUSEBUTTONDOWN, pos=(10, y_units), button=1))
        ts.advance(0.05)
        await asyncio.sleep(0)

    # Click Range Default row once
    y_range = start_y + row_h + row_h // 2
    pg.event.post(pg.event.Event(pg.MOUSEBUTTONDOWN, pos=(10, y_range), button=1))
    ts.advance(0.05)
    await asyncio.sleep(0)

    # No file should exist yet (deferred until Save)
    path = SettingsStore.settings_path()
    assert not path.exists()

    # Invoke Save via softkey mapping (ensure mapping installed first)
    ui._settings_screen.visible = True  # ensure visible to expose Back/Save
    ui._sync_softkeys()  # type: ignore[attr-defined]
    bar = ui._softkeys  # type: ignore[attr-defined]
    assert bar is not None
    # Force Save action
    bar.actions["Save"]()
    await asyncio.sleep(0)

    data = json.loads(path.read_text())
    assert data["units"] in {"mi_ft_mph", "km_m_kmh"}
    assert data["range_nm"] in {2.0, 5.0, 10.0, 20.0, 40.0, 80.0}

    await ui.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await tracks.stop()
