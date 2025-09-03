from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from pocketscope.core.events import EventBus
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.data.sectors import load_sectors_json
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource
from pocketscope.render.view_ppi import PpiView, TrackSnapshot


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(8192)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


async def _drain_playback(ts: SimTimeSource, src: FilePlaybackSource) -> None:
    # Advance sim time to process all events
    # Keep stepping to next due until none left
    while True:
        nxt = src.next_due_monotonic()
        if nxt is None:
            break
        now = ts.monotonic()
        dt = max(0.0, nxt - now)
        if dt == 0.0:
            # ensure loop progresses
            ts.advance(0.001)
        else:
            ts.advance(dt)
        # allow task scheduling
        await ts.sleep(0)


@pytest.mark.asyncio
async def test_render_golden(tmp_path: Path) -> None:
    # Ensure headless before importing pygame backend
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from pocketscope.platform.display.pygame_backend import PygameDisplayBackend

    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=100000)

    # Start services
    await tracks.run()
    src = FilePlaybackSource("tests/data/render_golden_aircraft.jsonl", ts=ts, bus=bus)
    task = __import__("asyncio").create_task(src.run())

    # Drain playback deterministically
    await _drain_playback(ts, src)

    # Build simple snapshots from TrackService state
    active = tracks.list_active()
    snaps: list[TrackSnapshot] = []
    center_lat, center_lon = (42.00748, -71.20899)
    for tr in active:
        if not tr.history:
            continue
        lat = tr.history[-1][1]
        lon = tr.history[-1][2]

        def _flt(v: object) -> float | None:
            return float(v) if isinstance(v, (int, float)) else None

        snaps.append(
            TrackSnapshot(
                icao=tr.icao24,
                lat=lat,
                lon=lon,
                callsign=tr.callsign,
                course_deg=_flt(tr.state.get("track_deg"))
                if "track_deg" in tr.state
                else None,
                trail_enu=None,
                geo_alt_ft=None,
                baro_alt_ft=_flt(tr.state.get("baro_alt"))
                if "baro_alt" in tr.state
                else None,
                ground_speed_kt=_flt(tr.state.get("ground_speed"))
                if "ground_speed" in tr.state
                else None,
                vertical_rate_fpm=_flt(tr.state.get("vertical_rate"))
                if "vertical_rate" in tr.state
                else None,
            )
        )

    # Prepare airport
    airports_tuples = [
        (42.0008792, -71.1980411, "1B9"),  # Label should be offset
        (42.12, -71.00, "BED"),  # Label should be default NE
    ]

    # Load sample sectors (2 polygons)
    sectors = load_sectors_json("tests/data/render_golden_sectors.json")

    # Render a frame
    display = PygameDisplayBackend(size=(320, 480))
    view = PpiView(range_nm=20.0, show_data_blocks=False)
    canvas = display.begin_frame()
    view.draw(
        canvas,
        size_px=display.size(),
        center_lat=center_lat,
        center_lon=center_lon,
        tracks=snaps,
        airports=airports_tuples,
        sectors=sectors,
    )
    display.end_frame()

    # Save and assert hash
    out_path = Path(__file__).parent.parent / "out/golden_render.png"
    display.save_png(str(out_path))

    digest = _sha256_file(str(out_path))
    expected = "f1616ab738ba299ae593fafad0065db0edcb90fafa65e2c176501711fe717aae"
    assert digest == expected

    # Cleanup
    await src.stop()
    await tracks.stop()
    await task
