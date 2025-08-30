from __future__ import annotations

from typing import List, Tuple

from pocketscope.render.labels import DataBlockLayout


def bbox(p: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    return p


def intersects(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def test_two_close_anchors_nudge_ne_se() -> None:
    layout = DataBlockLayout((320, 480), font_px=12, line_gap_px=2, block_pad_px=2)
    lines = ("ABC123", "350", "045 46")
    items = [
        ((160, 240), lines, False),
        ((170, 245), lines, False),
    ]
    placements = layout.place_blocks(items)
    assert len(placements) == 2
    # Compute bboxes
    bboxes: List[Tuple[int, int, int, int]] = []
    for p in placements:
        w, h = layout.measure(p.lines)
        bboxes.append((p.x, p.y, w, h))
    assert not intersects(bboxes[0], bboxes[1])


def test_four_same_anchor_distinct_quadrants() -> None:
    layout = DataBlockLayout((320, 480), font_px=12, line_gap_px=2, block_pad_px=2)
    lines = ("ABC123", "350", "045 46")
    items = [
        ((160, 240), lines, False),
        ((160, 240), lines, False),
        ((160, 240), lines, False),
        ((160, 240), lines, False),
    ]
    placements = layout.place_blocks(items)
    assert len(placements) == 4
    bboxes = []
    for p in placements:
        w, h = layout.measure(p.lines)
        bboxes.append((p.x, p.y, w, h))
    # Ensure no overlaps
    for i in range(4):
        for j in range(i + 1, 4):
            assert not intersects(bboxes[i], bboxes[j])


def test_expanded_block_wider_nudge_accounts_width() -> None:
    layout = DataBlockLayout((320, 480), font_px=12, line_gap_px=2, block_pad_px=2)
    std = ("ABC123", "350", "045 46")
    exp = ("ABC123 | ABC123", "350 | +0", "045 46 | L2J")
    items = [
        ((160, 240), std, False),
        ((162, 242), exp, True),
    ]
    placements = layout.place_blocks(items)
    assert len(placements) == 2
    bboxes = []
    for p in placements:
        w, h = layout.measure(p.lines)
        bboxes.append((p.x, p.y, w, h))
    assert not intersects(bboxes[0], bboxes[1])
    # On-screen bounds
    W, H = 320, 480
    for x, y, w, h in bboxes:
        assert 0 <= x <= W - w
        assert 0 <= y <= H - h
