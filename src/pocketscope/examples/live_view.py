"""
Live desktop viewer for dump1090 JSON traffic.

Defaults to full ATC-style three-line data blocks with leader lines. Use
``--simple`` to show minimal one-line labels instead. Typography can be
customized via ``--font-px`` and ``--block-line-gap-px``.

How to run:
    python -m pocketscope.examples.live_view \
            --url https://adsb.chrispatten.dev/data/aircraft.json \
            --center 42.00748,-71.20899 \
            --range 20

Optional label switches:
    # Minimal labels instead of full data blocks
    python -m pocketscope.examples.live_view --simple

    # Tweak data block typography
    python -m pocketscope.examples.live_view \
            --font-px 12 --block-line-gap-px -5

This opens a Pygame window and renders live traffic centered on the given
coordinates. Press Ctrl+C to exit.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from pocketscope.core.events import EventBus
from pocketscope.core.geo import ecef_to_enu, geodetic_to_ecef
from pocketscope.core.models import AircraftTrack
from pocketscope.core.time import RealTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.data.airports import load_airports_json
from pocketscope.data.sectors import load_sectors_json
from pocketscope.ingest.adsb.json_source import Dump1090JsonSource
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView, TrackSnapshot
from pocketscope.ui.controllers import UiConfig, UiController


def _make_snapshots(
    tracks: Iterable[AircraftTrack], center_lat: float, center_lon: float
) -> list[TrackSnapshot]:
    out: list[TrackSnapshot] = []
    # Precompute center ECEF
    _cx, _cy, _cz = geodetic_to_ecef(center_lat, center_lon, 0.0)
    for tr in tracks:
        # Use last known lat/lon if present
        latest_lat = None
        latest_lon = None
        if tr.history:
            latest_lat = tr.history[-1][1]
            latest_lon = tr.history[-1][2]
        # Fall back to no position if unknown
        if latest_lat is None or latest_lon is None:
            continue

        # Build simple ENU trail from history
        enu_trail: list[tuple[float, float]] = []
        for _, lat, lon, _alt in tr.history[-60:]:  # limit to last ~60 samples
            tx, ty, tz = geodetic_to_ecef(lat, lon, 0.0)
            e, n, _ = ecef_to_enu(tx, ty, tz, center_lat, center_lon, 0.0)
            enu_trail.append((e, n))

        course = None
        if "track_deg" in tr.state and isinstance(tr.state["track_deg"], (int, float)):
            course = float(tr.state["track_deg"])

        # Kinematics for labels
        _geo = tr.state.get("geo_alt")
        geo_alt = float(_geo) if isinstance(_geo, (int, float)) else None
        _baro = tr.state.get("baro_alt")
        baro_alt = float(_baro) if isinstance(_baro, (int, float)) else None
        _gs = tr.state.get("ground_speed")
        gs = float(_gs) if isinstance(_gs, (int, float)) else None
        _vr = tr.state.get("vertical_rate")
        vr = float(_vr) if isinstance(_vr, (int, float)) else None

        out.append(
            TrackSnapshot(
                icao=tr.icao24,
                lat=latest_lat,
                lon=latest_lon,
                callsign=tr.callsign,
                course_deg=course,
                trail_enu=enu_trail,
                geo_alt_ft=geo_alt,
                baro_alt_ft=baro_alt,
                ground_speed_kt=gs,
                vertical_rate_fpm=vr,
            )
        )
    return out


def _print_help() -> None:
    print("Keys: [ / - = zoom out, ] / = zoom in, o overlay, q/ESC quit")


async def main_async(args: argparse.Namespace) -> None:
    ts = RealTimeSource()
    bus = EventBus()
    tracks = TrackService(bus, ts)

    # Source selection
    class _Source(Protocol):
        async def run(self) -> None:
            ...

        async def stop(self) -> None:
            ...

    src: _Source
    if args.playback:
        src = FilePlaybackSource(args.playback, ts=ts, bus=bus, speed=1.0, loop=True)
    else:
        src = Dump1090JsonSource(args.url, bus=bus, poll_hz=1.0)

    # Open a window (portrait)
    display = PygameDisplayBackend(size=(480, 800), create_window=True)
    view = PpiView(
        show_data_blocks=not bool(args.simple),
        label_font_px=args.font_px,
        label_line_gap_px=args.block_line_gap_px,
    )

    airports = None
    sectors = None
    airports_path: str | None = None
    if args.airports:
        airports_path = args.airports
    else:
        # Try workspace sample_data/airports.json automatically
        try_default1 = (
            Path(__file__).resolve().parents[3] / "sample_data" / "airports.json"
        )
        try_default2 = Path.cwd() / "sample_data" / "airports.json"
        if try_default1.exists():
            airports_path = str(try_default1)
        elif try_default2.exists():
            airports_path = str(try_default2)

    if airports_path:
        try:
            aps = load_airports_json(airports_path)
            airports = [(ap.lat, ap.lon, ap.ident) for ap in aps]
        except Exception as e:
            print(f"[live_view] Failed to load airports: {e}")

    # Sectors: optional path, default to sample_data/artcc.json if present
    sectors_path: str | None = None
    if args.sectors:
        sectors_path = args.sectors
    else:
        try_default1 = (
            Path(__file__).resolve().parents[3] / "sample_data" / "artcc.json"
        )
        try_default2 = Path.cwd() / "sample_data" / "artcc.json"
        if try_default1.exists():
            sectors_path = str(try_default1)
        elif try_default2.exists():
            sectors_path = str(try_default2)

    if sectors_path:
        try:
            secs = load_sectors_json(sectors_path)
            sectors = secs
        except Exception as e:
            print(f"[live_view] Failed to load sectors: {e}")

    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(range_nm=float(args.range), overlay=True, target_fps=30.0),
        center_lat=float(args.center[0]),
        center_lon=float(args.center[1]),
        airports=airports,
        sectors=sectors,
        font_px=args.font_px,
    )

    _print_help()
    # Start track maintenance (spawns internal tasks and returns immediately).
    await tracks.run()

    # Run UI and source concurrently; stop others when one exits.
    src_task = asyncio.create_task(src.run(), name="adsb_source")
    ui_task = asyncio.create_task(ui.run(), name="ui")
    try:
        done, pending = await asyncio.wait(
            {src_task, ui_task}, return_when=asyncio.FIRST_COMPLETED
        )

        # If UI finished (user quit), stop source and tracks.
        if ui_task in done:
            await src.stop()
            await tracks.stop()
        # If source finished first (error or end), stop UI as well.
        if src_task in done:
            await ui.stop()

        # Await remaining tasks and swallow exceptions to ensure cleanup.
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        # Best-effort final cleanup.
        await tracks.stop()
        await src.stop()
        for t in (src_task, ui_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(src_task, ui_task, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PocketScope Live Viewer")
    p.add_argument(
        "--url",
        default="http://127.0.0.1:8080/data/aircraft.json",
        help="dump1090 aircraft.json URL",
    )
    p.add_argument(
        "--playback",
        type=str,
        default=None,
        help="Path to JSONL ADS-B trace for local playback (overrides --url)",
    )
    p.add_argument(
        "--center",
        type=lambda s: tuple(map(float, s.split(","))),
        default=(42.00748, -71.20899),
        help="Center lat,lon",
    )
    p.add_argument(
        "--range",
        type=float,
        default=20.0,
        help="Range in NM",
    )
    p.add_argument(
        "--simple",
        action="store_true",
        help="Show simple labels instead of ATC-style three-line data blocks",
    )
    p.add_argument(
        "--font-px",
        dest="font_px",
        type=int,
        default=12,
        help="Font size in px for all text on canvas (default: 12)",
    )
    p.add_argument(
        "--block-line-gap-px",
        dest="block_line_gap_px",
        type=int,
        default=-5,
        help="Additional gap between data block lines in px (default: -5)",
    )
    p.add_argument(
        "--airports",
        type=str,
        default=None,
        help=(
            "Path to airports.json; defaults to sample_data/airports.json if present"
        ),
    )
    p.add_argument(
        "--sectors",
        type=str,
        default=None,
        help=(
            "Path to sectors file (simple JSON or GeoJSON FeatureCollection);"
            " defaults to sample_data/artcc.json if present"
        ),
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
