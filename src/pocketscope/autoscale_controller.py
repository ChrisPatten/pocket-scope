"""Automatic zoom and altitude filter controller.

Implements an adaptive controller that keeps roughly N_target aircraft
visible by adjusting map radius and altitude band. The controller is pure
logic: callers supply state accessors via *state_adapter*.
"""
from __future__ import annotations

import heapq
import time
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass
class _Settings:
    enabled: bool = True
    target_count: int = 12
    deadband_low_ratio: float = 0.8
    deadband_high_ratio: float = 1.2
    ema_alpha: float = 0.4
    confirm_ticks: int = 2
    radius_nm_min: float = 3.0
    radius_nm_max: float = 60.0
    zoom_step_factor_in: float = 0.8696
    zoom_step_factor_out: float = 1.15
    alt_min_floor_ft: int = 0
    alt_max_ceiling_ft: int = 45000
    alt_step_expand_ft: int = 2000
    alt_margin_ft: int = 500
    alt_min_band_ft: int = 1500
    zoom_cooldown_s: float = 2.0
    alt_cooldown_s: float = 2.5
    max_changes_per_5s: int = 2
    prefer_zoom_bias: float = 0.7
    protect_focused: bool = True
    include_high_when_quiet: bool = True


def _snap_alt(value: float | int | None) -> int | None:
    if value is None:
        return None
    v = int(round(float(value) / 500.0) * 500)
    if v < 0:
        v = 0
    return v


def _snap_radius(value: float | int | None) -> float | None:
    if value is None:
        return None
    return float(value)


class AutoScaleController:
    """Adaptive auto-zoom/altitude controller."""

    def __init__(
        self, state_adapter: Any, settings: dict[str, Any] | None = None
    ) -> None:
        self._state = state_adapter
        self._settings = _Settings(**(settings or {}))
        self._ema: float | None = None
        self._last_zoom_change: float = 0.0
        self._last_alt_change: float = 0.0
        self._over_ticks = 0
        self._under_ticks = 0
        self._recent_changes: list[float] = []
        self._status: str = ""

    # ------------------------------------------------------------------
    def reload_settings(self, new_settings: dict[str, Any]) -> None:
        self._settings = _Settings(**new_settings)

    def reset(self) -> None:
        self._ema = None
        self._over_ticks = 0
        self._under_ticks = 0
        self._recent_changes.clear()
        self._last_zoom_change = 0.0
        self._last_alt_change = 0.0

    # ------------------------------------------------------------------
    def _count_visible(
        self,
        aircraft_list: Sequence[Any],
        radius: float,
        alt_min: int | None,
        alt_max: int | None,
    ) -> tuple[int, list[int]]:
        visible = 0
        alts: list[int] = []
        in_view = getattr(self._state, "in_view", None)
        for a in aircraft_list:
            alt = self.alt(a)
            if alt is None:
                continue
            if alt_min is not None and alt < alt_min:
                continue
            if alt_max is not None and alt >= alt_max:
                continue
            if callable(in_view):
                try:
                    if not in_view(a):
                        continue
                except Exception:
                    continue
            visible += 1
            alts.append(int(alt))
        return visible, alts

    # ------------------------------------------------------------------
    @staticmethod
    def alt(a: Any) -> int | None:
        for key in ("geo_alt", "baro_alt", "alt", "alt_ft"):
            v = getattr(a, key, None) if not isinstance(a, dict) else a.get(key)
            if isinstance(v, (int, float)):
                return int(v)
        return None

    # ------------------------------------------------------------------
    def status_string(self) -> str:
        return self._status

    # ------------------------------------------------------------------
    def tick(
        self,
        aircraft_list: Sequence[Any],
        focused: Any | None = None,
        now: float | None = None,
    ) -> dict[str, float | int | None]:
        if not self._settings.enabled:
            return {"radius_nm": None, "alt_min_ft": None, "alt_max_ft": None}
        now = now if now is not None else time.monotonic()
        radius = float(self._state.get_radius_nm())
        alt_min, alt_max = self._state.get_alt_band_ft()
        visible, alts = self._count_visible(aircraft_list, radius, alt_min, alt_max)

        if self._ema is None:
            self._ema = float(visible)
        else:
            alpha = float(self._settings.ema_alpha)
            self._ema = alpha * float(visible) + (1.0 - alpha) * float(self._ema)

        target = float(self._settings.target_count)
        n_lo = target * float(self._settings.deadband_low_ratio)
        n_hi = target * float(self._settings.deadband_high_ratio)
        act: dict[str, float | int | None] = {
            "radius_nm": None,
            "alt_min_ft": None,
            "alt_max_ft": None,
        }

        too_many = self._ema > n_hi
        too_few = self._ema < n_lo
        if too_many:
            self._over_ticks += 1
        else:
            self._over_ticks = 0
        if too_few:
            self._under_ticks += 1
        else:
            self._under_ticks = 0
        changed = False

        def can_change(last: float, cooldown: float) -> bool:
            if now - last < cooldown:
                return False
            # Limit aggregate changes per 5s window
            self._recent_changes = [t for t in self._recent_changes if now - t < 5.0]
            return len(self._recent_changes) < int(self._settings.max_changes_per_5s)

        # Too many aircraft -------------------------------------------------
        if self._over_ticks >= int(self._settings.confirm_ticks) and too_many:
            # Prefer zoom
            if radius > self._settings.radius_nm_min and can_change(
                self._last_zoom_change, self._settings.zoom_cooldown_s
            ):
                radius = max(
                    radius * float(self._settings.zoom_step_factor_in),
                    self._settings.radius_nm_min,
                )
                act["radius_nm"] = _snap_radius(radius)
                self._last_zoom_change = now
                self._recent_changes.append(now)
                changed = True
            elif (
                can_change(self._last_alt_change, self._settings.alt_cooldown_s)
                and alts
            ):
                k = min(len(alts), int(target))
                if k > 0:
                    # altitude of N_target-th lowest
                    thresh = heapq.nsmallest(k, alts)[-1]
                    new_max = max(
                        (alt_min or 0) + int(self._settings.alt_min_band_ft),
                        thresh + int(self._settings.alt_margin_ft),
                    )
                    new_max = min(new_max, int(self._settings.alt_max_ceiling_ft))
                    if alt_max is None or new_max < alt_max:
                        act["alt_max_ft"] = _snap_alt(new_max)
                        self._last_alt_change = now
                        self._recent_changes.append(now)
                        changed = True
        # Too few aircraft --------------------------------------------------
        elif self._under_ticks >= int(self._settings.confirm_ticks) and too_few:
            if radius < self._settings.radius_nm_max and can_change(
                self._last_zoom_change, self._settings.zoom_cooldown_s
            ):
                radius = min(
                    radius * float(self._settings.zoom_step_factor_out),
                    self._settings.radius_nm_max,
                )
                act["radius_nm"] = _snap_radius(radius)
                self._last_zoom_change = now
                self._recent_changes.append(now)
                changed = True
            elif can_change(self._last_alt_change, self._settings.alt_cooldown_s):
                if alt_max is not None and alt_max < int(
                    self._settings.alt_max_ceiling_ft
                ):
                    new_max = min(
                        alt_max + int(self._settings.alt_step_expand_ft),
                        int(self._settings.alt_max_ceiling_ft),
                    )
                    act["alt_max_ft"] = _snap_alt(new_max)
                    self._last_alt_change = now
                    self._recent_changes.append(now)
                    changed = True
                elif alt_min is not None and alt_min > int(
                    self._settings.alt_min_floor_ft
                ):
                    new_min = max(
                        int(self._settings.alt_min_floor_ft),
                        alt_min - int(self._settings.alt_step_expand_ft),
                    )
                    act["alt_min_ft"] = _snap_alt(new_min)
                    self._last_alt_change = now
                    self._recent_changes.append(now)
                    changed = True

        # Focused aircraft protection --------------------------------------
        if focused is not None and self._settings.protect_focused:
            f_alt = self.alt(focused)
            if f_alt is not None:
                if act["alt_max_ft"] is not None and f_alt > int(act["alt_max_ft"]):
                    act["alt_max_ft"] = _snap_alt(
                        f_alt + int(self._settings.alt_margin_ft)
                    )
                elif (
                    act["alt_max_ft"] is None
                    and alt_max is not None
                    and f_alt > alt_max
                ):
                    act["alt_max_ft"] = _snap_alt(
                        f_alt + int(self._settings.alt_margin_ft)
                    )
                if hasattr(self._state, "ensure_in_view"):
                    try:
                        self._state.ensure_in_view(focused)
                    except Exception:
                        pass

        # Update status string when changed
        if changed:
            r = act["radius_nm"] if act["radius_nm"] is not None else radius
            lo = act["alt_min_ft"] if act["alt_min_ft"] is not None else alt_min
            hi = act["alt_max_ft"] if act["alt_max_ft"] is not None else alt_max
            lo_str = "0" if lo is None else str(int(lo / 1000)) + "k"
            hi_str = "∞" if hi is None else str(int(hi / 1000)) + "k"
            self._status = (
                f"Auto: {visible} in view · {r:.0f} nm · {lo_str}-{hi_str} ft"
            )
        return act


# ---------------------------------------------------------------------------
def _demo() -> None:  # pragma: no cover - simple harness
    import argparse
    import random

    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if not args.demo:
        return
    state = type("S", (), {})()
    state.radius_nm = 10.0
    state.alt_min_ft = 0
    state.alt_max_ft = 45000
    state.get_radius_nm = lambda: state.radius_nm
    state.get_alt_band_ft = lambda: (state.alt_min_ft, state.alt_max_ft)

    def in_view(a: tuple[float, float]) -> bool:
        return a[0] <= float(state.radius_nm)

    state.in_view = in_view

    ctrl = AutoScaleController(state, {})
    rng = random.Random(0)
    now = 0.0
    for i in range(60):
        # ring of aircraft with distance alt
        aircraft = [
            (rng.uniform(0, 80), rng.uniform(0, 45000))
            for _ in range(rng.randint(0, 40))
        ]
        ac_list = [{"geo_alt": alt, "dist": dist} for dist, alt in aircraft]
        state.in_view = lambda a, st=state: a["dist"] <= st.radius_nm
        updates = ctrl.tick(ac_list, now=now)
        if updates["radius_nm"] is not None:
            state.radius_nm = updates["radius_nm"]
        if updates["alt_min_ft"] is not None:
            state.alt_min_ft = updates["alt_min_ft"]
        if updates["alt_max_ft"] is not None:
            state.alt_max_ft = updates["alt_max_ft"]
        print(f"{i:02d} {updates} {ctrl.status_string()}")
        now += 1.0


if __name__ == "__main__":  # pragma: no cover - demo entry
    _demo()
