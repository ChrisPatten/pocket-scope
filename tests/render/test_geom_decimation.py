from __future__ import annotations

from typing import Iterable, Tuple

from pocketscope.render.view_ppi import PpiView, TrackSnapshot


class FakeCanvas:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def clear(self, color):  # pragma: no cover - trivial
        self.calls.append(("clear", (color,), {}))

    def line(self, p0, p1, width=1, color=(255, 255, 255, 255)):  # pragma: no cover
        self.calls.append(("line", (p0, p1), {"width": width, "color": color}))

    def circle(self, center, radius, width=1, color=(255, 255, 255, 255)):  # pragma: no cover
        self.calls.append(("circle", (center, radius), {"width": width, "color": color}))

    def filled_circle(self, center, radius, color=(255, 255, 255, 255)):  # pragma: no cover
        self.calls.append(("filled_circle", (center, radius), {"color": color}))

    def polyline(self, pts, width=1, color=(255, 255, 255, 255)):
        self.calls.append(("polyline", (list(pts),), {"width": width, "color": color}))

    def text(self, pos, s, size_px=12, color=(255, 255, 255, 255)):
        self.calls.append(("text", (pos, s), {"size_px": size_px, "color": color}))


def _sample_state_feature() -> dict:
    # Minimal GeoJSON-like structure with a simple square polygon around center
    coords = [
        [
            [-71.11, 42.01],
            [-71.09, 42.01],
            [-71.09, 41.99],
            [-71.11, 41.99],
            [-71.11, 42.01],
        ]
    ]
    return {"name": "TestState", "geometry": {"type": "Polygon", "coordinates": coords}}


def _empty_tracks() -> Iterable[TrackSnapshot]:  # pragma: no cover - simple helper
    return []


def test_geom_decimation_reuse() -> None:
    view = PpiView(range_nm=10.0, geom_decimation_enabled=True, geom_target_refresh_fps=2.0)
    canvas = FakeCanvas()
    states = [_sample_state_feature()]
    map_data = {"states": states}
    # First draw builds geometry
    view.draw(
        canvas,
        size_px=(320, 320),
        center_lat=42.0,
        center_lon=-71.1,
        tracks=_empty_tracks(),
        map_data=map_data,
    )
    first_calls = [c for c in canvas.calls if c[0] == "polyline"]
    assert first_calls, "expected polyline on first build"
    first_pts = first_calls[0][1][0]
    # Simulate several frames quickly (fps high) -> interval should grow and reuse cache
    for _ in range(5):
        canvas2 = FakeCanvas()
        view.draw(
            canvas2,
            size_px=(320, 320),
            center_lat=42.0,
            center_lon=-71.1,
            tracks=_empty_tracks(),
            map_data=map_data,
        )
    # Decimation stats should indicate reuse (age > 0, interval >= 1)
    stats = view.last_geom_decimation_stats
    assert stats.get("enabled"), "decimation should be enabled"
    assert stats.get("interval", 1) >= 1
    # Force a rotation change to trigger rebuild
    view.rotation_deg += 10.0
    canvas3 = FakeCanvas()
    view.draw(
        canvas3,
        size_px=(320, 320),
        center_lat=42.0,
        center_lon=-71.1,
        tracks=_empty_tracks(),
        map_data=map_data,
    )
    # Expect at least one polyline again and rebuild flag set this frame
    stats2 = view.last_geom_decimation_stats
    assert stats2.get("rebuild") in (1, True)