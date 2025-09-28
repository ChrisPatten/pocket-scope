from pocketscope.ui.status_overlay import StatusOverlay
from pocketscope.settings.schema import Settings
from typing import Sequence, Tuple

Color = tuple[int, int, int, int]

class FakeCanvas:
    def __init__(self):
        self.calls = []
    def clear(self, color: Color | None = None):
        self.calls.append(("clear", color))
    def line(self, p0, p1, width=1, color=(0,0,0,255)):
        self.calls.append(("line", p0, p1, width, color))
    def circle(self, center, radius, width=1, color=(255,255,255,255)):
        self.calls.append(("circle", center, radius, width, color))
    def filled_circle(self, center, radius, color=(0,0,0,255)):
        self.calls.append(("filled_circle", center, radius, color))
    def polyline(self, pts: Sequence[Tuple[int,int]], width=1, color=(255,255,255,255)):
        self.calls.append(("polyline", list(pts), width, color))
    def text(self, pos, s, size_px=12, color=(255,255,255,255)):
        self.calls.append(("text", pos, s, size_px, color))


def test_status_overlay_single_line_draws():
    c = FakeCanvas()
    settings = Settings()  # defaults
    so = StatusOverlay(settings)
    so.draw(
        c,
        settings,
        range_nm=10.0,
        clock_utc="12:34:56Z",
        center_lat=42.0,
        center_lon=-71.0,
        last_update_ts=None,
        ac_counts=(5,3),
    )
    texts = [call for call in c.calls if call[0] == "text"]
    text_strings = [t[2] for t in texts]
    assert any("12:34:56Z" in s for s in text_strings)
    assert any(s.startswith("AC:") for s in text_strings)
    assert len(texts) <= 6  # clock + AC + badge text (LIVE/DELAY/STALE)
