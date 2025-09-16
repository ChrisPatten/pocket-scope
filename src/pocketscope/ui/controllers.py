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

from pocketscope import config as _config
from pocketscope.core.events import EventBus, Subscription, unpack
from pocketscope.core.geo import ecef_to_enu, geodetic_to_ecef
from pocketscope.core.time import TimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource
from pocketscope.render.canvas import DisplayBackend
from pocketscope.render.view_ppi import PpiView, TrackSnapshot
from pocketscope.settings.schema import Settings
from pocketscope.settings.store import SettingsStore
from pocketscope.settings.values import (
    ALTITUDE_FILTER_BANDS,
    ALTITUDE_FILTER_CYCLE_ORDER,
    RANGE_LADDER_NM,
    SETTINGS_SCREEN_CONFIG,
    TRACK_LENGTH_PRESETS_S,
    TRACK_SERVICE_DEFAULTS,
    UNITS_ORDER,
    ZOOM_LIMITS,
)
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
    min_range_nm: float = float(ZOOM_LIMITS.get("min_range_nm", 2.0))
    max_range_nm: float = float(ZOOM_LIMITS.get("max_range_nm", 80.0))
    target_fps: float = 30.0
    overlay: bool = True


class UiController:
    """Owns frame loop, input handling, and composite rendering for PPI UI."""

    def __init__(
        self,
        *,
        display: DisplayBackend,
        view: PpiView,
        bus: EventBus,
        ts: TimeSource,
        tracks: TrackService,
        cfg: UiConfig,
        center_lat: float | None = None,
        center_lon: float | None = None,
        font_px: int = 12,
    ) -> None:
        # Core references
        self._display = display
        self._view = view
        self._bus = bus
        self._ts = ts
        # Apply track service defaults if instance appears to have library defaults
        try:
            if isinstance(tracks, TrackService):
                if getattr(tracks, "_trail_len_default_s", None) == 60.0:
                    setattr(
                        tracks,
                        "_trail_len_default_s",
                        float(TRACK_SERVICE_DEFAULTS.get("trail_len_default_s", 60.0)),
                    )
                if getattr(tracks, "_trail_len_pinned_s", None) == 180.0:
                    setattr(
                        tracks,
                        "_trail_len_pinned_s",
                        float(TRACK_SERVICE_DEFAULTS.get("trail_len_pinned_s", 180.0)),
                    )
        except Exception:
            pass
        self._tracks = tracks
        self._cfg = cfg
        # Runtime state
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

        # Persistent settings load & field mirrors
        # Load persisted settings early so overlay can pick up padding/font
        # values from the store instead of falling back to defaults.
        self._settings: Settings = SettingsStore.load()
        # Overlay (diagnostics / status)
        try:  # width may raise if backend not fully initialized in tests
            disp_w, _disp_h = self._display.size()
        except Exception:
            disp_w = 300  # pragmatic fallback for headless environments
        self._overlay = StatusOverlay(self._settings, width_px=disp_w)
        self._cfg.range_nm = float(self._settings.range_nm)
        self.units = self._settings.units
        self.track_length_s = float(getattr(self._settings, "track_length_s", 45.0))
        # Track expiry window (seconds) persisted; fallback to service defaults
        try:
            self.track_expiry_s = float(
                getattr(
                    self._settings,
                    "track_expiry_s",
                    float(TRACK_SERVICE_DEFAULTS.get("expiry_s", 300.0)),
                )
            )
        except Exception:
            self.track_expiry_s = float(TRACK_SERVICE_DEFAULTS.get("expiry_s", 300.0))
        self.demo_mode = self._settings.demo_mode
        self.altitude_filter = getattr(self._settings, "altitude_filter", "All")
        self.north_up_lock = getattr(self._settings, "north_up_lock", True)
        # Sector label visibility (persisted)
        self.sector_labels = bool(getattr(self._settings, "sector_labels", True))
        # Apply persisted trail length immediately so TrackService windows
        # reflect a user-provided custom value on startup (previously only
        # applied when cycling or after a cfg.changed hot‑reload event).
        try:
            self._apply_track_windows()
        except Exception:
            pass

        # Whether the final framebuffer should be flipped/rotated for the
        # display hardware. Mirrors persisted setting but does not force a
        # disk write; persistence is controlled by the settings screen Save.
        self._flip_display = bool(getattr(self._settings, "flip_display", False))

        # Apply persisted flip state to backend immediately if supported.
        try:
            self.apply_display_flip(self._flip_display)
        except Exception:
            # Best-effort; do not break initialization if backend missing hook
            pass
        # Autoscale runtime-only overrides. When autoscale is enabled these
        # in-memory values may be adjusted by the AutoScaleController. They
        # are intentionally not persisted to settings.json.
        self._autoscale_range_override: float | None = None
        # altitude override is a tuple (min_ft, max_ft) where either value
        # may be None to indicate unbounded. None means no override.
        self._autoscale_alt_override: tuple[float | None, float | None] | None = None

        # Settings screen overlay (multiplier from configuration)
        settings_font_px = int(
            font_px * float(SETTINGS_SCREEN_CONFIG.get("font_multiplier", 1.2))
        )
        self._settings_screen = SettingsScreen(
            self._settings, font_px=settings_font_px, pad_px=6
        )
        self._apply_track_windows()
        self._apply_track_windows()
        # Apply persisted typography settings to active view if available
        try:
            if hasattr(self._view, "label_font_px"):
                self._view.label_font_px = int(self._settings.label_font_px)
            if hasattr(self._view, "label_line_gap_px"):
                self._view.label_line_gap_px = int(self._settings.label_line_gap_px)
            if hasattr(self._view, "label_block_pad_px"):
                self._view.label_block_pad_px = int(self._settings.label_block_pad_px)
            if hasattr(self._view, "show_sector_labels"):
                self._view.show_sector_labels = bool(self.sector_labels)
            # Apply aged trail cutoff from settings if view supports it
            try:
                if hasattr(self._view, "trail_aged_cutoff_s"):
                    self._view.trail_aged_cutoff_s = float(
                        getattr(self._settings, "trail_aged_cutoff_s", 30.0)
                    )
            except Exception:
                pass
        except Exception:
            pass

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

        # Optional static data. We always use an on-disk BaseMap SQLite DB
        # located at ~/.pocketscope/basemap.sqlite. Do not accept airports or
        # sectors as constructor params anymore; query the DB dynamically.
        self._airports: Optional[list[tuple[float, float, str]]] = None
        self._sectors: Optional[list["Sector"]] | None = None  # typed when available

        # Internal prefetch state to avoid scheduling IO every frame.
        # Key is a tuple: (range_nm_rounded, rotation_deg_rounded, idents)
        self._last_runway_prefetch_key: Optional[
            tuple[float, float, tuple[str, ...]]
        ] = None

        # Runway DB and prefetcher. Always attempt to use the default DB path
        # in the user's home directory; failures are non-fatal and leave
        # runway features disabled.
        from pathlib import Path as _Path

        try:
            from pocketscope.data.base_map import BaseMap, RunwayPrefetcher
        except Exception:
            RunwayPrefetcher = None  # type: ignore
            BaseMap = None  # type: ignore

        self._runways_basemap = None
        self._runway_prefetcher = None
        self._runway_icons = True
        try:
            _db_path = str(_Path.home() / ".pocketscope" / "basemap.sqlite")
            # Ensure directory exists (ingestion elsewhere may populate DB)
            _Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
            # Best-effort creation/opening of BaseMap
            if BaseMap is not None:
                try:
                    self._runways_basemap = BaseMap(_db_path)
                except Exception:
                    self._runways_basemap = None
            try:
                if self._runways_basemap is not None and RunwayPrefetcher is not None:
                    self._runway_prefetcher = RunwayPrefetcher(_db_path)
            except Exception:
                self._runway_prefetcher = None
        except Exception:
            self._runways_basemap = None
            self._runway_prefetcher = None

        # Prefetched state polygons cache. Keep cached states until the
        # ownship center moves more than 10 NM; no TTL.
        self._states_cached_center: Optional[tuple[float, float]] = None
        self._states_cached: Optional[list["Sector"]] = None

        # FPS tracking (EMA) + orientation
        self._prev_frame_t: Optional[float] = None
        self._fps_avg: float = float(cfg.target_fps)
        try:
            self._rotation_deg: float = float(getattr(self._view, "rotation_deg", 0.0))
        except Exception:
            self._rotation_deg = 0.0

    # ------------------------------------------------------------------
    # Basemap / states / runway prefetch helpers
    def _prefetch_basemap(self, *, radius_nm: float = 200.0) -> None:
        """Prefetch and cache basemap objects within `radius_nm` of the
        controller center. This is intentionally performed as a single
        disk/DB operation so render frames do not repeatedly hit the DB.

        On success updates:
        - self._states_cached, self._states_cached_center
        - self._sectors
        - self._airports
        and triggers runway prefetch for nearby airports if a runway
        prefetcher is available.
        """
        try:
            # Local imports to avoid module-level import cost in tests
            from pocketscope.core.geo import haversine_nm
            from pocketscope.data.sectors import get_states_in_view
        except Exception:
            return

        try:
            import math

            # Compute bbox for radius_nm
            dlat = float(radius_nm) / 60.0
            lat_rad = math.radians(float(self._center_lat))
            dlon = float(radius_nm) / (60.0 * max(1e-6, math.cos(lat_rad)))
            min_lat = float(self._center_lat) - dlat
            max_lat = float(self._center_lat) + dlat
            min_lon = float(self._center_lon) - dlon
            max_lon = float(self._center_lon) + dlon
        except Exception:
            return

        # Fetch state polygons (single DB call)
        try:
            states = get_states_in_view(
                getattr(self._runways_basemap, "sqlite_path", ""),
                float(min_lat),
                float(max_lat),
                float(min_lon),
                float(max_lon),
            )
            states_list = list(states) if states else []
            self._states_cached = states_list
            self._states_cached_center = (
                float(self._center_lat),
                float(self._center_lon),
            )
            self._sectors = states_list if states_list else None
        except Exception:
            # leave previous cache intact on failure
            pass

        # Fetch airports from basemap if available (single DB call)
        try:
            if self._runways_basemap is not None:
                # BaseMap expects (min_lat, max_lat, min_lon, max_lon)
                airports = self._runways_basemap.get_airports_in_view(
                    min_lat, max_lat, min_lon, max_lon
                )
                # Convert to simple list of tuples and keep only those within
                # radius
                ap_list: list[tuple[float, float, str]] = []
                for lat, lon, ident in airports:
                    try:
                        dist = haversine_nm(
                            float(self._center_lat),
                            float(self._center_lon),
                            float(lat),
                            float(lon),
                        )
                        if dist <= float(radius_nm):
                            ap_list.append((float(lat), float(lon), str(ident).upper()))
                    except Exception:
                        continue
                self._airports = ap_list
            else:
                self._airports = None
        except Exception:
            # keep previous airports on failure
            pass

        # Runway prefetch for idents within radius (single call)
        try:
            if self._runway_prefetcher and self._airports:
                idents = [ident for (_la, _lo, ident) in self._airports]
                if idents:
                    try:
                        self._runway_prefetcher.prefetch(idents)
                        # update last prefetch key to avoid redundant calls
                        self._last_runway_prefetch_key = (
                            round(float(self._cfg.range_nm), 3),
                            round(float(self._rotation_deg), 2),
                            tuple(sorted(idents)),
                        )
                    except Exception:
                        pass
        except Exception:
            pass

    def _should_refresh_cache_for_move(self) -> bool:
        """Return True when cached states/airports should be refreshed due to
        ownship center movement > 10 NM or when no cache exists.
        """
        try:
            from pocketscope.core.geo import haversine_nm

            if self._states_cached_center is None or self._states_cached is None:
                return True
            dist = haversine_nm(
                float(self._states_cached_center[0]),
                float(self._states_cached_center[1]),
                float(self._center_lat),
                float(self._center_lon),
            )
            return dist > 10.0
        except Exception:
            return True

    def set_softkeys(self, bar: SoftKeyBar) -> None:
        # Apply persisted softkey typography/padding when available
        try:
            bar._requested_font_px = int(
                getattr(self._settings, "softkeys_font_px", bar._requested_font_px)
            )
        except Exception:
            pass
        try:
            bar.pad_x = int(getattr(self._settings, "softkeys_pad_x", bar.pad_x))
        except Exception:
            pass
        try:
            bar.pad_y = int(getattr(self._settings, "softkeys_pad_y", bar.pad_y))
        except Exception:
            pass
        # Allow runtime settings to control height: clear any explicit height
        # so layout will derive bar height from requested font + pad_y.
        try:
            bar.bar_height = None
        except Exception:
            pass
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
                # Ensure basemap/sectors/airports are prefetched once on
                # startup to avoid per-frame disk I/O. The helper is
                # idempotent and cheap if DB missing.
                try:
                    if self._states_cached is None or self._airports is None:
                        self._prefetch_basemap(radius_nm=200.0)
                except Exception:
                    pass
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
                # Draw PPI view with occlusion rectangles
                # Prefetch runways for visible airports when PPI state changes
                try:
                    # Use cached states/airports and only touch disk when the
                    # ownship center has moved beyond the configured threshold
                    # or when the cache is empty. Heavy DB operations are
                    # encapsulated in _prefetch_basemap which centralizes IO.
                    try:
                        if self._runways_basemap and self._runway_prefetcher:
                            if self._should_refresh_cache_for_move():
                                # Refresh caches (states, airports, runways)
                                self._prefetch_basemap(radius_nm=200.0)
                        else:
                            # If no basemap present, ensure fields are None
                            self._airports = None
                            # Keep previous sectors if any; do not attempt IO
                    except Exception:
                        # Do not allow prefetch failures to break the render loop
                        pass
                except Exception:
                    # Do not allow prefetch failures to break the render loop
                    pass

                self._view.draw(
                    canvas,
                    size_px=self._display.size(),
                    center_lat=self._center_lat,
                    center_lon=self._center_lon,
                    tracks=snaps,
                    airports=self._airports,
                    sectors=cast("Optional[Sequence[Sector]]", self._sectors),
                    occlusions=self._compute_occlusions(),
                    basemap=self._runways_basemap,
                    runway_icons=self._runway_icons,
                )

                # Diagnostics overlay
                if self._cfg.overlay:
                    # FPS/bus diagnostics removed from overlay per new wireframe
                    _fps_inst, _fps_avg = self._update_fps(
                        t0
                    )  # still computed to keep EMA warm
                    # Future: health flags derived from services; for now assume True
                    clock_utc = self._fmt_clock(self._ts.wall_time())
                    # Compute most recent track timestamp across active
                    # tracks so the overlay can display the age of the
                    # latest data. If no tracks exist, pass None.
                    tracks = []
                    latest_ts: float | None = None
                    try:
                        tracks = self._tracks.list_active()
                        for tr in tracks:
                            try:
                                t = tr.last_ts.timestamp()
                                if latest_ts is None or t > latest_ts:
                                    latest_ts = t
                            except Exception:
                                continue
                    except Exception:
                        # leave tracks as [] and latest_ts as None
                        tracks = []
                        latest_ts = None

                    # Compute nearest track distance (nm) and altitude (ft)
                    # ac_count: number of aircraft whose location is within the
                    # visible area of the PPI (range only)
                    # ac_visible_count: number of aircraft actually visible to the
                    # user considering altitude filters in effect
                    nearest_range_nm = None
                    nearest_alt_ft = None
                    ac_count = 0
                    ac_visible = 0
                    try:
                        from math import asin, cos, radians, sin, sqrt

                        def _haversine_nm(
                            lat1: float, lon1: float, lat2: float, lon2: float
                        ) -> float:
                            R = 6371000.0
                            dlat = radians(lat2 - lat1)
                            dlon = radians(lon2 - lon1)
                            a = (
                                sin(dlat / 2) ** 2
                                + cos(radians(lat1))
                                * cos(radians(lat2))
                                * sin(dlon / 2) ** 2
                            )
                            c = 2 * asin(min(1, sqrt(a)))
                            meters = R * c
                            nm = meters / 1852.0
                            return nm

                        center_lat = float(self._center_lat)
                        center_lon = float(self._center_lon)
                        alt_min_ft, alt_max_ft = self.alt_filter
                        if isinstance(tracks, (list, tuple)) and tracks:
                            for tr in tracks:
                                try:
                                    if not tr.history:
                                        continue
                                    last = tr.history[-1]
                                    lat = float(last[1])
                                    lon = float(last[2])
                                    alt = None
                                    try:
                                        # last[3] may be altitude sample
                                        if isinstance(last[3], (int, float)):
                                            alt = float(last[3])
                                    except Exception:
                                        alt = None
                                    rng = None
                                    # Prefer a view helper if available
                                    try:
                                        fn = getattr(
                                            self._view, "great_circle_range_nm", None
                                        )
                                        if callable(fn):
                                            _res = fn(
                                                (lat, lon), (center_lat, center_lon)
                                            )
                                            try:
                                                if isinstance(_res, (int, float)):
                                                    rng = float(_res)
                                                else:
                                                    # try string-conversion fallback
                                                    rng = float(str(_res))
                                            except Exception:
                                                rng = None
                                    except Exception:
                                        rng = None
                                    if rng is None:
                                        rng = _haversine_nm(
                                            lat, lon, center_lat, center_lon
                                        )
                                    if rng is None:
                                        continue
                                    # Count aircraft within PPI range (ac_count)
                                    try:
                                        cfg_range = self._cfg.range_nm
                                        if isinstance(cfg_range, (int, float)) and (
                                            rng <= float(cfg_range)
                                        ):
                                            ac_count += 1
                                    except Exception:
                                        pass
                                    # Determine if aircraft passes altitude filter
                                    alt_pass = True
                                    try:
                                        # If an altitude filter is active and the
                                        # track lacks altitude, it's considered not
                                        # visible
                                        if (
                                            alt_min_ft is not None
                                            or alt_max_ft is not None
                                        ) and alt is None:
                                            alt_pass = False
                                        if (
                                            alt is not None
                                            and alt_min_ft is not None
                                            and alt < alt_min_ft
                                        ):
                                            alt_pass = False
                                        if (
                                            alt is not None
                                            and alt_max_ft is not None
                                            and alt >= alt_max_ft
                                        ):
                                            alt_pass = False
                                    except Exception:
                                        alt_pass = True
                                    # ac_visible: within range and passes altitude
                                    # filter
                                    try:
                                        cfg_range = self._cfg.range_nm
                                        if (
                                            isinstance(cfg_range, (int, float))
                                            and (rng <= float(cfg_range))
                                            and alt_pass
                                        ):
                                            ac_visible += 1
                                    except Exception:
                                        pass
                                    if (
                                        nearest_range_nm is None
                                        or rng < nearest_range_nm
                                    ):
                                        nearest_range_nm = rng
                                        nearest_alt_ft = alt
                                except Exception:
                                    continue
                    except Exception:
                        nearest_range_nm = None
                        nearest_alt_ft = None

                    # derive visible count if available
                    try:
                        ac_visible_count = ac_visible
                    except Exception:
                        ac_visible_count = None

                    self._overlay.draw(
                        canvas,
                        self._settings,
                        range_nm=self._cfg.range_nm,
                        clock_utc=clock_utc,
                        center_lat=self._center_lat,
                        center_lon=self._center_lon,
                        gps_ok=True,
                        imu_ok=True,
                        decoder_ok=True,
                        last_update_ts=latest_ts,
                        ac_count=(
                            ac_count if isinstance(tracks, (list, tuple)) else None
                        ),
                        ac_visible_count=ac_visible_count,
                        nearest_range_nm=nearest_range_nm,
                        nearest_alt_ft=nearest_alt_ft,
                        alt_filter=self.alt_filter,
                    )
                # Settings overlay drawn (softkey mapping already synced earlier)
                if self._settings_screen.visible:
                    self._settings_screen.draw(
                        canvas, size=self._display.size(), controller=self
                    )
                # Draw softkeys last (either restricted or full set)
                if self._softkeys:
                    self._softkeys.draw(canvas)

                # No per-frame flip call here — flips are applied when the
                # setting changes or at controller initialization to avoid
                # repeatedly invoking backend hooks every frame.

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

    # ------------------------------------------------------------------
    def _compute_occlusions(self) -> list[tuple[int, int, int, int]]:
        """Return rectangles obscuring the PPI for label visibility filtering.

        Rectangles are (x, y, w, h) in display coordinates. Covers:
        - Status overlay (top band) when enabled
        - SoftKeyBar (bottom band) when present
        """
        occ: list[tuple[int, int, int, int]] = []
        try:
            w, _h = self._display.size()
        except Exception:
            return occ
        # Status overlay band
        try:
            if self._cfg.overlay:
                so = self._overlay
                lines = 2 + (1 if self.demo_mode else 0)
                line_h = so.font_px + 2 * so.pad_y
                panel_h = so.pad_top + so.pad_bottom + line_h * lines
                occ.append((0, 0, w, panel_h))
        except Exception:
            pass
        # Softkeys band
        try:
            if self._softkeys and getattr(self._softkeys, "_rects", None):
                r0 = self._softkeys._rects[0]
                y0 = r0[1]
                h_bar = r0[3]
                occ.append((0, y0, w, h_bar))
        except Exception:
            pass
        return occ

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
        order = list(UNITS_ORDER)
        i = order.index(self.units)
        self.units = order[(i + 1) % len(order)]
        self._settings.units = self.units
        if persist:
            SettingsStore.save_debounced(self._settings)

    def cycle_track_length(self, *, persist: bool = True) -> None:
        presets = list(TRACK_LENGTH_PRESETS_S)
        cur = float(getattr(self, "track_length_s", presets[0]))
        if cur in presets:
            idx = presets.index(cur)
            cur = presets[(idx + 1) % len(presets)]
        else:
            # Custom value -> reset to first preset
            cur = presets[0]
        self.track_length_s = float(cur)
        self._settings.track_length_s = float(cur)
        self._apply_track_windows()
        if persist:
            SettingsStore.save_debounced(self._settings)

    def cycle_track_expiry(self, *, persist: bool = True) -> None:
        # Small sensible preset ladder; mirror settings_screen constant
        presets = [120.0, 180.0, 300.0, 600.0, 900.0]
        cur = float(getattr(self, "track_expiry_s", presets[2]))
        if cur in presets:
            idx = presets.index(cur)
            cur = presets[(idx + 1) % len(presets)]
        else:
            cur = presets[0]
        self.track_expiry_s = float(cur)
        try:
            self._settings.track_expiry_s = float(cur)
        except Exception:
            pass
        self._apply_track_windows()
        if persist:
            SettingsStore.save_debounced(self._settings)

    def toggle_demo(self, *, persist: bool = True) -> None:
        self.demo_mode = not self.demo_mode
        self._settings.demo_mode = self.demo_mode
        if persist:
            # Save immediately to avoid race where the file watcher may read
            # an older file version and publish a stale config that resets
            # the in-memory demo flag. Debounce is used elsewhere, but demo
            # toggles are explicit user actions that should persist promptly.
            try:
                SettingsStore.save(self._settings)
            except Exception:
                # Fall back to debounced save if direct save fails
                try:
                    SettingsStore.save_debounced(self._settings)
                except Exception:
                    pass
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
        order = list(ALTITUDE_FILTER_CYCLE_ORDER)
        i = order.index(self.altitude_filter)
        self.altitude_filter = order[(i + 1) % len(order)]
        # Persist altitude filter band in settings model (schema includes field)
        self._settings.altitude_filter = self.altitude_filter
        if persist:
            SettingsStore.save_debounced(self._settings)

    @property
    def alt_filter(self) -> tuple[float | None, float | None]:
        """Return active altitude filter bounds (min_ft, max_ft)."""
        # Priority order when autoscale is enabled:
        # 1. In-memory autoscale altitude override (not persisted)
        # 2. Explicit persisted settings altitude_min_ft / altitude_max_ft
        # 3. Selected altitude band
        try:
            # Runtime autoscale override takes precedence when present
            _ov = getattr(self, "_autoscale_alt_override", None)
            if _ov is not None:
                try:
                    lo, hi = _ov
                    return lo, hi
                except Exception:
                    # If override is malformed, fall through to other sources
                    pass

            custom_lo = getattr(self._settings, "altitude_min_ft", None)
            custom_hi = getattr(self._settings, "altitude_max_ft", None)
            if custom_lo is not None or custom_hi is not None:
                return (
                    float(custom_lo) if custom_lo is not None else None,
                    float(custom_hi) if custom_hi is not None else None,
                )
        except (ValueError, TypeError):
            # Fallback to band if settings values are invalid
            pass

        # Otherwise, use the selected band
        band = getattr(self, "altitude_filter", "All")
        lo_hi = ALTITUDE_FILTER_BANDS.get(band, (None, None))
        return lo_hi[0], lo_hi[1]

    # Autoscale override APIs --------------------------------------
    def apply_autoscale_range_override(self, range_nm: float | None) -> None:
        """Apply a runtime-only autoscale range override (or clear with None).

        This updates the live view immediately but does not persist to disk.
        """
        try:
            self._autoscale_range_override = (
                float(range_nm) if range_nm is not None else None
            )
            if range_nm is not None:
                self._cfg.range_nm = float(range_nm)
            else:
                # restore to persisted baseline when cleared
                self._cfg.range_nm = float(self._settings.range_nm)
        except Exception:
            pass

    def apply_autoscale_alt_override(
        self, alt_pair: tuple[float | None, float | None] | None
    ) -> None:
        """Apply an in-memory altitude override tuple (min_ft, max_ft)
        or clear with None.

        When set, `alt_filter` will prefer this override. This method is
        the supported public API for autoscale adjustments.
        """
        try:
            if alt_pair is None:
                self._autoscale_alt_override = None
            else:
                lo, hi = alt_pair
                lo_val = float(lo) if lo is not None else None
                hi_val = float(hi) if hi is not None else None
                self._autoscale_alt_override = (lo_val, hi_val)
        except Exception:
            pass

    def clear_autoscale_overrides(self) -> None:
        """Clear both range and altitude autoscale overrides and restore
        persisted values.
        """
        try:
            self._autoscale_range_override = None
            self._autoscale_alt_override = None
            self._cfg.range_nm = float(self._settings.range_nm)
        except Exception:
            pass

    def _apply_track_windows(self) -> None:
        presets = list(TRACK_LENGTH_PRESETS_S)
        val = float(getattr(self, "track_length_s", presets[0]))
        self._tracks._trail_len_default_s = val
        # Pinned length: next larger preset if exists else max(val, largest preset)
        pinned = val
        try:
            if val in presets:
                idx = presets.index(val)
                if idx < len(presets) - 1:
                    pinned = presets[idx + 1]
                else:
                    pinned = max(val, presets[-1])
            else:
                pinned = max(val, presets[-1])
        except Exception:
            pinned = max(val, presets[-1]) if presets else val
        self._tracks._trail_len_pinned_s = float(pinned)
        # Re-trim existing active tracks immediately so UI reflects change
        try:
            self._tracks.retrim_all()
        except Exception:
            pass
        # Apply expiry window live
        try:
            if hasattr(self._tracks, "_expiry_s"):
                self._tracks._expiry_s = float(
                    getattr(
                        self,
                        "track_expiry_s",
                        float(TRACK_SERVICE_DEFAULTS.get("expiry_s", 300.0)),
                    )
                )
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
        steps = list(RANGE_LADDER_NM)
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
                self.track_length_s = float(
                    getattr(new, "track_length_s", self.track_length_s)
                )
                # Track expiry window (seconds)
                try:
                    self.track_expiry_s = float(
                        getattr(
                            new,
                            "track_expiry_s",
                            getattr(
                                self,
                                "track_expiry_s",
                                float(TRACK_SERVICE_DEFAULTS.get("expiry_s", 300.0)),
                            ),
                        )
                    )
                except Exception:
                    pass
                # Mirror trail aged cutoff to view if present
                try:
                    cutoff = float(getattr(new, "trail_aged_cutoff_s", 30.0))
                    if hasattr(self._view, "trail_aged_cutoff_s"):
                        try:
                            self._view.trail_aged_cutoff_s = cutoff
                        except Exception:
                            pass
                except Exception:
                    pass
                self.demo_mode = new.demo_mode
                self.altitude_filter = getattr(new, "altitude_filter", "All")
                self.north_up_lock = getattr(new, "north_up_lock", True)
                # Mirror flip_display runtime state and notify backend
                try:
                    self._flip_display = bool(getattr(new, "flip_display", False))
                    fn = getattr(self._display, "apply_flip", None)
                    if callable(fn):
                        fn(self._flip_display)
                except Exception:
                    pass
                self._apply_track_windows()
                # Refresh visible settings screen with external changes
                self._settings_screen.refresh_from_controller(self._settings)
                # Update central runtime config and notify listeners so
                # renderers and other components can react to external
                # settings changes dynamically. Notifications are deferred
                # to the event loop inside the config module to avoid
                # synchronous timing hazards.
                try:
                    _config.update_from_settings(self._settings)
                except Exception:
                    pass
                # Apply backlight setting to display backend when present
                try:
                    bl = getattr(self._settings, "backlight_pct", None)
                    if bl is not None:
                        fn = getattr(self._display, "set_backlight_pct", None)
                        if callable(fn):
                            try:
                                fn(float(bl))
                            except Exception:
                                pass
                except Exception:
                    pass
                # Apply typography changes to active view
                try:
                    if hasattr(self._view, "label_font_px"):
                        self._view.label_font_px = int(self._settings.label_font_px)
                    if hasattr(self._view, "label_line_gap_px"):
                        self._view.label_line_gap_px = int(
                            self._settings.label_line_gap_px
                        )
                    if hasattr(self._view, "label_block_pad_px"):
                        self._view.label_block_pad_px = int(
                            self._settings.label_block_pad_px
                        )
                    # Apply status overlay font size as well
                    try:
                        self._overlay.font_px = int(self._settings.status_font_px)
                    except Exception:
                        pass
                    # Apply softkey typography/padding
                    try:
                        if self._softkeys:
                            # Set requested font so layout uses it
                            self._softkeys._requested_font_px = int(
                                self._settings.softkeys_font_px
                            )
                            self._softkeys.pad_x = int(self._settings.softkeys_pad_x)
                            self._softkeys.pad_y = int(self._settings.softkeys_pad_y)
                            # Allow automatic height computation based on font/pad
                            try:
                                self._softkeys.bar_height = None
                            except Exception:
                                pass
                            self._softkeys.layout()
                    except Exception:
                        pass
                    # Apply explicit top/bottom padding when present
                    try:
                        spt = getattr(self._settings, "status_pad_top_px", None)
                        spb = getattr(self._settings, "status_pad_bottom_px", None)
                        if spt is not None:
                            self._overlay.pad_top = int(spt)
                        if spb is not None:
                            self._overlay.pad_bottom = int(spb)
                    except Exception:
                        pass
                except Exception:
                    pass
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
        tracks = self._tracks.list_active()
        lo, hi = self.alt_filter
        out: list[TrackSnapshot] = []
        # Precompute center ECEF once per frame (avoid repetition inside loop)
        _ox, _oy, _oz = geodetic_to_ecef(self._center_lat, self._center_lon, 0.0)
        for tr in tracks:
            if not tr.history:
                continue
            last = tr.history[-1]
            lat, lon = float(last[1]), float(last[2])
            # Determine altitude used for filtering. Preference order:
            # geo_alt, then baro_alt, then last trail altitude sample.
            alt_for_filter: float | None = None
            _ga = tr.state.get("geo_alt")
            _ba = tr.state.get("baro_alt")
            if isinstance(_ga, (int, float)):
                alt_for_filter = float(_ga)
            elif isinstance(_ba, (int, float)):
                alt_for_filter = float(_ba)
            else:
                try:
                    if isinstance(last[3], (int, float)):
                        alt_for_filter = float(last[3])
                except Exception:
                    pass
            if alt_for_filter is None and (lo is not None or hi is not None):
                # Exclude tracks lacking altitude when filtering active
                continue
            if lo is not None and alt_for_filter is not None and alt_for_filter < lo:
                continue
            if hi is not None and alt_for_filter is not None and alt_for_filter >= hi:
                continue
            # Course
            course = None
            v = tr.state.get("track_deg")
            if isinstance(v, (int, float)):
                course = float(v)
            # Dynamic trail window & thinning ---------------------------------
            # We render up to *track_length_s* seconds of trail, selecting
            # points by timestamp (not just count) so custom long lengths
            # (e.g. 600s) display correctly. When the resulting window has
            # more than MAX_POINTS we thin the *older* portion while keeping
            # a dense recent tail for visual fidelity of current motion.
            try:
                window_s = float(getattr(self, "track_length_s", 60.0))
            except Exception:
                window_s = 60.0
            # Compute cutoff timestamp
            try:
                end_ts = last[0].timestamp()
            except Exception:
                # Fallback: skip dynamic behaviour if timestamp missing
                end_ts = None
            hist = tr.history
            if end_ts is not None:
                cutoff = end_ts - window_s
                # Find first index >= cutoff (linear scan; history lengths are modest)
                start_idx = 0
                for i, pt in enumerate(hist):
                    try:
                        if pt[0].timestamp() >= cutoff:
                            start_idx = i
                            break
                    except Exception:
                        continue
                window_pts = hist[start_idx:]
            else:
                # Fallback: take the most recent window_s points by count
                try:
                    window_pts = hist[-int(window_s) :]
                except Exception:
                    window_pts = hist

            MAX_POINTS = 600  # hard cap for rendering performance
            RECENT_DENSE = 300  # keep this many newest points unthinned when thinning
            pts_sel = window_pts
            if len(pts_sel) > MAX_POINTS:
                # Keep last RECENT_DENSE verbatim; thin older portion uniformly.
                dense = pts_sel[-RECENT_DENSE:]
                older = pts_sel[:-RECENT_DENSE]
                if older:
                    target_old = MAX_POINTS - RECENT_DENSE
                    if target_old < 1:
                        target_old = 1
                    step = max(1, int(len(older) / target_old))
                    thinned_old = older[::step]
                    # Ensure we don't exceed MAX_POINTS (trim oldest if necessary)
                    combined = thinned_old + dense
                    if len(combined) > MAX_POINTS:
                        combined = combined[-MAX_POINTS:]
                    pts_sel = combined
                else:
                    pts_sel = dense  # degenerate case

            # Convert selected points to ENU
            trail_enu: list[tuple[float, float, float]] = []
            for ts_dt, la, lon_pt, _alt in pts_sel:
                try:
                    tx, ty, tz = geodetic_to_ecef(float(la), float(lon_pt), 0.0)
                    e, n, _ = ecef_to_enu(
                        tx, ty, tz, self._center_lat, self._center_lon, 0.0
                    )
                    try:
                        ts_val = float(ts_dt.timestamp())
                    except Exception:
                        ts_val = 0.0
                    trail_enu.append((e, n, ts_val))
                except Exception:
                    continue
            # Optional kinematics
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
                    geo_alt_ft=(
                        float(geo_alt) if isinstance(geo_alt, (int, float)) else None
                    ),
                    baro_alt_ft=(
                        float(baro_alt) if isinstance(baro_alt, (int, float)) else None
                    ),
                    ground_speed_kt=float(gs) if isinstance(gs, (int, float)) else None,
                    vertical_rate_fpm=(
                        float(vr) if isinstance(vr, (int, float)) else None
                    ),
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

    def apply_display_flip(self, flip: bool) -> None:
        """Set runtime flip flag and notify backend if it supports the hook.

        This method provides a single callable used by the settings screen so
        toggles can be applied immediately and consistently.
        """
        try:
            new = bool(flip)
        except Exception:
            new = False
        try:
            if new != getattr(self, "_flip_display", None):
                try:
                    print(f"[UiController] apply_display_flip -> {new}")
                except Exception:
                    pass
            self._flip_display = new
        except Exception:
            self._flip_display = False
        try:
            fn = getattr(self._display, "apply_flip", None)
            if callable(fn):
                try:
                    fn(self._flip_display)
                except Exception:
                    pass
        except Exception:
            pass

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
