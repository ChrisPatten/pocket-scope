from pocketscope.theme import (
    REQUIRED_KEYS,
    THEMES,
    ThemeManager,
    hex_color,
    to_rgb565,
)


def test_atc_classic_key_coverage() -> None:
    palette = THEMES.get("atc_classic")
    assert palette is not None, "atc_classic theme missing"
    missing = [k for k in REQUIRED_KEYS if k not in palette]
    assert not missing, f"missing keys in atc_classic: {missing}"


def test_hex_color_and_rgb565() -> None:
    # Short form + explicit alpha expansion
    c1 = hex_color("#0F0")  # -> 00 FF 00
    assert (c1.r, c1.g, c1.b, c1.a) == (0, 255, 0, 255)
    c2 = hex_color("#11223344")
    assert (c2.r, c2.g, c2.b, c2.a) == (0x11, 0x22, 0x33, 0x44)
    # RGB565 packing (ignore alpha)
    red = hex_color("#FF0000")
    green = hex_color("#00FF00")
    blue = hex_color("#0000FF")
    assert to_rgb565(red) == 0xF800
    assert to_rgb565(green) == 0x07E0
    assert to_rgb565(blue) == 0x001F


def test_theme_manager_overrides() -> None:
    ThemeManager.load({"theme": "atc_classic", "themeOverrides": {"range.ring": "#FFFFFF"}})
    ring = ThemeManager.color("range.ring")
    assert ring[0:3] == (255, 255, 255)