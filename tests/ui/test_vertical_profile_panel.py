from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from pocketscope.settings.schema import Settings
from pocketscope.ui.vertical_profile import (
    VerticalProfilePanel,
    VerticalProfileSample,
)


def _make_track(
    icao: str,
    *,
    altitudes: list[float],
    vs: float,
    lat: float,
    lon: float,
    speed: float = 250.0,
    heading: float = 90.0,
) -> SimpleNamespace:
    now = datetime.now(UTC)
    history = []
    points = len(altitudes)
    for idx, alt in enumerate(altitudes):
        ts = now - timedelta(seconds=(points - idx - 1) * 30)
        history.append((ts, lat, lon, alt))
    return SimpleNamespace(
        icao24=icao,
        callsign=icao.upper(),
        history=history,
        last_ts=history[-1][0],
        state={
            "vertical_rate": vs,
            "ground_speed": speed,
            "track_deg": heading,
        },
    )


def _sample_from_track(track: SimpleNamespace, *, distance_nm: float) -> VerticalProfileSample:
    return VerticalProfileSample(
        icao=track.icao24,
        callsign=track.callsign,
        lat=track.history[-1][1],
        lon=track.history[-1][2],
        altitude_ft=track.history[-1][3],
        last_ts=track.last_ts.timestamp(),
        distance_nm=distance_nm,
        track=track,
    )


def test_vertical_profile_focuses_highest_vertical_rate() -> None:
    settings = Settings()
    panel = VerticalProfilePanel(settings)
    track_fast = _make_track("abc123", altitudes=[10000.0, 12000.0], vs=1800.0, lat=0.5, lon=0.5)
    track_slow = _make_track("def456", altitudes=[15000.0, 15200.0], vs=400.0, lat=0.6, lon=0.6)
    samples = [
        _sample_from_track(track_fast, distance_nm=8.0),
        _sample_from_track(track_slow, distance_nm=4.0),
    ]
    now_wall = track_fast.last_ts.timestamp()
    state = panel.update(
        samples,
        center_lat=0.0,
        center_lon=0.0,
        now_monotonic=0.0,
        now_wall=now_wall,
    )

    assert state.focus is not None
    assert state.focus.icao == "abc123"
    assert "abc123" in state.info_targets
    assert "def456" in state.info_targets  # closest aircraft should also be tracked
    assert state.history_points
    assert state.window[1] - state.window[0] == 300.0


def test_vertical_profile_manual_step_advances_focus() -> None:
    settings = Settings()
    panel = VerticalProfilePanel(settings)
    track_a = _make_track("aaa111", altitudes=[8000.0, 9000.0], vs=900.0, lat=0.2, lon=0.2)
    track_b = _make_track("bbb222", altitudes=[6000.0, 7000.0], vs=850.0, lat=0.25, lon=0.25)
    samples = [
        _sample_from_track(track_a, distance_nm=10.0),
        _sample_from_track(track_b, distance_nm=12.0),
    ]
    now_wall = track_a.last_ts.timestamp()
    state_initial = panel.update(
        samples,
        center_lat=0.0,
        center_lon=0.0,
        now_monotonic=0.0,
        now_wall=now_wall,
    )
    assert state_initial.focus is not None
    first_focus = state_initial.focus.icao

    panel.step_next(0.0)
    state_next = panel.update(
        samples,
        center_lat=0.0,
        center_lon=0.0,
        now_monotonic=1.0,
        now_wall=now_wall,
    )
    assert state_next.focus is not None
    assert state_next.focus.icao != first_focus
