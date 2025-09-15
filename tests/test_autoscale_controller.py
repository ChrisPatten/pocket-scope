import random
from pocketscope.autoscale_controller import AutoScaleController


class DummyState:
    def __init__(self, radius=10.0, alt_min=0, alt_max=45000):
        self.radius_nm = radius
        self.alt_min_ft = alt_min
        self.alt_max_ft = alt_max

    # state adapter API -------------------------------------------------
    def get_radius_nm(self):
        return self.radius_nm

    def get_alt_band_ft(self):
        return (self.alt_min_ft, self.alt_max_ft)

    def in_view(self, a):
        return a.get("dist", 0) <= self.radius_nm

    def ensure_in_view(self, target):
        pass


# Helper to apply controller outputs back to state
def apply(state, updates):
    if updates["radius_nm"] is not None:
        state.radius_nm = updates["radius_nm"]
    if updates["alt_min_ft"] is not None:
        state.alt_min_ft = updates["alt_min_ft"]
    if updates["alt_max_ft"] is not None:
        state.alt_max_ft = updates["alt_max_ft"]


def make_ac(count, dist=5, alt=10000):
    return [{"geo_alt": alt, "dist": dist} for _ in range(count)]


def test_cooldowns():
    state = DummyState(radius=10)
    settings = {"target_count": 5, "radius_nm_max": 20.0, "confirm_ticks": 1}
    ctrl = AutoScaleController(state, settings)
    ac = make_ac(0)
    out1 = ctrl.tick(ac, now=3.0)
    apply(state, out1)
    out2 = ctrl.tick(ac, now=3.5)
    assert out2["radius_nm"] is None  # zoom cooldown
    out3 = ctrl.tick(ac, now=6.5)
    assert out3["radius_nm"] is not None


def test_protects_focused_alt():
    state = DummyState(radius=5, alt_min=0, alt_max=15000)
    ctrl = AutoScaleController(state, {"confirm_ticks": 1})
    aircraft = make_ac(40, dist=3, alt=12000)
    focused = {"geo_alt": 30000, "dist": 2}
    aircraft.append(focused)
    out = ctrl.tick(aircraft, focused=focused, now=3.0)
    assert out["alt_max_ft"] >= 30500


def test_quiet_expands_alt():
    state = DummyState(radius=60, alt_min=0, alt_max=10000)
    settings = {"radius_nm_max": 60.0, "target_count": 10, "confirm_ticks": 1}
    ctrl = AutoScaleController(state, settings)
    ac = make_ac(1, dist=2, alt=15000)
    out = ctrl.tick(ac, now=3.0)
    assert out["alt_max_ft"] is not None and out["alt_max_ft"] > 10000


def test_crowded_trims_alt():
    state = DummyState(radius=3, alt_min=0, alt_max=45000)
    settings = {"target_count": 12, "radius_nm_min": 3.0, "confirm_ticks": 1}
    ctrl = AutoScaleController(state, settings)
    aircraft = []
    for i in range(1, 41):
        aircraft.append({"geo_alt": i * 1000, "dist": 2})
    out = ctrl.tick(aircraft, now=3.0)
    assert out["alt_max_ft"] is not None
    assert out["alt_max_ft"] <= 13000


def test_converges_random():
    state = DummyState(radius=10)
    settings = {"target_count": 12, "confirm_ticks": 1}
    ctrl = AutoScaleController(state, settings)
    rng = random.Random(0)
    now = 0.0
    for _ in range(60):
        n = rng.randint(0, 40)
        aircraft = [{"geo_alt": rng.uniform(0, 40000), "dist": rng.uniform(0, 80)} for _ in range(n)]
        updates = ctrl.tick(aircraft, now=now)
        apply(state, updates)
        now += 1.0
    # Allow controller a few extra iterations on the last set to converge
    for _ in range(10):
        vis = sum(1 for a in aircraft if ctrl.alt(a) is not None and a["dist"] <= state.radius_nm)
        n_lo = settings["target_count"] * 0.8
        n_hi = settings["target_count"] * 1.2
        if n_lo <= vis <= n_hi:
            break
        updates = ctrl.tick(aircraft, now=now)
        apply(state, updates)
        now += 1.0
    assert n_lo <= vis <= n_hi
