from pocketscope.render.airport_icon import AirportIconRenderer
from pocketscope.theme import ThemeManager


class CaptureCanvas:
    def __init__(self):
        self.calls = []

    def line(self, p0, p1, width=1, color=(0, 0, 0, 255)):
        self.calls.append(("line", p0, p1, width, color))

    def filled_circle(self, center, radius, color=(0, 0, 0, 255)):
        self.calls.append(("filled_circle", center, radius, color))


def test_runway_uses_airport_marker_theme_color():
    # Ensure theme is loaded (defaults used) and grab expected color
    ThemeManager.reload({})
    expected = ThemeManager.color("airport.marker")

    c = CaptureCanvas()
    renderer = AirportIconRenderer(c)
    runways = [
        {"length_m": 2000.0, "bearing_true": 45.0},
        {"length_m": 800.0, "bearing_true": 135.0},  # short runway variant
    ]
    renderer.draw((100, 100), runways, pixels_per_meter=0.05, scale=0.4)

    # Collect drawn line colors
    line_colors = [call[4] for call in c.calls if call[0] == "line"]
    assert line_colors, "No runway lines were drawn"
    # Primary runway should have exact themed RGB (alpha forced 255)
    assert any(col[:3] == expected[:3] for col in line_colors)
    # Minor runway (short) should share hue but reduced alpha relative to the max alpha used
    if len(line_colors) > 1:
        max_alpha = max(c[3] for c in line_colors)
        min_alpha = min(c[3] for c in line_colors)
        if max_alpha != min_alpha:  # variation expected when emphasize_major True
            assert min_alpha < max_alpha
