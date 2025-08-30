"""Headless golden-frame test for PPI rendering using pygame backend.

This test replays a tiny ADS-B trace via SimTimeSource/EventBus/TrackService,
renders a portrait 320x480 PPI, saves PNG, and compares SHA-256 hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import List, Tuple

import pytest

from pocketscope.core.events import EventBus
from pocketscope.core.geo import ecef_to_enu, geodetic_to_ecef
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource
from pocketscope.render.view_ppi import PpiView, TrackSnapshot

TEST_DATA_DIR = Path(__file__).parent.parent / "data"
TRACE_PATH = TEST_DATA_DIR / "adsb_trace_ppi.jsonl"
OUT_DIR = Path(__file__).parent.parent / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(8192)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@pytest.mark.asyncio
async def test_golden_ppi() -> None:
    # Setup bus, time, track service
    bus = EventBus()
    ts = SimTimeSource(start=0.0)
    track_svc = TrackService(bus, ts, expiry_s=1e9)
    await track_svc.run()

    # Create tiny trace file if not present
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRACE_PATH.exists():
        events = [
            {
                "t_mono": 0.0,
                "msg": {
                    "icao24": "abc123",
                    "callsign": "ALPHA1",
                    "lat": 42.0,
                    "lon": -71.0,
                    "baro_alt": 30000,
                    "ground_speed": 400,
                    "track_deg": 45.0,
                    "src": "PLAYBACK",
                },
            },
            {
                "t_mono": 0.4,
                "msg": {
                    "icao24": "def456",
                    "callsign": "BRAVO2",
                    "lat": 42.02,
                    "lon": -71.03,
                    "baro_alt": 25000,
                    "ground_speed": 350,
                    "track_deg": 120.0,
                    "src": "PLAYBACK",
                },
            },
            {
                "t_mono": 1.0,
                "msg": {
                    "icao24": "abc123",
                    "lat": 42.03,
                    "lon": -71.04,
                    "baro_alt": 30100,
                    "ground_speed": 405,
                    "track_deg": 47.0,
                    "src": "PLAYBACK",
                },
            },
        ]
        with open(TRACE_PATH, "w", encoding="utf-8") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

    # Start playback
    src = FilePlaybackSource(str(TRACE_PATH), ts=ts, bus=bus, speed=1.0)
    asyncio.create_task(src.run())
    await asyncio.sleep(0)  # allow immediate event

    # Advance to subsequent events
    for _ in range(4):
        nd = src.next_due_monotonic()
        if nd is None:
            break
        ts.set_time(nd)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # Build snapshots from TrackService
    tracks = track_svc.list_active()
    assert tracks, "No tracks after playback"

    center_lat, center_lon = 42.0, -71.0

    # Build ENU trail for each track (downsample to <= 50)
    snapshots: List[TrackSnapshot] = []
    _ox, _oy, _oz = geodetic_to_ecef(center_lat, center_lon, 0.0)

    for tr in tracks:
        if not tr.history:
            # Skip tracks without position
            continue
        # Latest
        last = tr.history[-1]
        lat, lon = float(last[1]), float(last[2])
        callsign = tr.callsign
        course = None
        v = tr.state.get("track_deg")
        if isinstance(v, (int, float)):
            course = float(v)

        # Trail
        pts = tr.history[-50:]
        trail_enu: List[Tuple[float, float]] = []
        for _, la, lo, _ in pts:
            tx, ty, tz = geodetic_to_ecef(float(la), float(lo), 0.0)
            e, n, _ = ecef_to_enu(tx, ty, tz, center_lat, center_lon, 0.0)
            trail_enu.append((e, n))

        snapshots.append(
            TrackSnapshot(
                icao=tr.icao24,
                lat=lat,
                lon=lon,
                callsign=callsign,
                course_deg=course,
                trail_enu=trail_enu,
            )
        )

    # Render
    # Ensure headless before importing pygame backend
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from pocketscope.platform.display.pygame_backend import PygameDisplayBackend

    backend = PygameDisplayBackend(size=(320, 480))
    canvas = backend.begin_frame()
    PpiView(range_nm=10.0).draw(
        canvas,
        size_px=backend.size(),
        center_lat=center_lat,
        center_lon=center_lon,
        tracks=snapshots,
        airports=None,
    )
    backend.end_frame()
    out_path = OUT_DIR / "golden_ppi.png"
    backend.save_png(str(out_path))

    # Hash and assert determinism
    digest = sha256_file(out_path)
    GOLDEN_SHA256 = "7de86c8d89f34990887f7f1ea35e8014074d6295bd8f68be471b2d1120bec6d8"
    assert digest == GOLDEN_SHA256

    await track_svc.stop()


def test_input_smoke() -> None:
    # Ensure pygame loads in dummy mode and import backend/input lazily
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
    from pocketscope.platform.input.pygame_input import PygameInputBackend

    backend = PygameDisplayBackend(size=(100, 100))
    inp = PygameInputBackend()

    # Synthesize a mouse click at center using pygame event API
    import pygame

    w, h = backend.size()
    center = (w // 2, h // 2)
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": center}))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": center}))

    events = list(inp.pump())
    # Expect at least a tap event
    assert any(
        e.type == "tap" and abs(e.x - center[0]) <= 1 and abs(e.y - center[1]) <= 1
        for e in events
    )
