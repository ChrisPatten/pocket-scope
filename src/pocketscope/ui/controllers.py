"""
Interactive UI controllers for PocketScope.

Provides a UiController that owns the frame tick, range control, and an
optional status overlay. It renders a PPI view and processes basic pygame
inputs for zooming and quitting.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Sequence, cast

from pocketscope.core.events import EventBus, Subscription, unpack
from pocketscope.core.geo import ecef_to_enu, geodetic_to_ecef
from pocketscope.core.time import TimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.view_ppi import PpiView, TrackSnapshot
from pocketscope.settings.schema import Settings
from pocketscope.settings.store import SettingsStore
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
        self._display = display
        self._view = view
        self._bus = bus
        self._ts = ts
        self._tracks = tracks
        self._cfg = cfg
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._overlay = StatusOverlay(font_px=font_px)
        self._settings: Settings = SettingsStore.load()
        self._cfg.range_nm = float(self._settings.range_nm)
        self.units: str = self._settings.units
        self.track_length_mode: str = self._settings.track_length_mode
        self.demo_mode: bool = self._settings.demo_mode
        self._apply_track_windows()
        self._softkeys: SoftKeyBar | None = None
        self._cfg_sub: Subscription | None = bus.subscribe("cfg.changed")
        self._cfg_task: asyncio.Task[None] | None = asyncio.create_task(
            self._cfg_listener()
        )
        # Defaults for Boston area if not provided
        self._center_lat = 42.0 if center_lat is None else float(center_lat)
        self._center_lon = -71.0 if center_lon is None else float(center_lon)
        # Optional airports data as (lat, lon, ident) tuples
        self._airports: Optional[list[tuple[float, float, str]]] = (
            list(airports) if airports else None
        )
        # Optional sectors: Sequence[Sector]
        self._sectors = sectors
        # FPS tracking (EMA)
        self._prev_frame_t: Optional[float] = None
        self._fps_avg: float = cfg.target_fps
        # View orientation (degrees, 0 = North-up, clockwise positive)
        try:
            self._rotation_deg: float = float(getattr(self._view, "rotation_deg", 0.0))
        except Exception:
            self._rotation_deg = 0.0

    def set_softkeys(self, bar: SoftKeyBar) -> None:
        self._softkeys = bar
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

                # Build snapshot of active tracks
                snaps = self._build_snapshots()

                # Render frame
                canvas = self._display.begin_frame()
                self._view.range_nm = float(self._cfg.range_nm)
                # Apply rotation to view each frame
                if hasattr(self._view, "rotation_deg"):
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
                    fps_inst, fps_avg = self._update_fps(t0)
                    bus_summary = self._bus_summary()
                    clock_utc = self._fmt_clock(self._ts.wall_time())
                    self._overlay.draw(
                        canvas,
                        fps_inst=fps_inst,
                        fps_avg=fps_avg,
                        range_nm=self._cfg.range_nm,
                        active_tracks=len(snaps),
                        bus_summary=bus_summary,
                        clock_utc=clock_utc,
                        units=self.units,
                        demo_mode=self.demo_mode,
                    )
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

    def zoom_in(self) -> None:
        self._cfg.range_nm = self._step_range(self._cfg.range_nm, direction=-1)
        self._settings.range_nm = self._cfg.range_nm
        SettingsStore.save_debounced(self._settings)

    def zoom_out(self) -> None:
        self._cfg.range_nm = self._step_range(self._cfg.range_nm, direction=+1)
        self._settings.range_nm = self._cfg.range_nm
        SettingsStore.save_debounced(self._settings)

    def toggle_overlay(self) -> None:
        self._cfg.overlay = not self._cfg.overlay

    def cycle_units(self) -> None:
        order = ["nm_ft_kt", "mi_ft_mph", "km_m_kmh"]
        i = order.index(self.units)
        self.units = order[(i + 1) % len(order)]
        self._settings.units = self.units
        SettingsStore.save_debounced(self._settings)

    def cycle_track_length(self) -> None:
        order = ["short", "medium", "long"]
        i = order.index(self.track_length_mode)
        self.track_length_mode = order[(i + 1) % len(order)]
        self._settings.track_length_mode = self.track_length_mode
        self._apply_track_windows()
        SettingsStore.save_debounced(self._settings)

    def toggle_demo(self) -> None:
        self.demo_mode = not self.demo_mode
        self._settings.demo_mode = self.demo_mode
        SettingsStore.save_debounced(self._settings)

    def _apply_track_windows(self) -> None:
        mapping = {"short": 15.0, "medium": 45.0, "long": 120.0}
        default = mapping.get(self.track_length_mode, 45.0)
        next_map = {"short": "medium", "medium": "long", "long": "long"}
        pinned = mapping[next_map[self.track_length_mode]]
        self._tracks._trail_len_default_s = default
        self._tracks._trail_len_pinned_s = pinned

    def rotate_left(self, step_deg: float = 5.0) -> None:
        """Rotate view counter-clockwise (left arrow)."""
        self._rotation_deg = (self._rotation_deg - float(step_deg)) % 360.0

    def rotate_right(self, step_deg: float = 5.0) -> None:
        """Rotate view clockwise (right arrow)."""
        self._rotation_deg = (self._rotation_deg + float(step_deg)) % 360.0

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
                    self._running = False
            elif ev.type == pg.MOUSEBUTTONDOWN:
                if self._softkeys:
                    x, y = ev.pos
                    self._softkeys.on_mouse(x, y, ev.button == 1)
            elif ev.type == pg.MOUSEWHEEL:
                if getattr(ev, "y", 0) > 0:
                    self.zoom_in()
                elif getattr(ev, "y", 0) < 0:
                    self.zoom_out()

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
                self._apply_track_windows()
        except asyncio.CancelledError:
            pass

    def _build_snapshots(self) -> list[TrackSnapshot]:
        # Convert TrackService tracks into TrackSnapshot with short ENU trail
        tracks = self._tracks.list_active()
        out: list[TrackSnapshot] = []
        # Precompute center ECEF once per frame
        _ox, _oy, _oz = geodetic_to_ecef(self._center_lat, self._center_lon, 0.0)
        for tr in tracks:
            if not tr.history:
                continue
            last = tr.history[-1]
            lat, lon = float(last[1]), float(last[2])
            course = None
            v = tr.state.get("track_deg")
            if isinstance(v, (int, float)):
                course = float(v)

            # Trail: last ~60 samples -> ENU
            pts = tr.history[-60:]
            trail_enu: list[tuple[float, float]] = []
            for _, la, lo, _alt in pts:
                tx, ty, tz = geodetic_to_ecef(float(la), float(lo), 0.0)
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
