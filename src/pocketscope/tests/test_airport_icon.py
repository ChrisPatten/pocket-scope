from pocketscope.render.airport_icon import AirportIconRenderer


class FakeCanvas:
    def __init__(self):
        self.calls = []

    def line(self, p0, p1, width=1, color=(0, 0, 0, 255)):
        self.calls.append(("line", p0, p1, width, color))

    def filled_circle(self, center, radius, color=(0, 0, 0, 255)):
        self.calls.append(("filled_circle", center, radius, color))


def test_airport_icon_draws():
    c = FakeCanvas()
    r = AirportIconRenderer(c)
    # single runway
    runways = [{"length_m": 2000.0, "bearing_true": 90.0}]
    r.draw((50, 50), runways, pixels_per_meter=0.01)
    assert len(c.calls) >= 1
