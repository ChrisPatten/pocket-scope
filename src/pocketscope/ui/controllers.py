"""
Interactive UI controllers for PocketScope.

Provides a UiController that owns the frame tick, range control, and an
optional status overlay. It renders a PPI view and processes basic pygame
inputs for zooming and quitting.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence, cast

from pocketscope.core.events import EventBus, Subscription, unpack
from pocketscope.core.geo import ecef_to_enu, geodetic_to_ecef
from pocketscope.core.time import TimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView, TrackSnapshot
from pocketscope.settings.schema import Settings
from pocketscope.settings.store import SettingsStore
from pocketscope.ui.settings_screen import SettingsScreen
from pocketscope.ui.softkeys import SoftKeyBar
from pocketscope.ui.status_overlay import StatusOverlay

if TYPE_CHECKING:
    from pocketscope.data.sectors import Sector

pg: Any = None
try:  # optional import guard for environments without SDL
    import pygame as _pg

    pg = _pg
except Exception:  # pragma: no cover
    pg = None


@dataclass(slots=True)
class UiConfig:
    range_nm: float = 10.0
    min_range_nm: float = 2.0
    max_range_nm: float = 80.0
    target_fps: float = 30.0
    overlay: bool = True


class UiController:
    """
    Owns the frame-tick loop and user input to control range.
    Renders PPI + overlay every frame.

    run() timing
    ------------
    - Targets cfg.target_fps by awaiting ts.sleep(max(0, 1/fps - frame_time)).
    - Each frame builds a TrackService snapshot and calls View.draw, then
      draws the overlay when enabled.

    Range updates propagate by writing view.range_nm each frame.

    Key bindings (pygame)
    ---------------------
    - '[' or '-'  : zoom out
    - ']' or '='  : zoom in
    - 'o'         : toggle overlay
    - 'q' or ESC  : quit (graceful stop)
    - Mouse wheel up/down: zoom in/out
    """

    def __init__(
        self,
        *,
        display: PygameDisplayBackend,
        view: PpiView,
        bus: EventBus,
        ts: TimeSource,
        tracks: TrackService,
        cfg: UiConfig,
        center_lat: float | None = None,
        center_lon: float | None = None,
        airports: Optional[list[tuple[float, float, str]]] = None,
        sectors: Optional[object] = None,
        font_px: int = 12,
    ) -> None:
        # Core references
        self._display = display
        self._view = view
        self._bus = bus
        self._ts = ts
        self._tracks = tracks
        self._cfg = cfg
        # Runtime state
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

        # Overlay (diagnostics / status)
        try:  # width may raise if backend not fully initialized in tests
            disp_w, _disp_h = self._display.size()
        except Exception:
            disp_w = 300  # pragmatic fallback for headless environments
        self._overlay = StatusOverlay(font_px=font_px, width_px=disp_w)

        # Persistent settings load & field mirrors
        self._settings: Settings = SettingsStore.load()
        self._cfg.range_nm = float(self._settings.range_nm)
        self.units: str = self._settings.units
        self.track_length_mode: str = self._settings.track_length_mode
        self.demo_mode: bool = self._settings.demo_mode
        self.altitude_filter: str = getattr(self._settings, "altitude_filter", "All")
        self.north_up_lock: bool = getattr(self._settings, "north_up_lock", True)

        # Settings screen overlay (slightly larger font for readability)
        settings_font_px = int(font_px * 1.2)
        self._settings_screen = SettingsScreen(
            self._settings, font_px=settings_font_px, pad_px=6
        )
        self._apply_track_windows()

        # Softkeys (late-bound via set_softkeys)
        self._softkeys: SoftKeyBar | None = None
        self._softkeys_base_actions: dict[str, Callable[[], None]] | None = None

        # Config change subscription & listener task
        self._cfg_sub: Subscription | None = bus.subscribe("cfg.changed")
        self._cfg_task: asyncio.Task[None] | None = asyncio.create_task(
            self._cfg_listener()
        )

        # Geographic center defaults (Boston area sentinel)
        self._center_lat: float = 42.0 if center_lat is None else float(center_lat)
        self._center_lon: float = -71.0 if center_lon is None else float(center_lon)
        # Preserve original (non-demo) center so we can restore when leaving demo
        self._center_lat_live: float = self._center_lat
        self._center_lon_live: float = self._center_lon

        # Demo playback management
        self._demo_src: FilePlaybackSource | None = None
        self._demo_task: asyncio.Task[None] | None = None
        self._demo_trace_path_env = "POCKETSCOPE_DEMO_TRACE"
        self._demo_default_trace = (
            Path(__file__).resolve().parents[3] / "sample_data" / "demo_adsb.jsonl"
        )

        # Optional static data
        self._airports: Optional[list[tuple[float, float, str]]] = (
            list(airports) if airports else None
        )
        self._sectors = sectors  # typed only when TYPE_CHECKING

        # FPS tracking (EMA) + orientation
        self._prev_frame_t: Optional[float] = None
        self._fps_avg: float = float(cfg.target_fps)
        try:
            self._rotation_deg: float = float(getattr(self._view, "rotation_deg", 0.0))
        except Exception:
            self._rotation_deg = 0.0

    def set_softkeys(self, bar: SoftKeyBar) -> None:
        self._softkeys = bar

        # Ensure Settings button is wired
        def _toggle_settings() -> None:
            self._settings_screen.on_key("s", self)

        self._softkeys.actions["Settings"] = _toggle_settings
        self._softkeys.layout()

    async def run(self) -> None:
        self._running = True
        dt_target = 1.0 / max(1e-6, float(self._cfg.target_fps))
        # Ensure pygame initialized for input
        if pg is not None and not pg.get_init():
            pg.init()
            if not pg.font.get_init():
                pg.font.init()

        try:
            while self._running:
                t0 = self._ts.monotonic()
                # Handle input
                self._process_input()
                # Ensure softkey action set reflects current settings screen visibility
                self._sync_softkeys()

                # Build snapshot of active tracks
                snaps = self._build_snapshots()

                # Render frame
                canvas = self._display.begin_frame()
                self._view.range_nm = float(self._cfg.range_nm)
                # Apply rotation to view each frame
                if hasattr(self._view, "rotation_deg"):
                    if self.north_up_lock:
                        self._rotation_deg = 0.0  # enforce lock each frame
                    self._view.rotation_deg = float(self._rotation_deg) % 360.0
                self._view.draw(
                    canvas,
                    size_px=self._display.size(),
                    center_lat=self._center_lat,
                    center_lon=self._center_lon,
                    tracks=snaps,
                    airports=self._airports,
                    sectors=cast("Optional[Sequence[Sector]]", self._sectors),
                )

                # Diagnostics overlay
                if self._cfg.overlay:
                    # FPS/bus diagnostics removed from overlay per new wireframe
                    _fps_inst, _fps_avg = self._update_fps(
                        t0
                    )  # still computed to keep EMA warm
                    # Future: health flags derived from services; for now assume True
                    clock_utc = self._fmt_clock(self._ts.wall_time())
                    self._overlay.draw(
                        canvas,
                        range_nm=self._cfg.range_nm,
                        clock_utc=clock_utc,
                        center_lat=self._center_lat,
                        center_lon=self._center_lon,
                        gps_ok=True,
                        imu_ok=True,
                        decoder_ok=True,
                        units=self.units,
                        demo_mode=self.demo_mode,
                    )
                # Settings overlay drawn (softkey mapping already synced earlier)
                if self._settings_screen.visible:
                    self._settings_screen.draw(
                        canvas, size=self._display.size(), controller=self
                    )
                # Draw softkeys last (either restricted or full set)
                if self._softkeys:
                    self._softkeys.draw(canvas)

                self._display.end_frame()

                # Frame pacing
                t1 = self._ts.monotonic()
                remaining = dt_target - max(0.0, t1 - t0)
                if remaining > 0:
                    await self._ts.sleep(remaining)
                else:
                    # Yield to avoid starving other tasks
                    await asyncio.sleep(0)
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancel
            pass
        finally:
            self._running = False

    async def stop(self) -> None:
        self._running = False
        if self._cfg_sub:
            await self._cfg_sub.close()
            self._cfg_sub = None
        if self._cfg_task:
            self._cfg_task.cancel()
            try:
                await self._cfg_task
            except asyncio.CancelledError:
                pass
            self._cfg_task = None

    def zoom_in(self, *, persist: bool = True) -> None:
        self._cfg.range_nm = self._step_range(self._cfg.range_nm, direction=-1)
        self._settings.range_nm = self._cfg.range_nm
        if persist:
            SettingsStore.save_debounced(self._settings)

    def zoom_out(self, *, persist: bool = True) -> None:
        self._cfg.range_nm = self._step_range(self._cfg.range_nm, direction=+1)
        self._settings.range_nm = self._cfg.range_nm
        if persist:
            SettingsStore.save_debounced(self._settings)

    def toggle_overlay(self) -> None:
        self._cfg.overlay = not self._cfg.overlay

    def cycle_units(self, *, persist: bool = True) -> None:
        order = ["nm_ft_kt", "mi_ft_mph", "km_m_kmh"]
        i = order.index(self.units)
        self.units = order[(i + 1) % len(order)]
        self._settings.units = self.units
        if persist:
            SettingsStore.save_debounced(self._settings)

    def cycle_track_length(self, *, persist: bool = True) -> None:
        order = ["short", "medium", "long"]
        i = order.index(self.track_length_mode)
        self.track_length_mode = order[(i + 1) % len(order)]
        self._settings.track_length_mode = self.track_length_mode
        self._apply_track_windows()
        if persist:
            SettingsStore.save_debounced(self._settings)

    def toggle_demo(self, *, persist: bool = True) -> None:
        self.demo_mode = not self.demo_mode
        self._settings.demo_mode = self.demo_mode
        if persist:
            SettingsStore.save_debounced(self._settings)
        try:
            if self.demo_mode:
                self._start_demo_mode()
            else:
                self._stop_demo_mode()
        except Exception:  # pragma: no cover - defensive; never break toggle
            pass

    def cycle_altitude_filter(self, *, persist: bool = True) -> None:
        """Cycle altitude filter band.

        Order matches settings screen menu. Persists (debounced) when *persist*
        is True.
        """
        order = ["All", "0–5k", "5–10k", "10–20k", ">20k"]
        i = order.index(self.altitude_filter)
        self.altitude_filter = order[(i + 1) % len(order)]
        # Persist altitude filter band in settings model (schema includes field)
        self._settings.altitude_filter = self.altitude_filter
        if persist:
            SettingsStore.save_debounced(self._settings)

    def _apply_track_windows(self) -> None:
        mapping = {"short": 15.0, "medium": 45.0, "long": 120.0}
        default = mapping.get(self.track_length_mode, 45.0)
        next_map = {"short": "medium", "medium": "long", "long": "long"}
        pinned = mapping[next_map[self.track_length_mode]]
        self._tracks._trail_len_default_s = default
        self._tracks._trail_len_pinned_s = pinned
        # Re-trim existing active tracks immediately so UI reflects change
        try:
            self._tracks.retrim_all()
        except Exception:
            pass

    def rotate_left(self, step_deg: float = 5.0) -> None:
        """Rotate view counter-clockwise (left arrow)."""
        if self.north_up_lock:
            self._rotation_deg = 0.0
            return
        self._rotation_deg = (self._rotation_deg - float(step_deg)) % 360.0

    def rotate_right(self, step_deg: float = 5.0) -> None:
        """Rotate view clockwise (right arrow)."""
        if self.north_up_lock:
            self._rotation_deg = 0.0
            return
        self._rotation_deg = (self._rotation_deg + float(step_deg)) % 360.0

    def toggle_north_up_lock(self, *, persist: bool = True) -> None:
        """Toggle persistent north-up orientation lock.

        Enabling the lock zeros current rotation and ignores manual rotate
        commands until disabled. Persist (debounced) by default.
        """
        self.north_up_lock = not self.north_up_lock
        if self.north_up_lock:
            self._rotation_deg = 0.0
        try:
            # Field present in schema; assign directly
            self._settings.north_up_lock = self.north_up_lock
        except Exception:
            pass
        if persist:
            SettingsStore.save_debounced(self._settings)

    # Internals ----------------------------------------------------------
    def _process_input(self) -> None:
        if pg is None:
            return
        for ev in pg.event.get():
            if ev.type == pg.QUIT:
                self._running = False
            elif ev.type == pg.KEYDOWN:
                key = ev.key
                if self._softkeys:
                    self._softkeys.on_key(pg.key.name(key))
                # Route to settings screen (string form from pygame key)
                if self._settings_screen.visible:
                    self._settings_screen.on_key(pg.key.name(key), self)
                    # If still visible after handling and not a toggle key,
                    # swallow other bindings
                    if self._settings_screen.visible and pg.key.name(key) not in {"s"}:
                        if key in (pg.K_q, pg.K_ESCAPE):
                            # settings screen handles quit to scope, not app
                            continue
                        # Do not process remaining key logic while menu open
                        continue
                elif pg.key.name(key) == "s":  # open via hotkey
                    self._settings_screen.on_key("s", self)
                    continue
                if key in (pg.K_LEFTBRACKET, pg.K_MINUS):
                    self.zoom_out()
                elif key in (pg.K_RIGHTBRACKET, pg.K_EQUALS):
                    self.zoom_in()
                elif key == pg.K_LEFT:
                    self.rotate_left()
                elif key == pg.K_RIGHT:
                    self.rotate_right()
                elif key == pg.K_o:
                    self.toggle_overlay()
                elif key in (pg.K_q, pg.K_ESCAPE):
                    if self._settings_screen.visible:
                        self._settings_screen.visible = False
                    else:
                        self._running = False
            elif ev.type == pg.MOUSEBUTTONDOWN:
                x, y = ev.pos
                # If settings screen visible, attempt to consume click first.
                if self._settings_screen.visible:
                    try:
                        consumed = self._settings_screen.on_mouse(
                            x, y, self._display.size(), self
                        )
                    except Exception:
                        consumed = False
                    if consumed:
                        continue  # Do not allow click to fall through
                if self._softkeys:
                    self._softkeys.on_mouse(x, y, ev.button == 1)
            elif ev.type == pg.MOUSEWHEEL:
                if getattr(ev, "y", 0) > 0:
                    self.zoom_in()
                elif getattr(ev, "y", 0) < 0:
                    self.zoom_out()

    def _sync_softkeys(self) -> None:
        """Synchronize softkey actions with settings screen visibility.

        This removes a previous one-frame lag where the restricted Back/Save
        actions were installed during rendering after input processing. That
        timing window could cause clicks immediately after toggling the
        settings screen to invoke the old mapping, making softkey presses
        appear to "miss" sporadically. By syncing right after input handling
        (and on every frame for safety) the mapping always matches what is
        displayed on screen before the user can click.
        """
        if not self._softkeys:
            return
        if self._settings_screen.visible:
            if self._softkeys_base_actions is None:
                self._softkeys_base_actions = dict(self._softkeys.actions)
                self._softkeys.actions = self._settings_screen.softkey_actions(self)
                self._softkeys.layout()
        else:
            if self._softkeys_base_actions is not None:
                # Restore full action set
                self._softkeys.actions = dict(self._softkeys_base_actions)
                self._softkeys_base_actions = None
                self._softkeys.layout()

    def _step_range(self, value: float, *, direction: int) -> float:
        # Discrete zoom ladder
        steps = [2.0, 5.0, 10.0, 20.0, 40.0, 80.0]
        v = float(value)
        # Find nearest step index
        idx = 0
        for i, s in enumerate(steps):
            if v <= s:
                idx = i
                break
        else:
            idx = len(steps) - 1
        idx = max(0, min(len(steps) - 1, idx + direction))
        nv = steps[idx]
        # Clamp to config bounds
        nv = max(float(self._cfg.min_range_nm), min(float(self._cfg.max_range_nm), nv))
        return nv

    async def _cfg_listener(self) -> None:
        if self._cfg_sub is None:
            return
        try:
            async for env in self._cfg_sub:
                data = unpack(env.payload)
                try:
                    new = Settings.model_validate(data)
                except Exception:
                    continue
                self._settings = new
                self._cfg.range_nm = float(new.range_nm)
                self.units = new.units
                self.track_length_mode = new.track_length_mode
                self.demo_mode = new.demo_mode
                self.altitude_filter = getattr(new, "altitude_filter", "All")
                self.north_up_lock = getattr(new, "north_up_lock", True)
                self._apply_track_windows()
                # Refresh visible settings screen with external changes
                self._settings_screen.refresh_from_controller(self._settings)
                # Respond to external demo_mode changes
                try:
                    if self.demo_mode and self._demo_task is None:
                        self._start_demo_mode()
                    elif not self.demo_mode and self._demo_task is not None:
                        self._stop_demo_mode()
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    def _build_snapshots(self) -> list[TrackSnapshot]:
        # Convert TrackService tracks into TrackSnapshot with short ENU trail
        tracks = self._tracks.list_active()
        # Pre-compute altitude filter bounds
        band = self.altitude_filter
        # Bounds are inclusive of lower, exclusive of upper except last band
        lo: float | None = None
        hi: float | None = None
        if band == "0–5k":
            lo, hi = 0.0, 5000.0
        elif band == "5–10k":
            lo, hi = 5000.0, 10000.0
        elif band == "10–20k":
            lo, hi = 10000.0, 20000.0
        elif band == ">20k":
            lo, hi = 20000.0, None
        out: list[TrackSnapshot] = []
        # Precompute center ECEF once per frame
        _ox, _oy, _oz = geodetic_to_ecef(self._center_lat, self._center_lon, 0.0)
        for tr in tracks:
            if not tr.history:
                continue
            last = tr.history[-1]
            lat, lon = float(last[1]), float(last[2])
            # Altitude filter: use geo_alt preferred then baro_alt else last trail alt
            alt_for_filter: float | None = None
            _ga = tr.state.get("geo_alt")
            _ba = tr.state.get("baro_alt")
            if isinstance(_ga, (int, float)):
                alt_for_filter = float(_ga)
            elif isinstance(_ba, (int, float)):
                alt_for_filter = float(_ba)
            else:
                # Trail point altitude (4th tuple element) may be set
                try:
                    if isinstance(last[3], (int, float)):
                        alt_for_filter = float(last[3])
                except Exception:
                    pass
            if band != "All" and alt_for_filter is not None:
                if lo is not None and alt_for_filter < lo:
                    continue
                if hi is not None and alt_for_filter >= hi:
                    continue
            elif band != "All" and alt_for_filter is None:
                # If filtering by band and have no altitude, exclude
                continue
            course = None
            v = tr.state.get("track_deg")
            if isinstance(v, (int, float)):
                course = float(v)

            # Trail: last ~60 samples -> ENU
            pts = tr.history[-60:]
            trail_enu: list[tuple[float, float]] = []
            # NOTE: use lon_pt variable name to avoid clobbering altitude
            # lower-bound variable 'lo' defined earlier for filtering.
            for _, la, lon_pt, _alt in pts:
                tx, ty, tz = geodetic_to_ecef(float(la), float(lon_pt), 0.0)
                e, n, _ = ecef_to_enu(
                    tx, ty, tz, self._center_lat, self._center_lon, 0.0
                )
                trail_enu.append((e, n))

            # Optional kinematics for data blocks
            geo_alt = tr.state.get("geo_alt")
            baro_alt = tr.state.get("baro_alt")
            gs = tr.state.get("ground_speed")
            vr = tr.state.get("vertical_rate")

            out.append(
                TrackSnapshot(
                    icao=tr.icao24,
                    lat=lat,
                    lon=lon,
                    callsign=tr.callsign,
                    course_deg=course,
                    trail_enu=trail_enu if len(trail_enu) >= 2 else None,
                    geo_alt_ft=float(geo_alt)
                    if isinstance(geo_alt, (int, float))
                    else None,
                    baro_alt_ft=float(baro_alt)
                    if isinstance(baro_alt, (int, float))
                    else None,
                    ground_speed_kt=float(gs) if isinstance(gs, (int, float)) else None,
                    vertical_rate_fpm=float(vr)
                    if isinstance(vr, (int, float))
                    else None,
                )
            )
        return out

    def _update_fps(self, t0: float) -> tuple[float, float]:
        t1 = self._ts.monotonic()
        if self._prev_frame_t is None:
            self._prev_frame_t = t1
            return (float(self._cfg.target_fps), float(self._cfg.target_fps))
        dt = max(1e-6, t1 - self._prev_frame_t)
        fps_inst = 1.0 / dt
        # Simple EMA
        alpha = 0.2
        self._fps_avg = (1.0 - alpha) * self._fps_avg + alpha * fps_inst
        self._prev_frame_t = t1
        return (fps_inst, self._fps_avg)

    def _bus_summary(self) -> str:
        m = self._bus.metrics()
        if not m.topics:
            return "bus: idle"
        # Aggregate counts
        qlen = max((s.queue_len for s in m.topics.values()), default=0)
        drops = sum(s.drops for s in m.topics.values())
        pubs = sum(s.publishes for s in m.topics.values())
        dels = sum(s.deliveries for s in m.topics.values())
        return f"bus q{qlen} p{pubs} d{dels} x{drops}"

    @staticmethod
    def _fmt_clock(wall_ts: float) -> str:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(wall_ts, tz=_dt.timezone.utc).strftime(
            "%H:%M:%SZ"
        )

    # Demo mode helpers -------------------------------------------------
    def _start_demo_mode(self) -> None:
        """Start looping JSONL playback and fix center position."""
        # Stop any existing demo (idempotent)
        self._stop_demo_mode()
        trace_path = (
            Path(os.environ[self._demo_trace_path_env])
            if self._demo_trace_path_env in os.environ
            else self._demo_default_trace
        )
        if not trace_path.exists():  # No trace -> silently keep just badge
            return
        # Derive center from first valid record if possible
        try:
            with open(trace_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        msg = rec.get("msg", {})
                        lat = msg.get("lat")
                        lon = msg.get("lon")
                        if isinstance(lat, (int, float)) and isinstance(
                            lon, (int, float)
                        ):
                            self._center_lat_live = (
                                self._center_lat
                            )  # stash current live center
                            self._center_lon_live = self._center_lon
                            self._center_lat = float(lat)
                            self._center_lon = float(lon)
                            break
                    except Exception:
                        continue
        except Exception:
            pass
        # Clear any existing tracks so demo is clean
        try:
            if hasattr(self._tracks, "clear"):
                self._tracks.clear()
        except Exception:
            pass
        # Launch playback source
        try:
            self._demo_src = FilePlaybackSource(
                str(trace_path), ts=self._ts, bus=self._bus, speed=1.0, loop=True
            )
            self._demo_task = asyncio.create_task(self._demo_src.run())
        except Exception:
            self._demo_src = None
            self._demo_task = None

    def _stop_demo_mode(self) -> None:
        """Stop demo playback and restore center if needed."""
        # Cancel playback task
        if self._demo_src is not None:
            try:
                asyncio.create_task(self._demo_src.stop())
            except Exception:
                pass
        if self._demo_task is not None:
            try:
                self._demo_task.cancel()
            except Exception:
                pass
        self._demo_src = None
        self._demo_task = None
        # Restore original center when leaving demo
        if not self.demo_mode:
            self._center_lat = self._center_lat_live
            self._center_lon = self._center_lon_live
        # Clear tracks so demo aircraft disappear promptly
        try:
            if hasattr(self._tracks, "clear"):
                self._tracks.clear()
        except Exception:
            pass
