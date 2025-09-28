from __future__ import annotations

from typing import List, Sequence, Tuple

from pocketscope.render.view_ppi import PpiView, TrackSnapshot
from pocketscope.theme import ThemeManager


class _FakeCanvas:
    def __init__(self) -> None:
        self.text_calls: List[tuple[Tuple[int, int], str, int, Tuple[int, int, int, int]]] = []
        self.line_calls: List[tuple[Tuple[int, int], Tuple[int, int], int, Tuple[int, int, int, int]]] = []
        self.cleared: Tuple[int, int, int, int] | None = None

    # Minimal API required by PpiView
    def clear(self, color):  # type: ignore[override]
        self.cleared = color

    def line(self, p0, p1, width: int = 1, color=(255, 255, 255, 255)):
        self.line_calls.append((p0, p1, width, color))

    def circle(self, *args, **kwargs):  # pragma: no cover - not used here
        pass

    def filled_circle(self, *args, **kwargs):  # pragma: no cover - not used here
        pass

    def polyline(self, *args, **kwargs):  # pragma: no cover - not used
        pass

    def text(self, pos, s: str, size_px: int = 12, color=(255, 255, 255, 255)):
        self.text_calls.append((pos, s, size_px, color))


def _bbox(pos: Tuple[int, int], text: str, size_px: int) -> Tuple[int, int, int, int]:
    # Mirror logic in simple label pass: char_w ≈ 0.6 * font_px (rounded)
    char_w = max(6, int(round(size_px * 0.6)))
    w = len(text) * char_w
    h = size_px
    return (pos[0], pos[1], w, h)


def _intersects(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def test_simple_labels_do_not_overlap_and_have_halo() -> None:
    ThemeManager.load({})
    # Two tracks very close so naive placement would overlap.
    t1 = TrackSnapshot(
        icao="abc111",
        callsign="AAA",
        lat=42.0,
        lon=-71.0,
        baro_alt_ft=30000,
        geo_alt_ft=None,
        ground_speed_kt=400,
        vertical_rate_fpm=0,
    )
    t2 = TrackSnapshot(
        icao="abc222",
        callsign="BBB",
        lat=42.00005,  # ~very small offset
        lon=-71.00005,
        baro_alt_ft=31000,
        geo_alt_ft=None,
        ground_speed_kt=410,
        vertical_rate_fpm=0,
    )
    view = PpiView(show_data_blocks=False, show_simple_labels=True)
    canvas = _FakeCanvas()
    view.draw(
        canvas,
        size_px=(400, 400),
        center_lat=42.0,
        center_lon=-71.0,
        tracks=[t1, t2],
        map_data={"airports": [], "runways": []},
        sectors=None,
        occlusions=None,
    )
    # Expect two text draws
    assert len([c for c in canvas.text_calls if c[1] in ("AAA", "BBB")]) == 2
    # Build bboxes and ensure non-overlapping
    boxes: List[Tuple[int, int, int, int]] = []
    for pos, text, size_px, _color in canvas.text_calls:
        if text in ("AAA", "BBB"):
            boxes.append(_bbox(pos, text, size_px))
    assert len(boxes) == 2
    assert not _intersects(boxes[0], boxes[1])
    # Halo lines should exist using label.halo color
    halo_color = ThemeManager.color("label.halo")
    assert any(lc[3] == halo_color for lc in canvas.line_calls), "Expected halo background lines for simple labels"
