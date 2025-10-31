"""Live desktop viewer for dump1090 JSON traffic (application entrypoint).

This module was moved from ``pocketscope.examples``. It provides the
async `main_async` entry and the synchronous `main()` helper.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, Type

from pocketscope.core.events import EventBus
from pocketscope.core.geo import ecef_to_enu, geodetic_to_ecef
from pocketscope.core.models import AircraftTrack
from pocketscope.core.time import RealTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.data.cache import LRUCache
from pocketscope.data.sectors import load_sectors_json
from pocketscope.ingest.adsb.json_source import Dump1090JsonSource
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource
from pocketscope.map.data_provider import MapDataProvider
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.platform.display.web_backend import WebDisplayBackend
from pocketscope.render.view_ppi import PpiView, TrackSnapshot
from pocketscope.settings.store import SettingsStore
from pocketscope.theme import ThemeManager
from pocketscope.tools.config_watcher import ConfigWatcher
from pocketscope.ui.controllers import UiConfig, UiController
from pocketscope.ui.softkeys import SoftKeyBar


# Protocol describing the minimal interface required from an ADS-B source
class SourceProtocol(Protocol):
    async def run(self) -> None:
        ...

    async def stop(self) -> None:
        ...


def _make_snapshots(tracks: Iterable[AircraftTrack], center_lat: float, center_lon: float) -> list[TrackSnapshot]:
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
    tracks = TrackService(bus, ts, expiry_s=300.0)

    # Source selection
    src: SourceProtocol
    if args.playback:
        src = FilePlaybackSource(args.playback, ts=ts, bus=bus, speed=1.0, loop=True)
    else:
        src = Dump1090JsonSource(args.url, bus=bus, poll_hz=1.0)

    # Open a window (portrait)
    # Optionally expose the view over HTTP for a simple browser UI
    # Optional TFT + touch support when running on embedded hardware
    # Annotate optional backend class references so runtime assignment to
    # None is allowed and mypy understands the intended type.
    # Use Type[Any] here to avoid referencing platform-specific classes at
    # module runtime; concrete classes are imported inside the TYPE_CHECKING
    # or runtime try/except blocks below.
    _ILI9341Cls: Type[Any] | None
    _TouchCls: Type[Any] | None

    try:
        if TYPE_CHECKING:  # pragma: no cover - typing only
            from pocketscope.platform.display.ili9341_backend import (
                ILI9341DisplayBackend as _ILI9341Cls,
            )
            from pocketscope.platform.input.xpt2046_touch import (
                XPT2046Touch as _TouchCls,
            )

            print("[live_view] TYPE_CHECKING mode active")
        else:
            try:
                from pocketscope.platform.display.ili9341_backend import (
                    ILI9341DisplayBackend as _ILI9341Cls,
                )

                print("[live_view] ILI9341 backend available")
            except Exception:
                _ILI9341Cls = None
                print("[live_view] ILI9341 backend NOT available")
            try:
                from pocketscope.platform.input.xpt2046_touch import (
                    XPT2046Touch as _TouchCls,
                )

                print("[live_view] XPT2046 touch available")
            except Exception:
                _TouchCls = None
                print("[live_view] XPT2046 touch NOT available")
    except Exception:
        _ILI9341Cls = None
        _TouchCls = None
        print("[live_view] ILI9341 backend NOT available")
        print("[live_view] XPT2046 touch NOT available")

    display: PygameDisplayBackend | WebDisplayBackend | Any
    touch: Any | None = None
    touch_task: asyncio.Task[Any] | None = None
    touch_forwarder_task: asyncio.Task[Any] | None = None
    if getattr(args, "tft", False) and _ILI9341Cls is not None:
        # Physical TFT portrait 240x320
        display = _ILI9341Cls(width=240, height=320)
        if _TouchCls is not None:
            touch = _TouchCls(width=240, height=320, poll_hz=float(args.touch_hz))
            _run = getattr(touch, "run", None)
            if callable(_run):
                try:
                    maybe_coro = _run()
                    if maybe_coro is not None and hasattr(maybe_coro, "__await__"):
                        # Narrow type: ensure we treat as coroutine
                        touch_task = asyncio.create_task(maybe_coro, name="touch.run")
                        try:
                            touch._task = touch_task
                        except Exception:
                            pass
                except Exception:
                    pass
        print("[live_view] TFT mode active (ILI9341 + XPT2046)")
    elif args.web_ui:
        # Use persisted settings to allow customizing the web UI resolution.
        try:
            settings = SettingsStore.load()
            w = int(getattr(settings, "web_ui_width", 1280))
            h = int(getattr(settings, "web_ui_height", 800))
            # defensive bounds
            if w <= 0 or h <= 0:
                raise ValueError("invalid web_ui dimensions")
            display = WebDisplayBackend(size=(w, h), create_window=False)
        except Exception:
            # Fall back to historical default if settings cannot be loaded.
            display = WebDisplayBackend(size=(1280, 800), create_window=False)
        print("[live_view] Web UI mode active")
    else:
        display = PygameDisplayBackend(size=(480, 800), create_window=True)
        print("[live_view] Pygame window mode active")
    view = PpiView(
        show_data_blocks=not bool(args.simple),
        label_font_px=args.font_px,
        label_line_gap_px=args.block_line_gap_px,
    )

    map_provider: MapDataProvider | None = None
    map_db_path = Path(args.map_db).expanduser()
    if map_db_path.exists():
        try:
            map_provider = MapDataProvider(str(map_db_path), cache=LRUCache())
        except Exception as e:
            print(f"[live_view] Failed to initialize map provider: {e}")
    else:
        print(f"[live_view] Map database not found: {map_db_path}")

    sectors = None

    # Sectors: optional path, default to sample_data/artcc.json if present
    sectors_path: str | None = None
    if args.sectors:
        sectors_path = args.sectors
    else:
        # Try package assets/us_states.json automatically (src/pocketscope/assets)
        try_default1 = Path(__file__).resolve().parents[1] / "assets" / "us_states.json"
        try_default2 = Path.cwd() / "src" / "pocketscope" / "assets" / "us_states.json"
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

    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(
            range_nm=float(args.range),
            overlay=True,
            target_fps=float(getattr(args, "fps", 30.0) or 30.0),
        ),
        center_lat=float(args.center[0]),
        center_lon=float(args.center[1]),
        sectors=sectors,
        font_px=args.font_px,
        map_provider=map_provider,
    )
    try:
        vprof_border = ThemeManager.color("vprof.border")
        vprof_border_color = (vprof_border[0], vprof_border[1], vprof_border[2], vprof_border[3])
    except Exception:
        vprof_border_color = (255, 255, 255, 255)

    bar = SoftKeyBar(
        display.size(),
        bar_height=60,
        pad_y=10,
        border_width=1,
        border_color=vprof_border_color,
        # Lightweight measurement when using TFT so we don't import pygame
        measure_fn=(lambda s, sz: (int(sz * 0.6) * len(s), sz)) if getattr(args, "tft", False) else None,
        actions={
            "-": ui.zoom_out,
            "Settings": lambda: None,
            "+": ui.zoom_in,
            # Screenshot softkey: only visible/useful on TFT but harmless elsewhere
            # (layout engine will size accordingly). Writes timestamped file to
            # ~/.pocketscope/screenshots and logs the destination.
            "Shot": lambda: print(f"[live_view] screenshot -> {ui.screenshot()}"),
        },
    )
    ui.set_softkeys(bar)

    # Install a SIGUSR1 handler to request screenshot when running as a service.
    def _sigusr1_handler(_sig: int, _frm: Any) -> None:  # pragma: no cover - signal path
        try:
            ui.request_screenshot()
        except Exception:
            pass

    try:
        signal.signal(getattr(signal, "SIGUSR1"), _sigusr1_handler)
    except Exception:
        pass
    watcher = ConfigWatcher(bus, poll_hz=2.0)
    asyncio.create_task(watcher.run())

    # If touch backend active, forward taps into the UI so physical
    # touches operate the softkeys / settings screen on embedded hardware.
    if touch is not None and hasattr(touch, "get_events"):

        async def _touch_forwarder() -> None:
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

                        def _dispatch_press(x: int, y: int) -> None:
                            consumed_local = False
                            # Settings overlay (legacy) takes precedence if present.
                            settings_screen = getattr(ui, "_settings_screen", None)
                            try:
                                if settings_screen is not None and getattr(settings_screen, "visible", False):
                                    try:
                                        consumed_local = settings_screen.on_mouse(x, y, display.size(), ui)
                                    except Exception:
                                        consumed_local = False
                            except Exception:
                                consumed_local = False
                            if consumed_local:
                                return
                            # Softkeys
                            try:
                                if ui._softkeys:
                                    ui._softkeys.on_mouse(x, y, True)
                            except Exception:
                                pass

                        if ev.type == "down":
                            _dispatch_press(ix, iy)
                            last_down_ts = float(ev.ts)
                            last_down_pos = (ix, iy)
                        elif ev.type == "tap":
                            recent = False
                            try:
                                if last_down_ts and last_down_pos:
                                    # consider same if within 0.25s and ~8px
                                    if float(ev.ts) - last_down_ts < 0.25 and (
                                        abs(last_down_pos[0] - ix) <= 8 and abs(last_down_pos[1] - iy) <= 8
                                    ):
                                        recent = True
                            except Exception:
                                recent = False
                            if not recent:
                                _dispatch_press(ix, iy)

                except Exception:
                    pass
                await asyncio.sleep(0.01)

        # Schedule forwarder after definition to satisfy type checker
        touch_forwarder_task = asyncio.create_task(_touch_forwarder(), name="touch.forwarder")

    _print_help()
    # Start track maintenance (spawns internal tasks and returns immediately).
    await tracks.run()

    # Run UI and source concurrently; stop others when one exits.
    src_task = asyncio.create_task(src.run(), name="adsb_source")
    ui_task = asyncio.create_task(ui.run(), name="ui")

    # Optional auto-halt for scripted diagnostics (e.g., collect ui.perf).
    run_seconds = getattr(args, "run_seconds", None)
    if isinstance(run_seconds, (int, float)) and run_seconds and run_seconds > 0:

        async def _auto_halt() -> None:
            try:
                await asyncio.sleep(float(run_seconds))
                await ui.stop()
            except Exception:
                pass

        asyncio.create_task(_auto_halt())
    try:
        done, pending = await asyncio.wait({src_task, ui_task}, return_when=asyncio.FIRST_COMPLETED)

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
        # Touch cleanup
        try:
            if touch is not None:
                stop_fn = getattr(touch, "stop", None)
                if callable(stop_fn):
                    try:
                        stop_fn()
                    except Exception:
                        pass
        except Exception:
            pass
        for _t in (touch_forwarder_task, touch_task):
            if _t and not _t.done():
                _t.cancel()
        if touch_task is not None:
            await asyncio.gather(
                *[t for t in (touch_task, touch_forwarder_task) if t],
                return_exceptions=True,
            )
        for t in (src_task, ui_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(src_task, ui_task, return_exceptions=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    When ``argv`` is None the values are read from ``sys.argv`` as usual.
    Accepting an ``argv`` list makes the parser testable programmatically.
    """
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
        "--fps",
        type=float,
        default=30.0,
        help="Target UI frames per second (default: 30.0)",
    )
    p.add_argument(
        "--run-seconds",
        dest="run_seconds",
        type=float,
        default=None,
        help="Automatically stop after N seconds (diagnostics / scripting)",
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
        "--map-db",
        type=str,
        default=str(Path.home() / ".pocketscope" / "pocketscope.db"),
        help="Path to PocketScope map SQLite database",
    )
    p.add_argument(
        "--sectors",
        type=str,
        default=None,
        help=(
            "Path to sectors file (simple JSON or GeoJSON FeatureCollection);"
            " defaults to assets/us_states.json if present"
        ),
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
        "--touch-hz",
        dest="touch_hz",
        type=float,
        default=180.0,
        help="Touch poll frequency when using --tft (default: 180.0)",
    )
    p.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        help="Run without GUI (lightweight mode suitable for CI/tests)",
    )
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    print(f"[live_view] Starting with args: {args}")
    try:
        if getattr(args, "headless", False):
            asyncio.run(_main_headless_async(args))
        else:
            asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


async def _main_headless_async(args: argparse.Namespace) -> None:
    """Lightweight headless runner for CI and tests.

    This starts the minimal services (time source, bus, track service,
    and data source) but does not create any display/UI. It runs briefly
    to allow startup logic to exercise without requiring a display.
    """
    ts = RealTimeSource()
    bus = EventBus()
    tracks = TrackService(bus, ts, expiry_s=300.0)

    # Choose source similarly to the UI runner but without display.
    src: SourceProtocol
    if args.playback:
        src = FilePlaybackSource(args.playback, ts=ts, bus=bus, speed=1.0, loop=False)
    else:
        src = Dump1090JsonSource(args.url, bus=bus, poll_hz=1.0)

    # Start services
    await tracks.run()
    src_task = asyncio.create_task(src.run(), name="adsb_source")

    # Run briefly to allow initialization; CI should use playback mode for
    # deterministic behavior. Keep runtime short to avoid long tests.
    try:
        await asyncio.sleep(0.1)
    finally:
        await src.stop()
        await tracks.stop()
        if not src_task.done():
            src_task.cancel()
        await asyncio.gather(src_task, return_exceptions=True)


if __name__ == "__main__":
    main()
