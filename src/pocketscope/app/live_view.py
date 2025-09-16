"""Live desktop viewer for dump1090 JSON traffic (application entrypoint).

This module was moved from ``pocketscope.examples``. It provides the
async `main_async` entry and the synchronous `main()` helper.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Protocol, Type, cast

from pocketscope.core.events import EventBus
from pocketscope.core.geo import ecef_to_enu, geodetic_to_ecef
from pocketscope.core.models import AircraftTrack
from pocketscope.core.time import RealTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.data.base_map import BaseMap
from pocketscope.ingest.adsb.json_source import Dump1090JsonSource
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.platform.display.web_backend import WebDisplayBackend
from pocketscope.render.view_ppi import PpiView, TrackSnapshot
from pocketscope.settings.store import SettingsStore
from pocketscope.tools.config_watcher import ConfigWatcher
from pocketscope.ui.autoscale_controller import AutoScaleController, AutoscaleSettings
from pocketscope.ui.controllers import UiConfig, UiController
from pocketscope.ui.softkeys import SoftKeyBar


# Protocol describing the minimal interface required from an ADS-B source
class SourceProtocol(Protocol):
    async def run(self) -> None:
        ...

    async def stop(self) -> None:
        ...


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
        enu_trail: list[tuple[float, float, float]] = []
        for ts_dt, lat, lon, _alt in tr.history[-60:]:  # limit to last ~60 samples
            try:
                ts_val = float(ts_dt.timestamp())
            except Exception:
                ts_val = 0.0
            tx, ty, tz = geodetic_to_ecef(lat, lon, 0.0)
            e, n, _ = ecef_to_enu(tx, ty, tz, center_lat, center_lon, 0.0)
            enu_trail.append((e, n, ts_val))

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
    if getattr(args, "tft", False) and _ILI9341Cls is not None:
        # Physical TFT portrait 240x320
        display = _ILI9341Cls(width=240, height=320)
        if _TouchCls is not None:
            touch = _TouchCls(width=240, height=320, poll_hz=float(args.touch_hz))
            _run = getattr(touch, "run", None)
            if callable(_run):
                try:
                    maybe_coro = _run()
                except Exception:
                    maybe_coro = None
                    if maybe_coro is not None and hasattr(maybe_coro, "__await__"):
                        asyncio.ensure_future(cast(Awaitable[Any], maybe_coro))
        print("[live_view] TFT mode active (ILI9341 + XPT2046)")
    elif args.web_ui:
        display = WebDisplayBackend(size=(1280, 800), create_window=False)
        print("[live_view] Web UI mode active (http://localhost:8080)")
    else:
        display = PygameDisplayBackend(size=(480, 800), create_window=True)
        print("[live_view] Pygame window mode active")
    view = PpiView(
        show_data_blocks=not bool(args.simple),
        label_font_px=args.font_px,
        label_line_gap_px=args.block_line_gap_px,
    )

    # Ensure basemap exists and is ingested by default (non-destructive if present)
    bm_db = str(Path.home() / ".pocketscope" / "basemap.sqlite")
    try:
        Path(bm_db).parent.mkdir(parents=True, exist_ok=True)
        bm = BaseMap(bm_db)
        # Attempt to ingest known asset files if present; ignore failures.
        try:
            runways_asset = (
                Path(__file__).resolve().parents[1] / "assets" / "runways.json"
            )
            if runways_asset.exists():
                bm.ingest_runways(str(runways_asset))
        except Exception:
            pass
        try:
            airports_asset = (
                Path(__file__).resolve().parents[1] / "assets" / "airports.json"
            )
            if airports_asset.exists():
                bm.ingest_airports(str(airports_asset))
        except Exception:
            pass
        try:
            states_asset = (
                Path(__file__).resolve().parents[1] / "assets" / "us_states.json"
            )
            if states_asset.exists():
                bm.ingest_states(str(states_asset))
        except Exception:
            pass
    except Exception:
        bm = None

    ui = UiController(
        display=display,
        view=view,
        bus=bus,
        ts=ts,
        tracks=tracks,
        cfg=UiConfig(range_nm=float(args.range), overlay=True, target_fps=30.0),
        center_lat=float(args.center[0]),
        center_lon=float(args.center[1]),
        font_px=args.font_px,
        # UiController will always use the default basemap DB internally.
    )
    bar = SoftKeyBar(
        display.size(),
        bar_height=60,
        pad_y=10,
        border_width=0,
        # Lightweight measurement when using TFT so we don't import pygame
        measure_fn=(lambda s, sz: (int(sz * 0.6) * len(s), sz))
        if getattr(args, "tft", False)
        else None,
        actions={
            "-": ui.zoom_out,
            "Settings": lambda: None,
            "+": ui.zoom_in,
        },
    )
    ui.set_softkeys(bar)
    # Autoscale: background control loop (1 Hz) that proposes changes.
    try:
        # Adapter providing minimal required interface for controller
        class _ASAdapter:
            def __init__(self, ui_ctrl: UiController):
                self._ui = ui_ctrl

            def get_radius_nm(self) -> float:
                return float(self._ui._cfg.range_nm)

            def get_alt_band_ft(self) -> tuple[int | None, int | None]:
                try:
                    return (
                        getattr(self._ui._settings, "altitude_min_ft", None),
                        getattr(self._ui._settings, "altitude_max_ft", None),
                    )
                except Exception:
                    return (None, None)

            def ensure_in_view(self, target: dict[str, Any] | Any) -> None:
                try:
                    # attempt to recentre slightly by setting center; best-effort
                    lat_v = target.get("lat")
                    lon_v = target.get("lon")
                    if lat_v is None or lon_v is None:
                        raise ValueError("missing lat/lon")
                    lat = float(lat_v)
                    lon = float(lon_v)
                    self._ui._center_lat = lat
                    self._ui._center_lon = lon
                except Exception:
                    pass

            def in_view(self, lat: float, lon: float, radius_nm: float) -> bool:
                # reuse haversine helper
                try:
                    from pocketscope.core.geo import haversine_nm

                    return haversine_nm(
                        self._ui._center_lat, self._ui._center_lon, lat, lon
                    ) <= float(radius_nm)
                except Exception:
                    return True

        autoscale_cfg = AutoscaleSettings()
        # Only load the persisted enabled flag; all runtime tuning is kept
        # in-memory and must not be persisted by autoscale logic.
        try:
            raw: dict[str, Any] = {}
            sp = SettingsStore.settings_path()
            if sp.exists():
                import json

                raw = json.loads(sp.read_text()) or {}
            acfg = raw.get("autoscale")
            # Support both legacy boolean and object shapes in settings.json.
            if isinstance(acfg, bool):
                autoscale_cfg.enabled = acfg
            elif isinstance(acfg, dict):
                val = acfg.get("enabled")
                if isinstance(val, bool):
                    autoscale_cfg.enabled = val
        except Exception:
            pass

        _adapter = _ASAdapter(ui)
        _autoscaler = AutoScaleController(_adapter, autoscale_cfg)

        async def _autoscale_loop() -> None:
            while True:
                try:
                    # Build aircraft list from TrackService quick snapshot
                    active_tracks = tracks.list_active()
                    ac_list = []
                    for tr in active_tracks:
                        try:
                            last = tr.history[-1]
                            geo_alt = tr.state.get("geo_alt") or tr.state.get(
                                "baro_alt"
                            )
                            ac_list.append(
                                {
                                    "lat": float(last[1]),
                                    "lon": float(last[2]),
                                    "geo_alt": geo_alt,
                                    "icao24": tr.icao24,
                                }
                            )
                        except Exception:
                            continue

                    props = _autoscaler.tick(ac_list, focused=None, now=ts.monotonic())

                    # Apply proposals in-memory: update UiController overrides so
                    # the UI updates without writing to settings.json.
                    rn = props.get("radius_nm")
                    if rn is not None:
                        try:
                            ui.apply_autoscale_range_override(float(rn))
                        except Exception:
                            pass

                    # Support explicit altitude override proposals (may contain
                    # None to indicate unbounded). This key takes precedence
                    # over legacy alt_min/alt_max proposals.
                    alt_ov = props.get("alt_override")
                    if alt_ov is not None:
                        try:
                            ui.apply_autoscale_alt_override((alt_ov[0], alt_ov[1]))
                        except Exception:
                            pass
                    else:
                        amin = props.get("alt_min_ft")
                        amax = props.get("alt_max_ft")
                        if amin is not None or amax is not None:
                            try:
                                lo = (
                                    float(amin)
                                    if amin is not None
                                    else ui.alt_filter[0]
                                )
                                hi = (
                                    float(amax)
                                    if amax is not None
                                    else ui.alt_filter[1]
                                )
                                ui.apply_autoscale_alt_override((lo, hi))
                            except Exception:
                                pass
                except Exception:
                    pass
                await asyncio.sleep(1.0)

        asyncio.create_task(_autoscale_loop())
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
                            # Settings overlay takes precedence
                            try:
                                if ui._settings_screen.visible:
                                    try:
                                        consumed_local = ui._settings_screen.on_mouse(
                                            x, y, display.size(), ui
                                        )
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
                                        abs(last_down_pos[0] - ix) <= 8
                                        and abs(last_down_pos[1] - iy) <= 8
                                    ):
                                        recent = True
                            except Exception:
                                recent = False
                            if not recent:
                                _dispatch_press(ix, iy)

                except Exception:
                    pass
                await asyncio.sleep(0.01)

        asyncio.create_task(_touch_forwarder())

    # Basemap and ingestion were prepared earlier; no per-run CLI flags
    # are required. Proceed to startup.

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
