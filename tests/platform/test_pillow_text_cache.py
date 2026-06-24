"""Pixel-equivalence tests for the cached label rasterisation in PillowCanvas.

The glyph-tile cache must reproduce ``ImageDraw.text`` exactly, otherwise it
would silently shift or recolour every label in the UI. These tests render the
same text through the direct path and the cached path and assert byte-identical
images, plus exercise the cache's hit/fallback behaviour.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from pocketscope.platform.display.pillow_canvas import FontCache, PillowCanvas


def _direct(text: str, size: int, color, pos=(7, 11), wh=(120, 40)) -> bytes:
    img = Image.new("RGBA", wh, (0, 0, 0, 255))
    fonts = FontCache()
    font = fonts.get(size)
    ImageDraw.Draw(img).text(pos, text, fill=color, font=font)
    return img.tobytes()


def _cached(text: str, size: int, color, pos=(7, 11), wh=(120, 40)) -> bytes:
    img = Image.new("RGBA", wh, (0, 0, 0, 255))
    canvas = PillowCanvas(img, FontCache())
    canvas.text(pos, text, size_px=size, color=color)
    return img.tobytes()


@pytest.mark.parametrize(
    "text,size,color",
    [
        ("ZBW37", 10, (255, 255, 255, 255)),
        ("KBOS 12,500", 12, (200, 220, 255, 255)),
        ("N", 14, (255, 0, 0, 255)),
        ("0123456789", 12, (0, 255, 0, 255)),
        ("gjpqy", 16, (255, 255, 0, 255)),  # descenders exercise the y-offset
    ],
)
def test_cached_text_matches_direct(text: str, size: int, color) -> None:
    assert _cached(text, size, color) == _direct(text, size, color)


def test_cache_reuses_tile() -> None:
    fonts = FontCache()
    img = Image.new("RGBA", (120, 40), (0, 0, 0, 255))
    canvas = PillowCanvas(img, fonts)
    canvas.text((5, 5), "STATIC", size_px=12, color=(255, 255, 255, 255))
    assert len(fonts.tiles) == 1
    # A second draw of the same (text, size) must hit the cache.
    canvas.text((30, 20), "STATIC", size_px=12, color=(255, 255, 255, 255))
    assert len(fonts.tiles) == 1
    # The coverage mask is color-independent, so a different color reuses it.
    canvas.text((5, 5), "STATIC", size_px=12, color=(255, 0, 0, 255))
    assert len(fonts.tiles) == 1
    # A different size is a distinct mask.
    canvas.text((5, 5), "STATIC", size_px=14, color=(255, 255, 255, 255))
    assert len(fonts.tiles) == 2


def test_translucent_falls_back_to_direct() -> None:
    color = (255, 255, 255, 128)
    fonts = FontCache()
    img = Image.new("RGBA", (120, 40), (0, 0, 0, 255))
    canvas = PillowCanvas(img, fonts)
    canvas.text((7, 11), "FADED", size_px=12, color=color)
    # Translucent fill is not cached (uses the direct alpha-compositing path).
    assert len(fonts.tiles) == 0
    assert img.tobytes() == _direct("FADED", 12, color)


def test_empty_string_is_noop() -> None:
    fonts = FontCache()
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 255))
    before = img.tobytes()
    PillowCanvas(img, fonts).text((1, 1), "", size_px=12, color=(255, 255, 255, 255))
    assert img.tobytes() == before
    assert len(fonts.tiles) == 0
