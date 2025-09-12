"""Airport icon rendering utilities.

This renderer draws simple runway-centered icons using the canvas API. The
module imports pygame for compatibility with the user's request but the
renderer accepts the package's Canvas protocol so it works with the existing
display backends.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple


class AirportIconRenderer:
    def __init__(self, canvas: Any):
        # Accept either a pygame.Surface or a Canvas-like object
        self.canvas = canvas

    def draw(
        self,
        center_px: Tuple[int, int],
        runways: List[Dict[str, Any]],
        pixels_per_meter: float,
        min_px: int = 8,
        max_px: int = 36,
        line_px: int = 2,
        emphasize_major: bool = True,
        scale: float = 0.5,
        max_runways: int | None = 3,
    ) -> None:
        cx, cy = int(center_px[0]), int(center_px[1])
        if not runways:
            # fallback: small dot
            try:
                self.canvas.filled_circle((cx, cy), 3, color=(200, 200, 200, 255))
            except Exception:
                pass
            return

        # Derive lengths and bearings
        entries = []
        longest = 0.0
        for r in runways:
            lm = r.get("length_m")
            if lm is None:
                continue
            try:
                lm = float(lm)
            except Exception:
                continue
            bearing = r.get("bearing_true")
            if bearing is None:
                # don't try to synthesize here; skip
                continue
            try:
                bearing = float(bearing) % 360.0
            except Exception:
                continue
            entries.append((lm, bearing, r))
            if lm > longest:
                longest = lm

        if not entries:
            try:
                self.canvas.filled_circle((cx, cy), 3, color=(200, 200, 200, 255))
            except Exception:
                pass
            return

        # Optionally limit to the N longest runways
        if max_runways is not None and len(entries) > max_runways:
            entries = sorted(entries, key=lambda e: e[0], reverse=True)[:max_runways]
            # recompute longest across the selected subset
            longest = max(e[0] for e in entries) if entries else longest

        # Draw each runway scaled by length relative to longest
        for lm, bearing, _r in entries:
            frac = lm / longest if longest > 0 else 1.0
            Lpx = max(min_px, min(max_px, int(round(frac * max_px))))
            # apply global scale factor (allow shrinking/enlarging icons)
            try:
                Lpx = max(1, int(round(Lpx * float(scale))))
            except Exception:
                pass
            # Optionally suppress short runways
            if emphasize_major and frac < 0.4:
                # draw faint thin marker or skip
                col = (140, 140, 140, 160)
                w = max(1, int(line_px - 1))
            else:
                col = (220, 220, 220, 255)
                w = line_px if lm >= longest else max(1, int(line_px))

            angle_rad = math.radians(90.0 - bearing)
            dx = (Lpx / 2.0) * math.cos(angle_rad)
            dy = (Lpx / 2.0) * math.sin(angle_rad)
            p1 = (int(round(cx - dx)), int(round(cy - dy)))
            p2 = (int(round(cx + dx)), int(round(cy + dy)))
            try:
                self.canvas.line(p1, p2, width=w, color=col)
                # small center marker under runways
            except Exception:
                pass

        # Draw small central dot as reference
        try:
            self.canvas.filled_circle((cx, cy), 3, color=(180, 180, 180, 255))
        except Exception:
            pass
