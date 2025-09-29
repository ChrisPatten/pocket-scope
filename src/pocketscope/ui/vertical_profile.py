"""Config-driven vertical profile sidebar."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence

from pocketscope.core.geo import initial_bearing_deg
from pocketscope.render.canvas import Canvas, Color
from pocketscope.settings.schema import Settings, VerticalProfileSettings
from pocketscope.theme import ThemeManager

# Fallback drawing colors (overridden each draw via _theme_color).
# These provide names for type checking inside helper methods before runtime
# theme resolution occurs.
COLOR_AXIS: Color = (90, 90, 90, 255)
COLOR_MUTED: Color = (140, 140, 160, 255)


def _theme_color(key: str, fallback: tuple[int, int, int, int]) -> Color:
    try:
        c = ThemeManager.color(key)
        return (int(c[0]), int(c[1]), int(c[2]), int(c[3]))
    except Exception:
        return fallback


_VS_CLAMP_ABS_FPM: float = 3000.0  # magnitude for vertical speed color saturation


@dataclass(slots=True)
class VerticalProfileSample:
    """Minimal data required for vertical profile calculations."""

    icao: str
    callsign: str | None
    lat: float
    lon: float
    altitude_ft: float | None
    last_ts: float
    distance_nm: float | None
    track: Any


@dataclass(slots=True)
class ProfileEntry:
    icao: str
    callsign: str | None
    altitude_ft: float | None
    vertical_rate_fpm: float | None
    distance_nm: float | None
    bearing_deg: float | None
    age_s: float | None
    ground_speed_kt: float | None
    heading_deg: float | None
    is_eligible: bool
    is_fallback: bool


@dataclass(slots=True)
class VerticalProfileState:
    focus: ProfileEntry | None
    closest: ProfileEntry | None
    rotation_index: int
    total_slots: int
    pinned: bool
    paused_until: float | None
    info_targets: set[str]
    eligible: list[ProfileEntry]
    fallback: list[ProfileEntry]
    history_points: list[tuple[float, float]]
    future_points: list[tuple[float, float]]
    window: tuple[float, float]
    now_ts: float


class VerticalProfilePanel:
    """Manage vertical profile selection, cycling, and drawing."""

    def __init__(self, settings: Settings, *, side: str = "right") -> None:
        self._settings = settings
        self._cfg: VerticalProfileSettings = settings.vertical_profile
        self.side = side if side in {"left", "right"} else "right"
        self._window_s: float = 60.0
        self._focus_icao: str | None = None
        self._pinned: bool = False
        self._cycle_deadline: float | None = None
        self._manual_hold_until: float | None = None
        self._eligible_flags: Dict[str, bool] = {}
        self._vs_cache: Dict[str, Optional[float]] = {}
        self._rotation_list: list[str] = []
        self._rotation_index: int = 0
        self._panel_rect: tuple[int, int, int, int] | None = None
        self._last_state: VerticalProfileState | None = None
        self._last_update_monotonic: float = 0.0

    # Config -----------------------------------------------------------
    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._cfg = settings.vertical_profile

    def set_side(self, side: str) -> None:
        if side in {"left", "right"}:
            self.side = side

    # Cycling ----------------------------------------------------------
    def toggle_pin(self, now_monotonic: float) -> bool:
        if self._focus_icao is None:
            return self._pinned
        self._pinned = not self._pinned
        if self._pinned:
            self._manual_hold_until = math.inf
        else:
            self._manual_hold_until = None
            self._cycle_deadline = now_monotonic + self._cfg.cycle_interval_sec
        return self._pinned

    def step_next(self, now_monotonic: float) -> None:
        if not self._rotation_list:
            return
        self._rotation_index = (self._rotation_index + 1) % len(self._rotation_list)
        self._focus_icao = self._rotation_list[self._rotation_index]
        self._cycle_deadline = now_monotonic + self._cfg.cycle_interval_sec
        self._manual_hold_until = self._manual_hold_deadline(now_monotonic)

    def step_prev(self, now_monotonic: float) -> None:
        if not self._rotation_list:
            return
        self._rotation_index = (self._rotation_index - 1) % len(self._rotation_list)
        self._focus_icao = self._rotation_list[self._rotation_index]
        self._cycle_deadline = now_monotonic + self._cfg.cycle_interval_sec
        self._manual_hold_until = self._manual_hold_deadline(now_monotonic)

    def _manual_hold_deadline(self, now_monotonic: float) -> float | None:
        resume_ms = int(getattr(self._cfg, "auto_resume_after_manual_ms", 30000))
        if resume_ms <= 0:
            return math.inf
        return now_monotonic + (resume_ms / 1000.0)

    # Update -----------------------------------------------------------
    def update(
        self,
        samples: Iterable[VerticalProfileSample],
        *,
        center_lat: float,
        center_lon: float,
        now_monotonic: float,
        now_wall: float,
    ) -> VerticalProfileState:
        entries: list[ProfileEntry] = []
        fallback_entries: list[ProfileEntry] = []
        closest_entry: ProfileEntry | None = None
        closest_dist = math.inf
        enter_thr = max(
            float(self._cfg.vs_hysteresis.enter),
            float(self._cfg.vs_threshold_fpm),
        )
        exit_thr = float(self._cfg.vs_hysteresis.exit)
        info_targets: set[str] = set()
        track_by_icao: Dict[str, Any] = {}

        for sample in samples:
            track = sample.track
            if not track:
                continue
            track_by_icao[sample.icao] = track
            age_s = float(max(0.0, now_wall - sample.last_ts))
            if age_s > 600.0:
                continue
            gs = self._float(track.state.get("ground_speed")) if hasattr(track, "state") else None
            bearing = None
            try:
                bearing = float(initial_bearing_deg(center_lat, center_lon, sample.lat, sample.lon))
            except Exception:
                bearing = None
            vs = self._vertical_rate(sample, now_wall)
            is_on_ground = False
            try:
                # Optional on-ground flag in track.state
                on_ground_flag = track.state.get("on_ground")
            except Exception:
                on_ground_flag = None
            if isinstance(on_ground_flag, bool):
                is_on_ground = on_ground_flag
            elif track and sample.altitude_ft is not None and gs is not None:
                if sample.altitude_ft < 200.0 and gs < 30.0:
                    is_on_ground = True

            eligible_prev = self._eligible_flags.get(sample.icao, False)
            eligible_now = False
            if not is_on_ground and vs is not None and age_s <= 10.0:
                abs_vs = abs(vs)
                if eligible_prev:
                    eligible_now = abs_vs >= exit_thr
                else:
                    eligible_now = abs_vs >= enter_thr
            self._eligible_flags[sample.icao] = eligible_now

            entry = ProfileEntry(
                icao=sample.icao,
                callsign=sample.callsign,
                altitude_ft=sample.altitude_ft,
                vertical_rate_fpm=vs,
                distance_nm=sample.distance_nm,
                bearing_deg=bearing,
                age_s=age_s,
                ground_speed_kt=gs,
                heading_deg=self._float(track.state.get("track_deg")) if hasattr(track, "state") else None,
                is_eligible=eligible_now,
                is_fallback=False,
            )

            if eligible_now:
                entries.append(entry)
            else:
                fallback_entries.append(entry)

            if entry.distance_nm is not None and entry.distance_nm < closest_dist:
                closest_entry = entry
                closest_dist = entry.distance_nm

        entries.sort(key=self._eligible_sort_key)
        fallback_entries.sort(
            key=lambda e: (
                e.distance_nm if e.distance_nm is not None else math.inf,
                e.age_s or math.inf,
            )
        )
        for fb in fallback_entries:
            fb.is_fallback = True

        # Rotation pool change (2025-09): previously only climbing/descending
        # "eligible" aircraft (entries) were rotated unless none were
        # available. Requirement update: rotate through ALL aircraft currently
        # visible on screen (the caller now filters samples to in-range
        # traffic). We therefore always build the pool from both lists while
        # preserving the existing ordering heuristic of placing actively
        # climbing/descending traffic ahead of level/on‑ground traffic.
        pool = entries + fallback_entries
        pool_ids = [e.icao for e in pool]
        self._rotation_list = pool_ids

        if not pool_ids:
            self._focus_icao = None
            self._rotation_index = 0
        else:
            if self._focus_icao not in pool_ids:
                self._rotation_index = 0
                self._focus_icao = pool_ids[0]
                self._cycle_deadline = now_monotonic + self._cfg.cycle_interval_sec
            if self._pinned and self._focus_icao not in pool_ids:
                self._pinned = False
                self._manual_hold_until = None
            if not self._pinned:
                if self._manual_hold_until is not None:
                    if now_monotonic >= self._manual_hold_until:
                        self._manual_hold_until = None
                    else:
                        if self._focus_icao not in pool_ids:
                            self._rotation_index = 0
                            self._focus_icao = pool_ids[0]
                            self._cycle_deadline = now_monotonic + self._cfg.cycle_interval_sec
                if self._manual_hold_until is None:
                    focus_entry = self._entry_for(pool, self._focus_icao)
                    if focus_entry and focus_entry.age_s and focus_entry.age_s > 10.0:
                        self._advance_focus(now_monotonic, pool_ids)
                    elif self._cycle_deadline is None:
                        self._cycle_deadline = now_monotonic + self._cfg.cycle_interval_sec
                    elif len(pool_ids) > 1 and now_monotonic >= self._cycle_deadline:
                        self._advance_focus(now_monotonic, pool_ids)
        focus_entry = self._entry_for(pool, self._focus_icao)
        total_slots = len(pool_ids)
        if focus_entry is None and pool:
            focus_entry = pool[0]
            self._focus_icao = focus_entry.icao
            self._rotation_index = 0

        if focus_entry:
            info_targets.add(focus_entry.icao)
        if closest_entry and closest_entry.icao not in info_targets:
            info_targets.add(closest_entry.icao)

        window = (now_wall - 240.0, now_wall + 60.0)
        history_points: list[tuple[float, float]] = []
        future_points: list[tuple[float, float]] = []
        if focus_entry and focus_entry.altitude_ft is not None:
            track_obj = track_by_icao.get(focus_entry.icao)
            if track_obj is not None:
                history_points, future_points = self._build_profile_points(
                    track_obj,
                    focus_entry.altitude_ft,
                    focus_entry.vertical_rate_fpm,
                    now_wall,
                )
                history_points = [pt for pt in history_points if pt[0] >= window[0] - 1.0]
                if history_points and history_points[0][0] > window[0]:
                    history_points.insert(0, (window[0], history_points[0][1]))
                future_points = [pt for pt in future_points if pt[0] <= window[1] + 1.0]

        state = VerticalProfileState(
            focus=focus_entry,
            closest=closest_entry,
            rotation_index=(self._rotation_index if total_slots else 0),
            total_slots=total_slots,
            pinned=self._pinned,
            paused_until=self._manual_hold_until,
            info_targets=info_targets,
            eligible=entries,
            fallback=fallback_entries,
            history_points=history_points,
            future_points=future_points,
            window=window,
            now_ts=now_wall,
        )
        self._last_state = state
        self._last_update_monotonic = now_monotonic
        return state

    def _advance_focus(self, now_monotonic: float, pool_ids: list[str]) -> None:
        if not pool_ids:
            return
        if not pool_ids:
            return
        if self._focus_icao not in pool_ids:
            self._rotation_index = 0
        else:
            idx = pool_ids.index(self._focus_icao)
            idx = (idx + 1) % len(pool_ids)
            self._rotation_index = idx
        self._focus_icao = pool_ids[self._rotation_index]
        self._cycle_deadline = now_monotonic + self._cfg.cycle_interval_sec

    def _entry_for(self, pool: Iterable[ProfileEntry], icao: str | None) -> ProfileEntry | None:
        if icao is None:
            return None
        for entry in pool:
            if entry.icao == icao:
                return entry
        return None

    def _vertical_rate(self, sample: VerticalProfileSample, now_wall: float) -> float | None:
        cached = self._vs_cache.get(sample.icao)
        track = sample.track
        history = getattr(track, "history", None)
        if not history:
            return cached
        latest = history[-1]
        latest_ts = latest[0].timestamp()
        target = latest_ts - self._window_s
        anchor = None
        for pt in reversed(history):
            ts = pt[0].timestamp()
            alt = pt[3]
            if alt is None:
                continue
            if ts <= target:
                anchor = pt
                break
            anchor = pt
        if anchor is None or anchor[3] is None or latest[3] is None:
            vs = None
        else:
            dt = latest[0].timestamp() - anchor[0].timestamp()
            if dt <= 0:
                vs = None
            else:
                vs = (float(latest[3]) - float(anchor[3])) / dt * 60.0
        if vs is None:
            try:
                # Optional vertical rate from track state; safe best-effort.
                raw = track.state.get("vertical_rate")
            except Exception:
                raw = None
            if isinstance(raw, (int, float)) and math.isfinite(raw):
                vs = float(raw)
        self._vs_cache[sample.icao] = vs
        return vs

    def _build_profile_points(
        self,
        track: Any,
        current_alt_ft: float,
        vertical_rate_fpm: float | None,
        now_ts: float,
    ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        history: list[tuple[float, float]] = []
        future: list[tuple[float, float]] = []
        try:
            hist = list(getattr(track, "history", []))
        except Exception:
            hist = []
        for pt in hist:
            try:
                ts = pt[0].timestamp()
                alt = pt[3]
                if alt is None:
                    continue
                history.append((ts, float(alt)))
            except Exception:
                continue
        history.append((now_ts, float(current_alt_ft)))
        history = [pt for pt in history if pt[0] <= now_ts]
        history = [pt for pt in history if pt[0] >= now_ts - 240.0]
        history.sort(key=lambda p: p[0])

        # ------------------------------------------------------------------
        # Gap smoothing / interpolation
        # Large temporal gaps produce long straight vertical/diagonal jumps
        # that are visually jarring. We densify segments so that rendering
        # appears smoother by inserting linearly interpolated samples when
        # the gap between consecutive points exceeds MAX_INTERVAL seconds.
        # This only affects rendering (history_points) and does not mutate
        # underlying track history or vertical rate computations upstream.
        MAX_INTERVAL = 5.0  # seconds; gaps larger than this are interpolated
        MAX_INTERPOLATED_PER_GAP = 50  # safety ceiling to avoid explosion
        if len(history) >= 2:
            dense: list[tuple[float, float]] = []
            for (t0, a0), (t1, a1) in zip(history, history[1:]):
                dense.append((t0, a0))
                dt = t1 - t0
                if dt > MAX_INTERVAL and math.isfinite(a0) and math.isfinite(a1):
                    # Number of intervals (n segments => n-1 interior points)
                    n_segments = int(dt // MAX_INTERVAL) + 1
                    n_segments = min(n_segments, MAX_INTERPOLATED_PER_GAP + 1)
                    # Insert interior points (exclude endpoints already present)
                    for seg in range(1, n_segments):
                        frac = seg / n_segments
                        tt = t0 + frac * dt
                        if tt >= t1:  # guard against float rounding
                            break
                        aa = a0 + (a1 - a0) * frac
                        dense.append((tt, aa))
            dense.append(history[-1])
            # Re-sort (interleaving preserves order but resort defensively)
            dense.sort(key=lambda p: p[0])
            history = dense

        if vertical_rate_fpm is not None and math.isfinite(vertical_rate_fpm):
            vr = float(vertical_rate_fpm)
            step = 15.0
            for dt in (step, step * 2, step * 3, step * 4):
                ts_future = now_ts + dt
                alt_future = current_alt_ft + vr * (dt / 60.0)
                future.append((ts_future, alt_future))
        return history, future

    def _draw_axes(
        self,
        canvas: Canvas,
        x0: int,
        y_top: int,
        x1: int,
        y_bottom: int,
        alt_min: float,
        alt_max: float,
        t_start: float,
        t_end: float,
        now_ts: float,
    ) -> None:
        # Baseline + Y axis
        canvas.line((x0, y_bottom), (x1, y_bottom), color=COLOR_AXIS)
        canvas.line((x0, y_top), (x0, y_bottom), color=COLOR_AXIS)
        # Two Y markers only (origin and top). Origin label forced to a whole
        # 1,000 ft increment supplied by caller as alt_min. Top label is the
        # observed maximum, rounded up to the next 1,000 ft (alt_max). Both
        # are rendered as flight levels (hundreds of feet). No mid label.
        if alt_max <= alt_min:
            return
        for alt in (alt_min, alt_max):
            y = self._map_alt(alt, alt_min, alt_max, y_top, y_bottom)
            canvas.line((x0 - 4, y), (x0, y), color=COLOR_AXIS)
            fl = int(round(alt / 100.0))
            canvas.text((x0 - 46, y - 6), f"FL{fl:03d}", size_px=10, color=COLOR_MUTED)
        # X axis labels & NOW marker intentionally omitted per request.

    def _draw_trace(
        self,
        canvas: Canvas,
        points: Sequence[tuple[float, float]],
        x0: int,
        y_top: int,
        x1: int,
        y_bottom: int,
        t_start: float,
        t_end: float,
        alt_min: float,
        alt_max: float,
        color: Color,
    ) -> None:
        if len(points) < 2:
            return
        mapped: list[tuple[int, int]] = []
        for ts, alt in points:
            x = self._map_time(ts, t_start, t_end, x0, x1)
            y = self._map_alt(alt, alt_min, alt_max, y_top, y_bottom)
            mapped.append((x, y))
        for idx in range(1, len(mapped)):
            canvas.line(mapped[idx - 1], mapped[idx], color=color)

    @staticmethod
    def _map_time(ts: float, t_start: float, t_end: float, x0: int, x1: int) -> int:
        if t_end <= t_start:
            return x0
        frac = (ts - t_start) / (t_end - t_start)
        frac = max(0.0, min(1.0, frac))
        return int(round(x0 + frac * (x1 - x0)))

    @staticmethod
    def _map_alt(alt: float, alt_min: float, alt_max: float, y_top: int, y_bottom: int) -> int:
        if alt_max <= alt_min:
            return y_bottom
        frac = (alt - alt_min) / (alt_max - alt_min)
        frac = max(0.0, min(1.0, frac))
        return int(round(y_bottom - frac * (y_bottom - y_top)))

    @staticmethod
    def _float(value: Any) -> float | None:
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
        return None

    @staticmethod
    def _eligible_sort_key(entry: ProfileEntry) -> tuple[float, float, float]:
        abs_vs = abs(entry.vertical_rate_fpm) if entry.vertical_rate_fpm is not None else -math.inf
        dist = entry.distance_nm if entry.distance_nm is not None else math.inf
        age = entry.age_s if entry.age_s is not None else math.inf
        return (-abs_vs, dist, age)

    # Input ------------------------------------------------------------
    def on_key(self, key: str, now_monotonic: float) -> bool:
        key = key.lower()
        if key in {",", "pageup"}:
            self.step_prev(now_monotonic)
            return True
        if key in {".", "pagedown"}:
            self.step_next(now_monotonic)
            return True
        if key in {"p"}:
            self.toggle_pin(now_monotonic)
            return True
        return False

    def on_mouse(self, x: int, y: int, button: int, now_monotonic: float) -> bool:
        # Graph view is informational only; ignore pointer input for now.
        return False

    # Drawing ----------------------------------------------------------
    def draw(
        self,
        canvas: Canvas,
        *,
        size: tuple[int, int],
        state: VerticalProfileState | None,
    ) -> None:
        if state is None:
            return
        w, h = size
        height_frac = max(0.1, min(0.6, float(getattr(self._cfg, "height_fraction", 0.35))))
        panel_h = max(60, int(h * height_frac))
        x0 = 0
        y0 = h - panel_h
        panel_w = w
        self._panel_rect = (x0, y0, panel_w, panel_h)
        # Resolve colors fresh each draw (theme live reload)
        C_BG = _theme_color("vprof.bg", (18, 18, 18, 235))
        C_BORDER = _theme_color("vprof.border", (64, 128, 200, 255))
        C_TEXT = _theme_color("vprof.text", (240, 240, 240, 255))
        C_MUTED = _theme_color("vprof.muted", (140, 140, 160, 255))
        C_AXIS = _theme_color("vprof.axis", (90, 90, 90, 255))
        C_TRACE = _theme_color("vprof.trace", (120, 200, 255, 255))
        C_VS_NEG = _theme_color("vprof.vs.neg", (215, 60, 60, 255))
        C_VS_POS = _theme_color("vprof.vs.pos", (60, 210, 90, 255))
        C_VS_NEUTRAL = _theme_color("vprof.vs.neutral", (160, 160, 160, 255))

        for dy in range(panel_h):
            canvas.line((x0, y0 + dy), (x0 + panel_w - 1, y0 + dy), color=C_BG)
        canvas.line((x0, y0), (x0 + panel_w - 1, y0), color=C_BORDER)
        canvas.line((x0, h - 1), (x0 + panel_w - 1, h - 1), color=C_BORDER)
        canvas.line((x0, y0), (x0, h - 1), color=C_BORDER)
        canvas.line((x0 + panel_w - 1, y0), (x0 + panel_w - 1, h - 1), color=C_BORDER)

        focus = state.focus
        if focus is None or not state.history_points:
            canvas.text((x0 + 16, y0 + 16), "VERT PROFILE", size_px=14, color=C_MUTED)
            canvas.text(
                (x0 + 16, y0 + 34),
                "No visible traffic",
                size_px=14,
                color=C_TEXT,
            )
            return

        # ------------------------------------------------------------------
        # Header layout redesign per request:
        #  Left : Flight number / callsign (same font size as VS)
        #  Center : Distance & bearing from ownship (e.g. "12.2nm@179°")
        #  Right : Vertical speed (signed, no " fpm" suffix)
        # Alignment is approximate using a monospaced width heuristic because
        # the Canvas API does not expose text measurement.
        callsign = focus.callsign.upper() if focus.callsign else focus.icao.upper()
        header_font = 14  # unify font size
        header_y = y0 + 8
        left_x = x0 + 8

        def _approx_text_w(txt: str, size_px: int) -> int:
            # Heuristic: ~0.6 * font size per character (matches other modules)
            return int(round(len(txt) * size_px * 0.6))

        # Vertical speed label & color (gradient red->green). Remove suffix.
        vs = focus.vertical_rate_fpm
        if isinstance(vs, (int, float)) and math.isfinite(vs):
            mag = max(-_VS_CLAMP_ABS_FPM, min(_VS_CLAMP_ABS_FPM, float(vs)))
            t = (mag + _VS_CLAMP_ABS_FPM) / (2.0 * _VS_CLAMP_ABS_FPM)
            r = int(round(C_VS_NEG[0] + t * (C_VS_POS[0] - C_VS_NEG[0])))
            g = int(round(C_VS_NEG[1] + t * (C_VS_POS[1] - C_VS_NEG[1])))
            b = int(round(C_VS_NEG[2] + t * (C_VS_POS[2] - C_VS_NEG[2])))
            vs_color: Color = (r, g, b, 255)
            vs_label = f"{vs:+.0f}"
        else:
            vs_color = C_VS_NEUTRAL
            vs_label = "--"

        # Distance / bearing center text.
        if (
            isinstance(focus.distance_nm, (int, float))
            and math.isfinite(float(focus.distance_nm))
            and isinstance(focus.bearing_deg, (int, float))
            and math.isfinite(float(focus.bearing_deg))
        ):
            dist_str = f"{float(focus.distance_nm):.1f}"
            bearing_int = int(round(float(focus.bearing_deg))) % 360
            # Zero-pad bearing to 3 digits per request (e.g., 005°, 090°, 179°)
            mid_label = f"{dist_str}nm@{bearing_int:03d}°"
        else:
            mid_label = "--"

        # Compute placement.
        right_margin = 8
        panel_w = self._panel_rect[2] if self._panel_rect else size[0]
        right_x_end = x0 + panel_w - right_margin
        vs_w = _approx_text_w(vs_label, header_font)
        vs_x = right_x_end - vs_w
        mid_w = _approx_text_w(mid_label, header_font)
        mid_x = x0 + panel_w // 2 - mid_w // 2

        # Draw in z-order: left, center, right
        canvas.text((left_x, header_y), callsign, size_px=header_font, color=C_TEXT)
        canvas.text((mid_x, header_y), mid_label, size_px=header_font, color=C_MUTED)
        canvas.text((vs_x, header_y), vs_label, size_px=header_font, color=vs_color)

        # Use only historical points for scaling; projection is excluded per
        # requirement (no future trace / projection in display extent).
        all_points = state.history_points
        alts = [pt[1] for pt in all_points if pt[1] is not None]
        if not alts:
            return
        obs_min = min(alts)
        obs_max = max(alts)
        # Axis origin (bottom) forced to whole 1,000 ft at/below observed min.
        y_min = math.floor(obs_min / 1000.0) * 1000.0
        # Axis top is observed max rounded UP to next 1,000 ft.
        y_max = math.ceil(obs_max / 1000.0) * 1000.0
        if y_max <= y_min:
            y_max = y_min + 1000.0  # ensure some span

        window_start, window_end = state.window
        # Reduced internal padding for denser presentation.
        graph_x0 = x0 + 52  # room for FL labels (FLxxx)
        graph_x1 = max(graph_x0 + 20, x0 + panel_w - 12)
        graph_y0 = y0 + panel_h - 18
        # Add a 3px margin above the chart so the highest trace point does
        # not butt directly against the panel interior/top content.
        graph_y1 = y0 + 28 + 3

        # Provide colors to axis draw helpers by temporarily assigning module
        # names (helpers reference COLOR_AXIS / COLOR_MUTED). This keeps API
        # simple without threading extra parameters everywhere.
        global COLOR_AXIS, COLOR_MUTED
        COLOR_AXIS = C_AXIS
        COLOR_MUTED = C_MUTED
        self._draw_axes(
            canvas,
            graph_x0,
            graph_y1,
            graph_x1,
            graph_y0,
            y_min,
            y_max,
            window_start,
            window_end,
            state.now_ts,
        )
        # Remove the final segment leading into the current point to avoid a
        # line visually connecting the FPM label area and the current marker.
        # (Assumption: request refers to this terminal segment.)
        hist_for_line: list[tuple[float, float]]
        if len(state.history_points) >= 2:
            hist_for_line = state.history_points[:-1]
        else:
            hist_for_line = state.history_points
        self._draw_trace(
            canvas,
            hist_for_line,
            graph_x0,
            graph_y1,
            graph_x1,
            graph_y0,
            window_start,
            window_end,
            y_min,
            y_max,
            C_TRACE,
        )
        # Current point marker removed per latest request.

    def panel_rect(self) -> tuple[int, int, int, int] | None:
        return self._panel_rect
