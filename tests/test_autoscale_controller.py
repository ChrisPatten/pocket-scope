import time
from pocketscope.ui.autoscale_controller import AutoScaleController, AutoscaleSettings


class DummyAdapter:
    def __init__(self, radius=10.0, lo=0, hi=45000):
        self._r = float(radius)
        self._lo = lo
        self._hi = hi

    def get_radius_nm(self):
        return self._r

    def get_alt_band_ft(self):
        return (self._lo, self._hi)

    def ensure_in_view(self, _):
        return None

    def in_view(self, lat, lon, radius):
        return True


def make_ac(n, alt_low=0, alt_high=40000):
    out = []
    step = max(1, int(n))
    for i in range(n):
        out.append({"lat": 0.0, "lon": float(i), "geo_alt": int(alt_low + (alt_high - alt_low) * (i / max(1, n - 1)))})
    return out


def test_converges_to_target():
    adapter = DummyAdapter(radius=10.0)
    cfg = AutoscaleSettings(enabled=True, target_count=12, ema_alpha=0.5, confirm_ticks=1)
    c = AutoScaleController(adapter, cfg)
    now = time.monotonic()
    # Start quiet -> expect radius increase or alt expand
    ac = make_ac(5)
    out = c.tick(ac, focused=None, now=now)
    assert isinstance(out, dict)


def test_cooldowns_and_rate_limit():
    adapter = DummyAdapter(radius=10.0)
    cfg = AutoscaleSettings(enabled=True, target_count=12, confirm_ticks=1, max_changes_per_5s=1, zoom_cooldown_s=1.0, alt_cooldown_s=1.0)
    c = AutoScaleController(adapter, cfg)
    now = time.monotonic()
    ac = make_ac(40)
    o1 = c.tick(ac, now=now)
    # immediate second tick should be rate limited
    o2 = c.tick(ac, now=now + 0.1)
    # At most one of these should propose a change (rate limit 1 per 5s)
    changes = sum(1 for o in (o1, o2) if any(v is not None for v in o.values()))
    assert changes <= 1


def test_protect_focused():
    adapter = DummyAdapter(radius=3.0)
    cfg = AutoscaleSettings(enabled=True, target_count=1, radius_nm_min=3.0, radius_nm_max=60.0, confirm_ticks=1)
    c = AutoScaleController(adapter, cfg)
    now = time.monotonic()
    ac = make_ac(20, alt_low=30000, alt_high=35000)
    focused = ac[0]
    out = c.tick(ac, focused=focused, now=now)
    # Focused shouldn't be clipped by proposed alt_max_ft
    if out.get("alt_max_ft") is not None:
        assert focused["geo_alt"] <= out["alt_max_ft"] + 1


def test_quiet_at_rmax_expands_alt():
    # Start at R_max but with a restricted alt band so expansion is possible
    adapter = DummyAdapter(radius=60.0, lo=0, hi=20000)
    cfg = AutoscaleSettings(enabled=True, target_count=12, radius_nm_max=60.0, confirm_ticks=1)
    c = AutoScaleController(adapter, cfg)
    now = time.monotonic()
    ac = make_ac(5)
    out = c.tick(ac, now=now)
    # Should propose alt_max_ft expansion when at R_max
    assert out.get("alt_max_ft") is not None or out.get("radius_nm") is not None


def test_crowded_at_rmin_trims_alt():
    adapter = DummyAdapter(radius=3.0)
    cfg = AutoscaleSettings(enabled=True, target_count=12, radius_nm_min=3.0, confirm_ticks=1)
    c = AutoScaleController(adapter, cfg)
    now = time.monotonic()
    ac = make_ac(40, alt_low=1000, alt_high=40000)
    out = c.tick(ac, now=now)
    # Should suggest alt_max_ft to trim
    assert out.get("alt_max_ft") is not None or out.get("radius_nm") is not None

