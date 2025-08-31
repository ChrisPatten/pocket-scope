from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pocketscope.data.sectors import load_sectors_json
from pocketscope.render.view_ppi import PpiView, TrackSnapshot


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(8192)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def test_sectors_golden(tmp_path: Path) -> None:
    # Ensure headless before importing pygame backend
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from pocketscope.platform.display.pygame_backend import PygameDisplayBackend

    # Scene: centered near the sample polygons
    center_lat, center_lon = (42.0, -71.1)

    # No tracks for this golden; just sectors to stabilize image
    snaps: list[TrackSnapshot] = []

    # Load sample sectors (2 polygons)
    sectors = load_sectors_json("tests/data/sectors_sample.json")

    # Render a frame
    display = PygameDisplayBackend(size=(320, 480))
    view = PpiView(range_nm=50.0, show_data_blocks=False, show_simple_labels=False)
    canvas = display.begin_frame()
    view.draw(
        canvas,
        size_px=display.size(),
        center_lat=center_lat,
        center_lon=center_lon,
        tracks=snaps,
        airports=None,
        sectors=sectors,
    )
    display.end_frame()

    # Save and assert hash
    out_path = tmp_path / "golden_sectors.png"
    display.save_png(str(out_path))

    digest = _sha256_file(str(out_path))
    # Fixed expected digest for this drawing; update if rendering changes
    expected = "c57add0c8afad339aba8c726157de193fc84cf75c7edd22255d9376a7713f375"
    assert digest == expected
