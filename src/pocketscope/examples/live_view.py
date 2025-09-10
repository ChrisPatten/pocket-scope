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
from typing import TYPE_CHECKING, Any, Awaitable, Optional, Protocol, cast

from pocketscope.config import make_ui_config
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
from pocketscope.platform.display.web_backend import WebDisplayBackend
from pocketscope.render.canvas import DisplayBackend
from pocketscope.render.view_ppi import PpiView, TrackSnapshot
from pocketscope.tools.config_watcher import ConfigWatcher
from pocketscope.ui.controllers import UiConfig, UiController
from pocketscope.ui.softkeys import SoftKeyBar

if TYPE_CHECKING:  # pragma: no cover - for static type checking only
    from pocketscope.platform.display.ili9341_backend import (
        ILI9341DisplayBackend as _ILI9341Cls,
    )
    from pocketscope.platform.input.xpt2046_touch import XPT2046Touch as _TouchCls
else:  # runtime optional imports with graceful fallback
    try:  # optional on desktop / hardware
        from pocketscope.platform.display.ili9341_backend import (
            ILI9341DisplayBackend as _ILI9341Cls,
        )
    except Exception:  # pragma: no cover
        _ILI9341Cls = None
    try:
        from pocketscope.platform.input.xpt2046_touch import XPT2046Touch as _TouchCls
    except Exception:  # pragma: no cover
        _TouchCls = None

# Public optional names (narrow types when available)
ILI9341DisplayBackend: Optional[type[Any]] = _ILI9341Cls
XPT2046Touch: Optional[type[Any]] = _TouchCls
## (imports moved to top for lint compliance)


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
    tracks = TrackService(bus, ts, expiry_s=300.0, sweep_interval_s=10.0)

    # Source selection
    class _Source(Protocol):
        async def run(self) -> None:
            ...

        async def stop(self) -> None:
            ...

    src: _Source
    if args.playback:
        src = FilePlaybackSource(args.playback, ts=ts, bus=bus, speed=1.0, loop=True)
    elif getattr(args, "local_json", None):
        # Local JSON file polled as data source (refresh every second)
        from pocketscope.ingest.adsb import LocalJsonFileSource

        src = LocalJsonFileSource(args.local_json, bus=bus, poll_hz=1.0)
    else:
        src = Dump1090JsonSource(
            args.url, bus=bus, poll_hz=1.0, main_loop=asyncio.get_running_loop()
        )

    # Open a window (portrait) and optionally expose the view over HTTP.
    display: DisplayBackend
    touch: Any | None = None
    if getattr(args, "tft", False) and ILI9341DisplayBackend is not None:
        # Physical TFT portrait 240x320 (rotate logical view if desired later)
        display = ILI9341DisplayBackend(width=240, height=320)
        if XPT2046Touch is not None:  # pragma: no branch - simple availability
            # Use elevated poll rate for lower latency taps (default overridable
            # via --touch-hz). Higher Hz reduces chance a very quick tap occurs
            # entirely between polls and gets missed.
            touch = XPT2046Touch(width=240, height=320, poll_hz=float(args.touch_hz))
            _run = getattr(touch, "run", None)
            if callable(_run):
                try:
                    maybe_coro = _run()
                except Exception:
                    maybe_coro = None
                if maybe_coro is not None and hasattr(maybe_coro, "__await__"):
                    asyncio.create_task(
                        cast(Awaitable[Any], maybe_coro)  # type: ignore[arg-type]
                    )
        print("[live_view] TFT mode active (ILI9341 + XPT2046)")
    elif args.web_ui:
        display = WebDisplayBackend(size=(1280, 800), create_window=False)
    else:
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
            secs = load_sectors_json(
                sectors_path,
                center_lat=float(args.center[0]),
                center_lon=float(args.center[1]),
                range_nm=float(args.range),
                cull_factor=2.0,
            )
            sectors = secs
        except Exception as e:
            print(f"[live_view] Failed to load sectors: {e}")

    # Build runtime config (values <- persisted settings <- CLI args)
    rc = make_ui_config(args=args)
    # Convert config.UIData -> UiConfig used by controller
    ui_cfg = UiConfig(
        range_nm=float(rc.ui.range_nm),
        min_range_nm=float(rc.ui.min_range_nm),
        max_range_nm=float(rc.ui.max_range_nm),
        target_fps=float(rc.ui.target_fps),
        overlay=bool(rc.ui.overlay),
    )

    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=ui_cfg,
        center_lat=float(args.center[0]),
        center_lon=float(args.center[1]),
        airports=airports,
        sectors=sectors,
        font_px=args.font_px,
    )
    bar = SoftKeyBar(
        display.size(),
        bar_height=60,
        pad_y=10,
        border_width=0,
        # Provide a lightweight measurement function on TFT so we never
        # attempt to import pygame for text metrics on embedded hardware.
        measure_fn=(
            (lambda s, sz: (int(sz * 0.6) * len(s), sz))
            if getattr(args, "tft", False)
            else None
        ),
        actions={
            "-": ui.zoom_out,
            "Settings": lambda: None,
            "+": ui.zoom_in,
        },
    )
    ui.set_softkeys(bar)
    # If touch backend active, forward taps into the UI so physical
    # touches operate the softkeys / settings screen when running on
    # embedded hardware (ILI9341 + XPT2046). Fall back to a simple
    # logger when forwarding fails for any reason.
    if touch is not None and hasattr(touch, "get_events"):

        async def _touch_forwarder() -> None:
            # Forward only initial "down" events (touch start) to achieve
            # immediate activation with no toggle-on-release. This mirrors
            # desktop behavior where activation is on mouse-down. High
            # poll rate (see --touch-hz) minimizes missed very short taps.
            # If a very quick tap occurs entirely between polls and only a
            # synthesized "tap" event is emitted (no preceding "down" seen),
            # fall back to activating on that tap so the user doesn't have to
            # hold. We track the last forwarded down to avoid double firing.
            last_down_ts: float = 0.0
            last_down_pos: tuple[int, int] | None = None
            while True:
                try:
                    for ev in touch.get_events():
                        try:
                            print(f"[touch] {ev.type} {ev.x},{ev.y} ts={ev.ts:.3f}")
                        except Exception:
                            pass
                        ix = int(ev.x)
                        iy = int(ev.y)

                        # Helper to dispatch a press with synthetic release
                        def _dispatch_press(x: int, y: int) -> None:
                            consumed_local = False
                            if ui._settings_screen.visible:
                                try:
                                    consumed_local = ui._settings_screen.on_mouse(
                                        x, y, ui._display.size(), ui
                                    )
                                except Exception:
                                    consumed_local = False
                            if consumed_local:
                                return
                            if ui._softkeys:
                                try:
                                    ui._softkeys.on_mouse(x, y, True)
                                    # Log which softkey (debug aid for +/-)
                                    try:
                                        # _hit may be internal; best-effort call
                                        lbl = ui._softkeys._hit(x, y)
                                        if lbl:
                                            # Split long debug line for lint (ruff E501)
                                            part1 = f"[touch] softkey '{lbl}' activated"
                                            part2 = f"at {x},{y}"
                                            print(part1 + " " + part2)
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                                # Synthetic quick release (pressed=False)
                                try:

                                    async def _rel() -> None:
                                        await asyncio.sleep(0.04)
                                        try:
                                            if ui._softkeys:
                                                ui._softkeys.on_mouse(x, y, False)
                                        except Exception:
                                            pass

                                    asyncio.create_task(_rel())
                                except Exception:
                                    pass
                            # Sync mapping immediately (Settings may have opened)
                            try:
                                ui._sync_softkeys()
                            except Exception:
                                pass

                        if ev.type == "down":
                            _dispatch_press(ix, iy)
                            last_down_ts = float(ev.ts)
                            last_down_pos = (ix, iy)
                        elif ev.type == "tap":
                            # Fire only if we did not just forward a down for
                            # the same spatial/temporal sequence.
                            recent = False
                            if last_down_pos is not None:
                                if (float(ev.ts) - last_down_ts) < 0.5:
                                    dx = abs(last_down_pos[0] - ix)
                                    dy = abs(last_down_pos[1] - iy)
                                    if dx <= 8 and dy <= 8:
                                        recent = True
                            if not recent:
                                _dispatch_press(ix, iy)
                                last_down_ts = float(ev.ts)
                                last_down_pos = (ix, iy)
                except Exception:
                    pass
                # Tight loop paced lightly; high poll_hz already controls
                # sampling latency so a very small sleep avoids busy-spin.
                await asyncio.sleep(0.01)

        asyncio.create_task(_touch_forwarder())

    # Start background services
    config_watcher = ConfigWatcher(bus)
    asyncio.create_task(config_watcher.run())
    await tracks.run()

    _print_help()
    # Start track maintenance (spawns internal tasks and returns immediately).

    # Run UI and source concurrently; stop others when one exits.
    src_task = asyncio.create_task(src.run(), name="adsb_source")
    ui_task = asyncio.create_task(ui.run(), name="ui")
    try:
        done, pending = await asyncio.wait(
            {src_task, ui_task}, return_when=asyncio.FIRST_COMPLETED
        )

        # If UI finished (user quit), stop source and tracks.
        if ui_task in done:
            if (exc := ui_task.exception()) is not None:
                print(f"[live_view] UI task error: {exc}")
            await src.stop()
            await tracks.stop()
        # If source finished first (error or end), stop UI as well.
        if src_task in done:
            if (exc := src_task.exception()) is not None:
                print(f"[live_view] Source task error: {exc}")
            await ui.stop()

        # Await remaining tasks and swallow exceptions to ensure cleanup.
        results = await asyncio.gather(*pending, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                print(f"[live_view] pending task error: {r}")
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
    p.add_argument(
        "--local-json",
        dest="local_json",
        type=str,
        default=None,
        help=("Path to a local dump1090-style JSON file to poll every second"),
    )
    p.add_argument(
        "--web-ui",
        dest="web_ui",
        action="store_true",
        help="Serve a minimal web UI at http://127.0.0.1:8000/ displaying the view",
    )
    p.add_argument(
        "--tft",
        dest="tft",
        action="store_true",
        help="Use SPI TFT (ILI9341) + touch (XPT2046) instead of pygame",
    )
    p.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="Target frames per second for display updates (default: 30.0)",
    )
    p.add_argument(
        "--touch-hz",
        dest="touch_hz",
        type=float,
        default=180.0,
        help="Touch poll frequency when using --tft (default: 180.0)",
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
