from __future__ import annotations

from typing import Any

from pocketscope.core.geo import dest_point
from pocketscope.render.labels import DataBlockFormatter, OwnshipRef, TrackSnapshot


def make_track(**kw: Any) -> TrackSnapshot:
    return TrackSnapshot(
        icao24=kw.get("icao24", "abc123"),
        callsign=kw.get("callsign", "DAL123"),
        lat=kw.get("lat", 42.0),
        lon=kw.get("lon", -71.0),
        geo_alt_ft=kw.get("geo_alt_ft", None),
        baro_alt_ft=kw.get("baro_alt_ft", None),
        ground_speed_kt=kw.get("ground_speed_kt", None),
        vertical_rate_fpm=kw.get("vertical_rate_fpm", None),
        emitter_type=kw.get("emitter_type", None),
        pinned=kw.get("pinned", False),
        focused=kw.get("focused", False),
    )


def test_alt_formatting_rules() -> None:
    fmt = DataBlockFormatter(OwnshipRef(42.0, -71.0))

    # 2600 ft with +600 fpm -> 026+
    t = make_track(baro_alt_ft=2600.0, vertical_rate_fpm=600.0)
    l1, l2, l3 = fmt.format_standard(t)
    assert l2 == "026+"

    # 35000 ft 0 fpm -> 350
    t = make_track(baro_alt_ft=35000.0, vertical_rate_fpm=0.0)
    l1, l2, l3 = fmt.format_standard(t)
    assert l2 == "350"

    # unknown alt or 50 ft -> 000
    t = make_track(baro_alt_ft=None)
    assert fmt.format_standard(t)[1] == "000"
    t = make_track(baro_alt_ft=50.0)
    assert fmt.format_standard(t)[1] == "000"


def test_speed_rounding() -> None:
    fmt = DataBlockFormatter(OwnshipRef(42.0, -71.0))

    def spd(gs: float | None) -> str:
        t = make_track(ground_speed_kt=gs)
        return fmt._format_brg_spd(t).split()[1]

    assert spd(447.0) == "45"
    assert spd(452.0) == "45"
    assert spd(455.0) == "46"
    assert spd(None) == "00"


def test_bearing_zero_padded() -> None:
    own = OwnshipRef(42.00748, -71.20899)
    fmt = DataBlockFormatter(own)
    # Take a destination roughly NE at 45 deg, 1 nm
    lat2, lon2 = dest_point(own.lat, own.lon, 45.0, 1.0)
    t = make_track(lat=lat2, lon=lon2)
    brg = fmt._format_brg_spd(t).split()[0]
    assert brg == "045"


def test_expanded_formatting() -> None:
    own = OwnshipRef(42.0, -71.0)
    fmt = DataBlockFormatter(own)
    t = make_track(
        callsign="JBU123",
        icao24="a1b2c3",
        baro_alt_ft=34000.0,
        vertical_rate_fpm=-1200.0,
        ground_speed_kt=460.0,
        emitter_type="L2J",
    )
    l1, l2, l3 = fmt.format_expanded(t)
    assert l1 == "JBU123 | A1B2C3"
    assert l2 == "340- | -1200"
    assert l3.startswith("000 46 | L2J")  # bearing depends on lat/lon
