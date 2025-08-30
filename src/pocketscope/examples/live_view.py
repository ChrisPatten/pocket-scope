"""
Live desktop viewer for dump1090 JSON traffic.

Defaults to full ATC-style three-line data blocks with leader lines. Use
``--simple`` to show minimal one-line labels instead. Typography can be
customized via ``--block-font-px`` and ``--block-line-gap-px``.

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
            --block-font-px 12 --block-line-gap-px -5

This opens a Pygame window and renders live traffic centered on the given
coordinates. Press Ctrl+C to exit.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable

from pocketscope.core.events import EventBus
from pocketscope.core.geo import ecef_to_enu, geodetic_to_ecef
from pocketscope.core.models import AircraftTrack
from pocketscope.core.time import RealTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.ingest.adsb.json_source import Dump1090JsonSource
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView, TrackSnapshot


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


async def render_loop(
    display: PygameDisplayBackend,
    view: PpiView,
    tracks_service: TrackService,
    *,
    center_lat: float,
    center_lon: float,
    range_nm: float,
) -> None:
    # Fixed 30 FPS
    dt = 1.0 / 30.0
    while True:
        canvas = display.begin_frame()
        # Snapshot tracks and draw
        active = tracks_service.list_active()
        snapshots = _make_snapshots(active, center_lat, center_lon)
        view.range_nm = range_nm
        view.draw(
            canvas,
            size_px=display.size(),
            center_lat=center_lat,
            center_lon=center_lon,
            tracks=snapshots,
        )
        display.end_frame()
        await asyncio.sleep(dt)


async def main_async(args: argparse.Namespace) -> None:
    ts = RealTimeSource()
    bus = EventBus()
    tracks = TrackService(bus, ts)
    src = Dump1090JsonSource(args.url, bus=bus, poll_hz=5.0)

    # Open a window (portrait)
    display = PygameDisplayBackend(size=(480, 800), create_window=True)
    view = PpiView(
        show_data_blocks=not bool(args.simple),
        label_font_px=args.block_font_px,
        label_line_gap_px=args.block_line_gap_px,
    )

    await asyncio.gather(
        src.run(),
        tracks.run(),
        render_loop(
            display,
            view,
            tracks,
            center_lat=args.center[0],
            center_lon=args.center[1],
            range_nm=args.range,
        ),
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PocketScope Live Viewer")
    p.add_argument(
        "--url",
        default="https://adsb.chrispatten.dev/data/aircraft.json",
        help="dump1090 aircraft.json URL",
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
        "--block-font-px",
        dest="block_font_px",
        type=int,
        default=12,
        help="Data block font size in px (default: 12)",
    )
    p.add_argument(
        "--block-line-gap-px",
        dest="block_line_gap_px",
        type=int,
        default=-5,
        help="Additional gap between data block lines in px (default: -5)",
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
