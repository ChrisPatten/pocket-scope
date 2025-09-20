from pocketscope.ui.status_overlay import StatusOverlay


def test_alt_filter_display_less_than_max():
    result = StatusOverlay.format_alt_filter((None, 24000.0))
    assert result == "<FL240"


def test_alt_filter_display_greater_than_min():
    result = StatusOverlay.format_alt_filter((6000.0, None))
    assert result == ">6k"


def test_alt_filter_display_none_when_unbounded():
    result = StatusOverlay.format_alt_filter((None, None))
    assert result == "---"


def test_alt_filter_autoscale_prefix():
    result = StatusOverlay.format_alt_filter((6000.0, None), autoscale=True)
    assert result == "A>6k"
