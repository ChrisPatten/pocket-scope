from __future__ import annotations

from pathlib import Path
from typing import Sequence, Tuple

from pocketscope.data.sectors import Sector, load_sectors_json
from pocketscope.render.sectors_layer import SectorsLayer


class FakeCanvas:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def clear(self, color: Tuple[int, int, int, int]) -> None:  # pragma: no cover
        self.calls.append(("clear", (color,), {}))

    def line(
        self,
        p0: Tuple[int, int],
        p1: Tuple[int, int],
        width: int = 1,
        color=(255, 255, 255, 255),
    ) -> None:  # pragma: no cover
        self.calls.append(("line", (p0, p1), {"width": width, "color": color}))

    def circle(
        self,
        center: Tuple[int, int],
        radius: int,
        width: int = 1,
        color=(255, 255, 255, 255),
    ) -> None:  # pragma: no cover
        self.calls.append(
            ("circle", (center, radius), {"width": width, "color": color})
        )

    def filled_circle(
        self, center: Tuple[int, int], radius: int, color=(255, 255, 255, 255)
    ) -> None:  # pragma: no cover
        self.calls.append(("filled_circle", (center, radius), {"color": color}))

    def polyline(
        self, pts: Sequence[Tuple[int, int]], width: int = 1, color=(255, 255, 255, 255)
    ) -> None:
        self.calls.append(("polyline", (list(pts),), {"width": width, "color": color}))

    def text(
        self,
        pos: Tuple[int, int],
        s: str,
        size_px: int = 12,
        color=(255, 255, 255, 255),
    ) -> None:
        self.calls.append(("text", (pos, s), {"size_px": size_px, "color": color}))


def test_load_and_normalize(fixtures_dir: Path) -> None:
    sectors = load_sectors_json(str(fixtures_dir / "sectors_sample.json"))
    names = {s.name for s in sectors}
    assert "ZBW37" in names and "ZBW38" in names
    # Must have 4 points each
    for s in sectors:
        assert len(s.points) == 4


def test_draw_square_polyline_and_label(fixtures_dir: Path) -> None:
    sectors = load_sectors_json(str(fixtures_dir / "sectors_sample.json"))
    # Choose the ZBW37 one, a ~square around (42.0, -71.1)
    s = [x for x in sectors if x.name == "ZBW37"][0]

    canvas = FakeCanvas()
    layer = SectorsLayer()
    center = (42.0, -71.1)
    layer.draw(
        canvas,
        center_lat=center[0],
        center_lon=center[1],
        range_nm=50.0,
        sectors=[s],
        screen_size=(320, 480),
    )

    # Expect at least one polyline call with 4-5 vertices (closed)
    polylines = [c for c in canvas.calls if c[0] == "polyline"]
    assert polylines, "expected a polyline call"
    pts = polylines[0][1][0]
    assert 4 <= len(pts) <= 5
    # Expect a label text call with the sector name
    texts = [c for c in canvas.calls if c[0] == "text" and c[1][1] == "ZBW37"]
    assert texts, "expected sector label text"


def test_culling_far_sector() -> None:
    # Sector placed far away
    far_sector = Sector(
        name="FAR",
        points=[
            (10.0, 10.0),
            (10.1, 10.0),
            (10.1, 10.1),
            (10.0, 10.1),
        ],
    )
    canvas = FakeCanvas()
    layer = SectorsLayer()
    center = (42.0, -71.1)
    layer.draw(
        canvas,
        center_lat=center[0],
        center_lon=center[1],
        range_nm=50.0,
        sectors=[far_sector],
        screen_size=(320, 480),
    )

    # Expect no polyline calls due to culling at > 2x range
    polylines = [c for c in canvas.calls if c[0] == "polyline"]
    assert not polylines
