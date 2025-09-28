from __future__ import annotations

"""Tests that US state boundaries use the map.border theme color when rendered.

We construct a minimal map_data structure containing a single square Polygon
around the center so it will not be culled. A fake Canvas captures polyline
draw calls. We then invoke PpiView.draw and assert that at least one polyline
was drawn using ThemeManager.color("map.border").
"""

from typing import List, Sequence, Tuple

from pocketscope.render.view_ppi import PpiView, TrackSnapshot
from pocketscope.theme import ThemeManager


class _FakeCanvas:
    def __init__(self) -> None:
        self.polylines: List[Tuple[Sequence[Tuple[int, int]], int, Tuple[int, int, int, int]]] = []
        self.cleared: Tuple[int, int, int, int] | None = None

    def clear(self, color):  # type: ignore[override]
        self.cleared = color

    def line(self, *args, **kwargs):  # pragma: no cover - not used
        pass

    def circle(self, *args, **kwargs):  # pragma: no cover - not used
        pass

    def filled_circle(self, *args, **kwargs):  # pragma: no cover - not used
        pass

    def polyline(self, pts, width=1, color=(255, 255, 255, 255)):
        self.polylines.append((list(pts), width, color))

    def text(self, *args, **kwargs):  # pragma: no cover - not needed for this test
        pass


def test_states_use_map_border_color() -> None:
    # Ensure theme loaded
    ThemeManager.load({})
    border_color = ThemeManager.color("map.border")

    # Simple square polygon ~0.5 NM across centered near (42,-71)
    # Coordinates form an exterior ring (lon,lat) pairs in GeoJSON style.
    square = {
        "type": "Feature",
        "properties": {"name": "Test State"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-71.001, 42.001],
                    [-70.999, 42.001],
                    [-70.999, 41.999],
                    [-71.001, 41.999],
                    [-71.001, 42.001],
                ]
            ],
        },
    }
    map_data = {"states": [square], "airports": [], "runways": []}

    view = PpiView(range_nm=10.0, show_data_blocks=False, show_simple_labels=False)
    canvas = _FakeCanvas()
    view.draw(
        canvas,
        size_px=(400, 400),
        center_lat=42.0,
        center_lon=-71.0,
        tracks=[],
        map_data=map_data,
        sectors=None,
        occlusions=None,
    )
    # At least one polyline should match the border color
    assert any(pl[2] == border_color for pl in canvas.polylines), (
        "Expected a state boundary polyline with map.border color"
    )
