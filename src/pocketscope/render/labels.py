"""Data block formatting and layout for ATC-style labels.

This module provides:

- DataBlockFormatter: Formats three-line labels in standard and expanded
  modes using fixed-width numeric fields with specific rules for altitude,
  bearing, and speed. All numeric components are zero-padded as specified.
  Altitude is in hundreds of feet with on-ground heuristic and vertical
  trend suffix; bearing is relative to ownship (0..359) padded to 3 digits;
  speed is rounded to nearest 10 kt and shown as two digits representing
  tens of knots.

- DataBlockLayout: Places label blocks near aircraft anchors using fixed
  offset directions (NE, SE, NW, SW). On collision with any previously
  placed block, the layout engine nudges the candidate position outward in
  a small spiral (increasing radius) while keeping the block on screen.
    Leader-line anchor semantics: each item provides an anchor_px (aircraft
  glyph position in screen pixels). The final placement returns the top-left
  block corner and an anchor point for the leader line.

Default usage in the UI
-----------------------
- The live viewer enables data blocks by default. ``PpiView`` builds
    ``labels.TrackSnapshot`` inputs from track state and asks
    ``DataBlockFormatter.format_standard`` for three text lines. These are laid
    out with ``DataBlockLayout.place_blocks``, and leader lines are rendered
    to the nearest block edge.

Inputs/outputs
--------------
- Inputs to the formatter are domain-agnostic numerical fields (altitudes,
    ground speed, vertical rate) plus position for bearing relative to ownship.
- Outputs are always exactly three strings (standard or expanded form).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from pocketscope.core.geo import range_bearing_from


@dataclass
class OwnshipRef:
    lat: float
    lon: float


@dataclass
class TrackSnapshot:
    icao24: str
    callsign: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    geo_alt_ft: Optional[float]
    baro_alt_ft: Optional[float]
    ground_speed_kt: Optional[float]
    vertical_rate_fpm: Optional[float]
    emitter_type: Optional[str] = None
    pinned: bool = False
    focused: bool = False


@dataclass
class BlockPlacement:
    # top-left corner of the block in pixels
    x: int
    y: int
    # anchor point for leader line (aircraft glyph screen px)
    anchor_px: Tuple[int, int]
    # 3 lines of rendered text
    lines: Tuple[str, str, str]
    expanded: bool


class DataBlockFormatter:
    """Formatter for ATC-style three-line data blocks.

    Rules
    -----
    - Line count: always three lines.
    - Standard mode (default):
        1. CALLSIGN if present else ICAO (uppercased).
        2. Altitude in hundreds of feet, zero-padded 3 digits; select
           geometric altitude if available else barometric. If unknown or on
           ground (<100 ft), show 000. Append '+' if vertical_rate_fpm > +500,
           '-' if < -500, else nothing.
        3. Bearing and speed: "BRG SPD" where BRG is relative to ownship
           0..359 padded to 3 digits; SPD is ground speed rounded to nearest
           10 kt, shown as two digits (tens). Unknown speed => 00.
    - Expanded mode (focused or pinned):
        1. "CALLSIGN | ICAO" (uppercase; ICAO always shown).
        2. "ALT[±] | VS" where ALT as above; VS is signed fpm (integer).
        3. "BRG SPD | TYPE" where TYPE is emitter category (e.g., L2J);
           if unknown, omit content after '|'.
    """

    def __init__(self, ownship: OwnshipRef):
        self.own = ownship

    @staticmethod
    def _format_alt_hundreds(
        geo_alt_ft: Optional[float],
        baro_alt_ft: Optional[float],
        vr_fpm: Optional[float],
    ) -> str:
        alt_ft = geo_alt_ft if geo_alt_ft is not None else baro_alt_ft
        if alt_ft is None or alt_ft < 100.0:
            base = 0
        else:
            base = int(round(alt_ft / 100.0))
        base_clamped = max(0, min(999, base))
        alt_str = f"{base_clamped:03d}"
        if vr_fpm is not None:
            if vr_fpm > 500.0:
                return alt_str + "+"
            if vr_fpm < -500.0:
                return alt_str + "-"
        return alt_str

    @staticmethod
    def _format_speed_tens(gs_kt: Optional[float]) -> str:
        if gs_kt is None or not (gs_kt == gs_kt):  # NaN-safe
            return "00"
        tens = int(round(gs_kt / 10.0))
        tens = max(0, min(99, tens))
        return f"{tens:02d}"

    def bearing_deg_rel(self, own: OwnshipRef, lat: float, lon: float) -> int:
        _, brg = range_bearing_from(own.lat, own.lon, lat, lon)
        return int(round(brg)) % 360

    def _format_brg_spd(self, t: TrackSnapshot) -> str:
        if t.lat is None or t.lon is None:
            brg = 0
        else:
            brg = self.bearing_deg_rel(self.own, t.lat, t.lon)
        brg_str = f"{int(brg)%360:03d}"
        spd_str = self._format_speed_tens(t.ground_speed_kt)
        return f"{brg_str} {spd_str}"

    def format_standard(self, t: TrackSnapshot) -> Tuple[str, str, str]:
        ident = (t.callsign or t.icao24).upper()
        alt = self._format_alt_hundreds(
            t.geo_alt_ft, t.baro_alt_ft, t.vertical_rate_fpm
        )
        line2 = alt
        line3 = self._format_brg_spd(t)
        return (ident, line2, line3)

    def format_expanded(self, t: TrackSnapshot) -> Tuple[str, str, str]:
        ident = (t.callsign or t.icao24).upper()
        left = f"{ident} | {t.icao24.upper()}"
        alt = self._format_alt_hundreds(
            t.geo_alt_ft, t.baro_alt_ft, t.vertical_rate_fpm
        )
        vs = 0 if t.vertical_rate_fpm is None else int(round(t.vertical_rate_fpm))
        line2 = f"{alt} | {vs:+d}"
        brg_spd = self._format_brg_spd(t)
        typ = t.emitter_type or ""
        if typ:
            line3 = f"{brg_spd} | {typ}"
        else:
            line3 = f"{brg_spd} | "
        return (left, line2, line3)


class DataBlockLayout:
    """
    Places blocks around aircraft with leader lines and simple overlap nudge.

    Strategy
    --------
    - Each item provides an anchor (aircraft glyph px), lines, and expanded flag.
    - We measure the block (monospace assumption): width = max line length * char_w,
      height = 3 * font_h + 2 * line_gap.
    - Try offset quadrants in fixed order: NE(dx=+8,dy=-8), SE, NW, SW.
    - On collision with any previously placed bbox, nudge outward along a
      small spiral by increasing radius while circling around the initial
      quadrant center. Attempts are capped to keep runtime bounded. If still
      colliding, we keep the last candidate position.
    - Block bboxes are clamped to stay on-screen with small margins.
    """

    def __init__(
        self,
        canvas_size: Tuple[int, int],
        font_px: int = 12,
        line_gap_px: int = 2,
        block_pad_px: int = 2,
    ) -> None:
        self.w, self.h = int(canvas_size[0]), int(canvas_size[1])
        self.font_px = int(font_px)
        self.line_gap_px = int(line_gap_px)
        self.block_pad_px = int(block_pad_px)
        # Assume a typical monospace aspect (approx). Fine for layout tests.
        self.char_w = max(6, int(round(self.font_px * 0.6)))
        self.line_h = self.font_px + self.line_gap_px

    def measure(self, lines: Sequence[str]) -> Tuple[int, int]:
        max_cols = max((len(s) for s in lines), default=0)
        width = max_cols * self.char_w + 2 * self.block_pad_px
        height = 3 * self.font_px + 2 * self.line_gap_px + 2 * self.block_pad_px
        return (width, height)

    @staticmethod
    def _intersects(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)

    def _clamp_bbox(self, x: int, y: int, w: int, h: int) -> Tuple[int, int]:
        x = max(0, min(self.w - w, x))
        y = max(0, min(self.h - h, y))
        return (x, y)

    def place_blocks(
        self, items: Sequence[tuple[Tuple[int, int], Tuple[str, str, str], bool]]
    ) -> list[BlockPlacement]:
        placements: list[BlockPlacement] = []
        occupied: list[Tuple[int, int, int, int]] = []  # x,y,w,h

        # Fixed initial offsets for quadrants
        offsets = [
            (8, -8),  # NE
            (8, 8),  # SE
            (-8, -8),  # NW
            (-8, 8),  # SW
        ]

        for anchor, lines, expanded in items:
            bw, bh = self.measure(lines)
            ax, ay = anchor
            # Candidate positions for top-left based on quadrant
            quads = [
                (ax + offsets[0][0], ay + offsets[0][1] - bh),  # NE above-right
                (ax + offsets[1][0], ay + offsets[1][1]),  # SE below-right
                (ax + offsets[2][0] - bw, ay + offsets[2][1] - bh),  # NW above-left
                (ax + offsets[3][0] - bw, ay + offsets[3][1]),  # SW below-left
            ]

            best_x, best_y = quads[0]

            for qx, qy in quads:
                # Nudge spiral parameters
                step = 6
                max_attempts = 40
                attempts = 0
                x, y = qx, qy

                def bbox_at(px: int, py: int) -> Tuple[int, int, int, int]:
                    return (px, py, bw, bh)

                # Try initial clamped pos
                x, y = self._clamp_bbox(x, y, bw, bh)
                box = bbox_at(x, y)
                # Check collision
                collides = any(self._intersects(box, b) for b in occupied)

                while collides and attempts < max_attempts:
                    # Spiral: move outward in a square spiral pattern
                    # Right, down, left, up... with increasing radius
                    r = 1 + attempts // 4
                    dir_idx = attempts % 4
                    dx = [step * r, 0, -step * r, 0][dir_idx]
                    dy = [0, step * r, 0, -step * r][dir_idx]
                    x = qx + dx
                    y = qy + dy
                    x, y = self._clamp_bbox(x, y, bw, bh)
                    box = bbox_at(x, y)
                    collides = any(self._intersects(box, b) for b in occupied)
                    attempts += 1

                if not collides:
                    best_x, best_y = x, y
                    break
                else:
                    # Keep the last candidate as fallback if nothing fits
                    best_x, best_y = x, y

            placements.append(
                BlockPlacement(
                    x=int(best_x),
                    y=int(best_y),
                    anchor_px=(int(ax), int(ay)),
                    lines=(lines[0], lines[1], lines[2]),
                    expanded=bool(expanded),
                )
            )
            occupied.append((int(best_x), int(best_y), bw, bh))

        return placements
