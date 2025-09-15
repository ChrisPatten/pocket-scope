"""AutoScale controller for PocketScope.

Pure control logic that proposes radius and altitude band changes to keep
~N_target aircraft visible. No I/O or UI here.
"""
from __future__ import annotations

import heapq
import time
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional


@dataclass
class AutoscaleSettings:
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


class AutoScaleController:
    """Controller implementing autoscale logic.

    state_adapter: object providing necessary map/state helpers. It must
      provide at least:
        - get_radius_nm() -> float
        - get_alt_band_ft() -> tuple[int|None,int|None]
        - ensure_in_view(target) -> None  (optional)
        - in_view(lat, lon, radius_nm) -> bool  (optional)

    The controller is pure logic and returns proposals from tick().
    """

    def __init__(
        self, state_adapter: Any, settings: Optional[AutoscaleSettings] = None
    ):
        self._adapter = state_adapter
        self._cfg = settings or AutoscaleSettings()

        # EMA of counts
        self._c_hat: float = float(self._cfg.target_count)
        # consecutive confirm ticks for low/high
        self._low_count = 0
        self._high_count = 0

        # cooldown bookkeeping
        self._last_zoom_t = 0.0
        self._last_alt_t = 0.0
        self._change_timestamps: List[float] = []  # times of changes (for rate limit)

        # status string for UI chip
        self._last_status: str = ""

    # Public API -------------------------------------------------
    def tick(
        self,
        aircraft_list: Iterable[Any],
        focused: Optional[Any] = None,
        now: Optional[float] = None,
    ) -> dict[str, Optional[float]]:
        """Process one control tick and propose changes.

        Returns dict with keys: radius_nm, alt_min_ft, alt_max_ft (None = no change)
        """
        if now is None:
            now = time.monotonic()

        # Proposal dict: None -> no change
        out: dict[str, Optional[float]] = {
            "radius_nm": None,
            "alt_min_ft": None,
            "alt_max_ft": None,
        }

        if not self._cfg.enabled:
            return out

        # Read current state
        try:
            r = float(self._adapter.get_radius_nm())
        except Exception:
            r = float(self._cfg.radius_nm_max)
        try:
            cur_lo, cur_hi = self._adapter.get_alt_band_ft()
        except Exception:
            cur_lo, cur_hi = (self._cfg.alt_min_floor_ft, self._cfg.alt_max_ceiling_ft)

        # Build visible set (within radius and alt band)
        vis_count = 0
        vis_alts: List[int] = []
        focused_alt: Optional[int] = None
        in_view_fn = getattr(self._adapter, "in_view", None)
        for a in aircraft_list:
            try:
                lat = float(a.get("lat"))
                lon = float(a.get("lon"))
            except Exception:
                continue
            # altitude selection
            alt_v = None
            if "geo_alt" in a and a["geo_alt"] is not None:
                try:
                    alt_v = int(a["geo_alt"])
                except Exception:
                    alt_v = None
            elif "baro_alt" in a and a["baro_alt"] is not None:
                try:
                    alt_v = int(a["baro_alt"])
                except Exception:
                    alt_v = None

            # check in viewport
            in_view = True
            if callable(in_view_fn):
                try:
                    in_view = bool(in_view_fn(lat, lon, r))
                except Exception:
                    in_view = True

            if not in_view:
                continue

            # check altitude band
            alt_ok = True
            if cur_lo is not None and alt_v is not None and alt_v < cur_lo:
                alt_ok = False
            if cur_hi is not None and alt_v is not None and alt_v >= cur_hi:
                alt_ok = False

            if alt_ok:
                vis_count += 1
                if alt_v is not None:
                    vis_alts.append(alt_v)

            # focused
            if focused is not None and self._same_target(a, focused):
                focused_alt = alt_v

        # Update EMA
        alpha = float(self._cfg.ema_alpha)
        self._c_hat = alpha * float(vis_count) + (1.0 - alpha) * self._c_hat

        # Deadband thresholds
        n_t = int(self._cfg.target_count)
        n_lo = int(max(0, round(n_t * float(self._cfg.deadband_low_ratio))))
        n_hi = int(max(1, round(n_t * float(self._cfg.deadband_high_ratio))))

        # Determine whether low/high condition holds
        if self._c_hat < n_lo:
            self._low_count += 1
            self._high_count = 0
        elif self._c_hat > n_hi:
            self._high_count += 1
            self._low_count = 0
        else:
            self._low_count = 0
            self._high_count = 0

        changed = False

        # Helper: rate limit
        self._expire_old_changes(now)
        changes_allowed = len(self._change_timestamps) < int(
            self._cfg.max_changes_per_5s
        )

        # Priority: protect focused (ensure in view and in band)
        if focused is not None:
            try:
                if hasattr(self._adapter, "ensure_in_view"):
                    self._adapter.ensure_in_view(focused)
            except Exception:
                pass
            # If focused altitude is outside current band, expand band slightly
            if self._cfg.protect_focused and focused_alt is not None:
                if cur_hi is None or focused_alt >= cur_hi:
                    new_hi = focused_alt + int(self._cfg.alt_margin_ft)
                    new_hi = min(new_hi, int(self._cfg.alt_max_ceiling_ft))
                    if cur_hi is None or new_hi > (cur_hi or 0):
                        out["alt_max_ft"] = self._snap_alt(new_hi)
                        changed = True
                if cur_lo is None or focused_alt < (cur_lo or 0):
                    new_lo = max(
                        int(self._cfg.alt_min_floor_ft),
                        focused_alt - int(self._cfg.alt_margin_ft),
                    )
                    if cur_lo is None or new_lo < (cur_lo or 0):
                        out["alt_min_ft"] = self._snap_alt(new_lo)
                        changed = True

        # If low and confirmed
        if self._low_count >= int(self._cfg.confirm_ticks):
            # Prefer zooming out first
            if (
                r < float(self._cfg.radius_nm_max)
                and (now - self._last_zoom_t) >= float(self._cfg.zoom_cooldown_s)
                and changes_allowed
            ):
                new_r = min(
                    float(self._cfg.radius_nm_max),
                    r * float(self._cfg.zoom_step_factor_out),
                )
                new_r = self._snap_radius(new_r)
                if new_r != r:
                    out["radius_nm"] = new_r
                    self._last_zoom_t = now
                    self._change_timestamps.append(now)
                    changed = True
            else:
                # Expand altitude ceiling toward ceiling
                if (
                    (cur_hi is None or cur_hi < int(self._cfg.alt_max_ceiling_ft))
                    and (now - self._last_alt_t) >= float(self._cfg.alt_cooldown_s)
                    and changes_allowed
                ):
                    target_hi = (cur_hi or 0) + int(self._cfg.alt_step_expand_ft)
                    target_hi = min(target_hi, int(self._cfg.alt_max_ceiling_ft))
                    snap_target_hi = self._snap_alt(target_hi)
                    if cur_hi is None or snap_target_hi > (cur_hi or 0):
                        out["alt_max_ft"] = snap_target_hi
                        self._last_alt_t = now
                        self._change_timestamps.append(now)
                        changed = True

        # If high and confirmed
        if self._high_count >= int(self._cfg.confirm_ticks):
            # Try zooming in first
            if (
                r > float(self._cfg.radius_nm_min)
                and (now - self._last_zoom_t) >= float(self._cfg.zoom_cooldown_s)
                and changes_allowed
            ):
                new_r = max(
                    float(self._cfg.radius_nm_min),
                    r * float(self._cfg.zoom_step_factor_in),
                )
                new_r = self._snap_radius(new_r)
                if new_r != r:
                    out["radius_nm"] = new_r
                    self._last_zoom_t = now
                    self._change_timestamps.append(now)
                    changed = True
            else:
                # Trim altitude ceiling using percentile of visible alts
                if (
                    vis_alts
                    and (now - self._last_alt_t) >= float(self._cfg.alt_cooldown_s)
                    and changes_allowed
                ):
                    # choose N_target-th lowest altitude
                    k = int(self._cfg.target_count)
                    if k < 1:
                        k = 1
                    # If fewer than k altitudes, set to min available + margin
                    nh: Optional[int] = None
                    if len(vis_alts) <= k:
                        nh = min(vis_alts) + int(self._cfg.alt_margin_ft)
                    else:
                        # find kth smallest efficiently
                        kth = heapq.nsmallest(k, vis_alts)[-1]
                        nh = kth + int(self._cfg.alt_margin_ft)
                    # clamp to >= cur_lo + min band
                    min_allowed = (cur_lo or 0) + int(self._cfg.alt_min_band_ft)
                    nh = max(nh, min_allowed)
                    nh = min(nh, int(self._cfg.alt_max_ceiling_ft))
                    nh = self._snap_alt(nh)
                    if cur_hi is None or nh < (cur_hi or int(1e9)):
                        out["alt_max_ft"] = nh
                        self._last_alt_t = now
                        self._change_timestamps.append(now)
                        changed = True

        # Final protection: ensure focused not clipped
        if (
            focused is not None
            and self._cfg.protect_focused
            and focused_alt is not None
        ):
            prop_hi = (
                out.get("alt_max_ft") if out.get("alt_max_ft") is not None else cur_hi
            )
            if prop_hi is not None and focused_alt >= prop_hi:
                new_hi = focused_alt + int(self._cfg.alt_margin_ft)
                new_hi = min(new_hi, int(self._cfg.alt_max_ceiling_ft))
                out["alt_max_ft"] = self._snap_alt(new_hi)
                changed = True

        # Update status string
        # Keep status string under the line-length limit
        status_count = int(round(self._c_hat))
        status_range = round(r, 1)
        status_band = f"{cur_lo or 0}-{cur_hi or self._cfg.alt_max_ceiling_ft}"
        self._last_status = (
            "Auto: "
            + str(status_count)
            + " in view · "
            + str(status_range)
            + " nm · "
            + status_band
            + " ft"
        )

        # If changes recorded, keep timestamps list trimmed
        if changed:
            self._expire_old_changes(now)

        return out

    def _expire_old_changes(self, now: float) -> None:
        # keep only last 5s
        cutoff = now - 5.0
        self._change_timestamps = [t for t in self._change_timestamps if t >= cutoff]

    def _snap_alt(self, v: int) -> int:
        # snap to 500 ft increments
        step = 500
        return int(round(v / step) * step)

    def _snap_radius(self, v: float) -> float:
        # snap radius to 2-decimal precision (small allocation)
        return round(float(v), 2)

    def reset(self) -> None:
        self._c_hat = float(self._cfg.target_count)
        self._low_count = 0
        self._high_count = 0
        self._last_zoom_t = 0.0
        self._last_alt_t = 0.0
        self._change_timestamps.clear()

    def reload_settings(self, new_settings: AutoscaleSettings) -> None:
        self._cfg = new_settings

    def status_string(self) -> str:
        return self._last_status

    # Utility
    @staticmethod
    def _same_target(a: Any, b: Any) -> bool:
        # compare by icao24 or callsign or lat/lon
        try:
            if isinstance(a, dict) and isinstance(b, dict):
                if a.get("icao24") and b.get("icao24"):
                    return str(a.get("icao24")) == str(b.get("icao24"))
                if a.get("callsign") and b.get("callsign"):
                    return str(a.get("callsign")) == str(b.get("callsign"))
                # fallback compare lat/lon
                return float(a.get("lat", 0.0)) == float(b.get("lat", 0.0)) and float(
                    a.get("lon", 0.0)
                ) == float(b.get("lon", 0.0))
        except Exception:
            pass
        return False


if __name__ == "__main__":
    # Simple deterministic demo harness
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    if args.demo:
        # synthetic: ring of aircraft at random altitudes
        from random import randint

        class Adapter:
            def __init__(self) -> None:
                self._r: float = 10.0
                self._lo: int = 0
                self._hi: int = 45000

            def get_radius_nm(self) -> float:
                return self._r

            def get_alt_band_ft(self) -> tuple[int, int]:
                return (self._lo, self._hi)

            def ensure_in_view(self, _: Any) -> None:
                return None

            def in_view(self, lat: float, lon: float, radius: float) -> bool:
                return True

        a = Adapter()
        ctrl = AutoScaleController(a)
        print("Tick, proposals:")
        for t in range(60):
            # vary count: low at start, then high
            if t < 10:
                n = 5
            elif t < 30:
                n = 12
            else:
                n = 40
            ac = []
            for i in range(n):
                ac.append({"lat": 0.0, "lon": float(i), "geo_alt": randint(0, 40000)})
            out = ctrl.tick(ac, focused=ac[0], now=time.monotonic())
            print(t, n, out, ctrl.status_string())
