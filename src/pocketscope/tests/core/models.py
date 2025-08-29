from datetime import datetime, timezone, timedelta

import pytest
from pocketscope.core.models import (
    AdsbMessage,
    GpsFix,
    ImuSample,
    AircraftTrack,
)


def _ts(n: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=n)


def test_adsb_message_valid():
    m = AdsbMessage(
        ts=_ts(),
        icao24="a1b2c3",
        callsign="TEST123",
        lat=40.0,
        lon=-74.0,
        baro_alt=32000,
        geo_alt=32500.0,  # Added required geo_alt
        ground_speed=450.0,
        track_deg=270.0,
        vertical_rate=-800.0,
        src="SBS",
    )
    js = m.model_dump_json()
    m2 = AdsbMessage.model_validate_json(js)
    assert m2.icao24 == "a1b2c3"
    assert m2.lat == 40.0
    assert m2.src == "SBS"


def test_adsb_message_bad_icao():
    with pytest.raises(ValueError):
        AdsbMessage(
            ts=_ts(),
            icao24="zzzzzz",  # invalid hex
            callsign="BAD123",
            lat=40.0,
            lon=-74.0,
            baro_alt=32000,
            geo_alt=32500.0,
            ground_speed=450.0,
            track_deg=270.0,
            vertical_rate=-800.0,
            src="JSON",
        )


def test_gpsfix_roundtrip():
    g = GpsFix(ts=_ts(), lat=51.5, lon=-0.12, alt_m=35.0, speed_mps=3.1)
    js = g.model_dump_json()
    g2 = GpsFix.model_validate_json(js)
    assert g2.lat == pytest.approx(51.5)
    assert g2.speed_mps == pytest.approx(3.1)


def test_imu_sample_create():
    s = ImuSample(
        ts=_ts(),
        ax=0.0,
        ay=0.0,
        az=9.81,
        gx=0.01,
        gy=0.02,
        gz=0.03,
        mx=10.0,
        my=0.0,
        mz=-5.0,
    )
    assert s.az == pytest.approx(9.81)


def test_aircraft_track_history_and_state():
    t = AircraftTrack(icao24="abc123", last_ts=_ts())
    p1 = (_ts(1), 40.0, -74.0, 32000.0)
    p2 = (_ts(2), 40.1, -74.1, 32100.0)
    t.add_point(p1)
    t.add_point(p2)
    t.state["heading"] = 270.0
    assert len(t.history) == 2
    assert t.last_ts == p2[0]
    js = t.model_dump_json()
    t2 = AircraftTrack.model_validate_json(js)
    assert t2.history[-1][2] == pytest.approx(-74.1)
