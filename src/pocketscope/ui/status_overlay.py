"""
Status overlay (HUD) rendering.

Draws a small translucent panel with FPS, range, active track count,
EventBus summary, and a UTC clock. Uses a monospaced font for alignment.

This module renders text via the framework-agnostic Canvas.text API.
For sizing the background panel, it uses pygame's font metrics obtained
through pocketscope.render.fonts.get_mono, but the returned handle is
not required by Canvas; it's only used for measuring text width/height.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from pocketscope.render.canvas import Canvas, Color
from pocketscope.render.fonts import get_mono
from pocketscope.settings.schema import Settings
from pocketscope.settings.values import STATUS_OVERLAY_CONFIG, THEME
from pocketscope.theme import ThemeManager

# Colors / defaults from theme
_SO_THEME = (
    THEME.get("colors", {}).get("status_overlay", {}) if isinstance(THEME, dict) else {}
)


def _c(v: object, fb: tuple[int, int, int, int]) -> Color:
    if (
        isinstance(v, (list, tuple))
        and len(v) == 4
        and all(isinstance(c, (int, float)) for c in v)
    ):
        return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))
    return fb


_COLOR_BG: Color = _c(_SO_THEME.get("bg"), (32, 32, 32, 180))
_COLOR_TEXT: Color = _c(_SO_THEME.get("text"), (255, 255, 255, 255))
_COLOR_BORDER: Color = _c(_SO_THEME.get("border"), (255, 255, 255, 255))


def _measure_text_lines(lines: List[str], *, font_px: int) -> Tuple[int, int]:
    """Measure max width and total height for a list of text lines.

    Uses pygame's Font.size when available (via get_mono), otherwise
    estimates width assuming ~0.6em per character which is sufficient
    to size the translucent background. Height is font_px per line.
    """

    try:
        # Prefer Pillow measurement so sizing matches the Pillow-backed
        # Canvas used by the ILI9341 backend. Try common TTF paths first.
        try:
            from PIL import ImageFont

            candidates = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
                "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
                "/Library/Fonts/Menlo.ttc",
                "/Library/Fonts/Consolas.ttf",
            ]
            font: Any = None  # PIL ImageFont instance or fallback
            for p in candidates:
                try:
                    font = ImageFont.truetype(p, font_px)
                    break
                except Exception:
                    continue
            if font is None:
                try:
                    font = ImageFont.truetype("DejaVuSansMono.ttf", font_px)
                except Exception:
                    font = ImageFont.load_default()
            max_w = 0
            for s in lines:
                try:
                    m = font.getmask(s)
                    w = m.size[0]
                except Exception:
                    w = int(len(s) * font_px * 0.6)
                if w > max_w:
                    max_w = w
            total_h = font_px * len(lines)
            return max_w, total_h
        except Exception:
            # Fall back to pygame-based measurement if Pillow isn't present
            import pygame

            if not pygame.get_init():
                pygame.init()
            if not pygame.font.get_init():
                pygame.font.init()

            fh = get_mono(font_px)
            font_obj: Any = getattr(fh, "obj", None)
            max_w = 0
            for s in lines:
                if font_obj is not None:
                    w, _ = font_obj.size(s)
                else:  # pragma: no cover - fallback
                    w = int(len(s) * font_px * 0.6)
                if w > max_w:
                    max_w = w
            total_h = font_px * len(lines)
            return max_w, total_h
    except Exception:
        # Conservative fallback in environments without Pillow/pygame
        max_len = max((len(s) for s in lines), default=0)
        return int(max_len * font_px * 0.6), font_px * len(lines)


def _fmt_alt(alt_ft: float | None) -> str:
    """Format an altitude in feet into a compact label.

    Uses flight levels (FLnnn) for altitudes >= 10000 ft, otherwise
    returns feet or abbreviated thousands (e.g. 10k).
    """

    if alt_ft is None:
        return "?"
    try:
        af = int(round(float(alt_ft)))
    except Exception:
        return "?"
    if af >= 10000:
        fl = int(round(af / 100.0))
        return f"FL{fl}"
    if af >= 1000 and af % 1000 == 0:
        return f"{af // 1000}k"
    if af >= 1000:
        return f"{af/1000:.1f}k"
    return f"{af}ft"


class StatusOverlay:
    def __init__(
        self,
        settings: Settings,
        *,
        width_px: int | None = None,
        bg_color: Color = _COLOR_BG,
        text_color: Color = _COLOR_TEXT,
        measure_fn: Callable[[str, int], Tuple[int, int]] | None = None,
        elements_layout: List[List[str]] | None = None,
    ) -> None:
        self.font_px = int(getattr(settings, "status_font_px", 12))
        # Compute sensible defaults scaled to the font size when caller
        # doesn't specify explicit padding values. This keeps the overlay
        # visually consistent across font sizes on different displays.
        self.pad_x = max(2, int(round(self.font_px * 0.3)))
        self.pad_y = max(1, int(round(self.font_px * 0.15)))
        # Top/bottom padding used to compute automatic panel height
        # Explicit settings values may be None (schema default) meaning
        # "auto"; only coerce when the attribute is numeric. Previous code
        # attempted int(None) which raised TypeError breaking UI tests.
        _auto_top = max(2, int(round(self.font_px * 0.4)))
        _raw_top = getattr(settings, "status_pad_top_px", None)
        if isinstance(_raw_top, (int, float)):
            self.pad_top = int(_raw_top)
        else:
            self.pad_top = _auto_top
        _auto_bottom = max(2, int(round(self.font_px * 0.25)))
        _raw_bottom = getattr(settings, "status_pad_bottom_px", None)
        if isinstance(_raw_bottom, (int, float)):
            self.pad_bottom = int(_raw_bottom)
        else:
            self.pad_bottom = _auto_bottom
        self.bg_color = bg_color
        self.text_color = text_color
        self.width_px = width_px  # if None we compute dyn based on content
        self._measure_cache: Dict[Tuple[str, int], Tuple[int, int]] = {}
        self._measure_fn = measure_fn or self._measure_text_internal
        # Optional instance-level layout (list of lists per line)
        if elements_layout is not None:
            self._elements_layout = elements_layout
        else:
            # Compact single row: CLOCK | AGE | AC
            self._elements_layout = [["CLOCK", "AGE", "AC"]]

    @staticmethod
    def format_alt_filter(
        alt_filter: tuple[float | None, float | None] | None,
        *,
        autoscale: bool = False,
    ) -> str:
        if alt_filter is None:
            min_ft, max_ft = None, None
        else:
            min_ft, max_ft = alt_filter
        # Treat very small minimum altitudes (<= 500 ft) as unspecified for
        # status display purposes so they don't clutter the label.
        try:
            if isinstance(min_ft, (int, float)) and float(min_ft) <= 500.0:
                min_ft = None
        except Exception:
            pass

        # Autoscale prefix should be a compact 'A' with no trailing space.
        prefix = "A" if autoscale else ""

        if (
            min_ft is None or (isinstance(min_ft, (int, float)) and min_ft < 1000)
        ) and max_ft is not None:
            label = f"<{_fmt_alt(max_ft)}"
        elif max_ft is None and min_ft is not None:
            label = f">{_fmt_alt(min_ft)}"
        elif min_ft is None and max_ft is None:
            label = "---"
        elif min_ft is not None and max_ft is not None:
            label = f"{_fmt_alt(min_ft)}–{_fmt_alt(max_ft)}"
        else:
            label = "---"

        # Do not include the previous 'ALT' label; if autoscale is enabled,
        # prefix with 'A' immediately before the label (no space).
        if prefix:
            return f"{prefix}{label}"
        return label

    # ------------------------------------------------------------------
    def draw(
        self,
        canvas: Canvas,
        settings: Settings,
        *,
        range_nm: float,
        clock_utc: str,
        center_lat: float,
        center_lon: float,
        gps_ok: bool = True,
        imu_ok: bool = True,
        decoder_ok: bool = True,
        last_update_ts: float | None = None,
        elements_layout: List[List[str]] | None = None,
        ac_counts: tuple[int | None, int | None] | None = None,
        nearest_range_nm: float | None = None,
        nearest_alt_ft: float | None = None,
        alt_filter: tuple[float | None, float | None] | None = None,
        alt_filter_autoscale: bool = False,
    ) -> None:
        # Refresh theme-driven colors each draw so live reload updates panel
        try:
            self.bg_color = ThemeManager.color("status.bg")
            self.text_color = ThemeManager.color("status.text")
        except Exception:
            pass
        # --- Build element arrays (no concatenation) ------------------
        units = settings.units
        if units == "mi_ft_mph":
            # assignment retained only if future logic needs converted value;
            # suppress unused expression
            _ = range_nm * 1.15078
        elif units == "km_m_kmh":
            _ = range_nm * 1.852
        else:
            pass

        def mark(ok: bool) -> str:
            return "ok" if ok else "x"

        # The overlay historically showed center Lat/Lon; replace that
        # with a human-friendly "time since last data update" so the
        # user can see how stale the displayed tracks are. Both the
        # LAT and LON element keys map to this single age display.
        # Element helpers -------------------------------------------------
        def _format_age(age_s: float | None) -> str:
            if age_s is None:
                return "Age ?"
            try:
                s = int(round(age_s))
            except Exception:
                return "Age ?"
            if s < 60:
                return f"Age {s}s"
            if s < 3600:
                m = s // 60
                r = s % 60
                return f"Age {m}m{r}s" if r else f"Age {m}m"
            h = s // 3600
            m = (s % 3600) // 60
            return f"Age {h}h{m}m" if m else f"Age {h}h"

        def _elem_gps(ok: bool) -> str:
            return f"GPS {'ok' if ok else 'x'}"

        def _elem_imu(ok: bool) -> str:
            return f"IMU {'ok' if ok else 'x'}"

        def _elem_dec(ok: bool) -> str:
            return f"DEC {'ok' if ok else 'x'}"

        def _elem_rng(range_nm: float, units: str) -> str:
            if units == "mi_ft_mph":
                rng = range_nm * 1.15078
                rng_units = "mi"
            elif units == "km_m_kmh":
                rng = range_nm * 1.852
                rng_units = "km"
            else:
                rng = range_nm
                rng_units = "nm"
            # Compact range display with colon and unit suffix, e.g. 'RNG:10nm'
            return f"RNG:{rng:.0f}{rng_units}"

        def _elem_clock(clock_utc: str) -> str:
            return clock_utc

        def _elem_freshness(
            last_update_ts: float | None,
        ) -> tuple[str, str, float | None]:
            # returns an AGE sentinel: ('AGE', label, age_s)
            if last_update_ts is None:
                return ("AGE", _format_age(None), None)
            try:
                import time

                now = time.time()
                age_s = max(0.0, now - float(last_update_ts))
                return ("AGE", _format_age(age_s), age_s)
            except Exception:
                return ("AGE", _format_age(None), None)

        def _elem_loc(lat: float | None, lon: float | None) -> str:
            """Format a compact location string for the center point.

            Returns a two-part lat/lon like 'N42.1234 W71.5678'. If either
            coordinate is None, returns '?' in place.
            """

            def fmt_lat(v: float | None) -> str:
                if v is None:
                    return "?"
                try:
                    v = float(v)
                except Exception:
                    return "?"
                hemi = "N" if v >= 0 else "S"
                return f"{hemi}{abs(v):.4f}"

            def fmt_lon(v: float | None) -> str:
                if v is None:
                    return "?"
                try:
                    v = float(v)
                except Exception:
                    return "?"
                hemi = "E" if v >= 0 else "W"
                return f"{hemi}{abs(v):.4f}"

            return f"{fmt_lat(lat)} {fmt_lon(lon)}"

        # --- Additional element helpers requested by UI config -------
        def _elem_near(
            range_nm_val: float | None, alt_ft: float | None, units_in: str = "nm_ft_kt"
        ) -> str:
            """Nearest target summary: "NEAR: 2.1nm / 3200ft".

            Accepts range in nautical miles and altitude in feet and
            converts range to configured units.
            """
            if range_nm_val is None:
                rng_label = "?"
            else:
                try:
                    if units_in == "mi_ft_mph":
                        rng = range_nm_val * 1.15078
                        ru = "mi"
                    elif units_in == "km_m_kmh":
                        rng = range_nm_val * 1.852
                        ru = "km"
                    else:
                        rng = range_nm_val
                        ru = "nm"
                    # use one decimal place for small ranges
                    rng_label = f"{rng:.1f}{ru}"
                except Exception:
                    rng_label = "?"
            # Short label 'NR' for nearest target
            return f"NR:{rng_label}/{_fmt_alt(alt_ft)}"

        def _elem_highest(alt_ft: float | None) -> str:
            """Highest target: small up-arrow + altitude (▲FL350)."""
            return f"▲{_fmt_alt(alt_ft)}"

        def _elem_lowest(alt_ft: float | None) -> str:
            """Lowest target: small down-arrow + altitude (▼1500ft)."""
            return f"▼{_fmt_alt(alt_ft)}"

        def _elem_fastest(spd_kt: float | None) -> str:
            """Fastest target speed summary (SPD 480kt)."""
            if spd_kt is None:
                return "SPD ?"
            try:
                s = int(round(float(spd_kt)))
                return f"SPD {s}kt"
            except Exception:
                return "SPD ?"

        def _elem_altfilter(
            alt_filter: tuple[float | None, float | None] | None
        ) -> str:
            return StatusOverlay.format_alt_filter(
                alt_filter, autoscale=alt_filter_autoscale
            )

        def _elem_ac_count(counts: tuple[int | None, int | None] | None) -> str:
            """Aircraft count summary (AC:total(visible))."""
            try:
                if not counts:
                    return "AC:?"
                total, visible = counts
                total_txt = "?" if total is None else str(int(total))
                visible_txt = "?" if visible is None else str(int(visible))
                # Show visible first, total in parentheses: 'AC:visible(total)'
                return f"AC:{visible_txt}({total_txt})"
            except Exception:
                return "AC:?"

        STATUS_OVERLAY_CONFIG.get("elements", {})

        def _outside_in_order(n: int) -> List[int]:
            # produces indices in order: 0, n-1, 1, n-2, 2, ...
            out: List[int] = []
            lo = 0
            hi = n - 1
            while lo <= hi:
                out.append(lo)
                lo += 1
                if lo <= hi:
                    out.append(hi)
                    hi -= 1
            return out

        # Build lines by placing provided element keys into outside-in
        # positions so outer elements occupy edges and additional
        # elements move inward.
        lines: List[List[object]] = []
        for keys in self._elements_layout:
            # keys is a list of element keys for this line
            if not isinstance(keys, (list, tuple)):
                # allow a single string to represent a single-cell line
                keys = [keys]
            # normalize element keys to uppercase strings; legacy LAT/LON
            # references removed — use LOC for lat/lon and AGE for freshness.
            norm: List[str] = [str(k).upper() for k in keys]

            n = max(1, len(norm))
            cells: List[object] = [""] * n
            _outside_in_order(n)

            # dispatch map: keys -> zero-arg callables that build cell
            dispatch: Dict[str, Callable[[], object]] = {
                "GPS": lambda: _elem_gps(gps_ok),
                "IMU": lambda: _elem_imu(imu_ok),
                "DEC": lambda: _elem_dec(decoder_ok),
                "RNG": lambda: _elem_rng(range_nm, units),
                "CLOCK": lambda: _elem_clock(clock_utc),
                "AGE": lambda: _elem_freshness(last_update_ts),
                "LOC": lambda: _elem_loc(center_lat, center_lon),
                "NEAR": lambda: _elem_near(
                    nearest_range_nm if nearest_range_nm is not None else None,
                    nearest_alt_ft,
                    units,
                ),
                "HIGHEST": lambda: _elem_highest(None),
                "LOWEST": lambda: _elem_lowest(None),
                "FASTEST": lambda: _elem_fastest(None),
                "ALTFILTER": lambda: _elem_altfilter(alt_filter),
                "AC": lambda: _elem_ac_count(ac_counts),
                "": lambda: "",
            }

            for src_idx, key in enumerate(norm):
                # Place elements in left-to-right order matching the provided list
                # (use src index). This aligns visual order with the config
                # element lists so ['RNG','NEAR','AC'] renders left->right.
                pos = src_idx
                key_u = str(key)
                maker = dispatch.get(key_u.upper())
                if maker is not None:
                    try:
                        cells[pos] = maker()
                    except Exception:
                        cells[pos] = str(key)
                else:
                    cells[pos] = str(key)
            lines.append(cells)
        # Demo mode no longer adds extra lines; status bar is fixed to one line.

        # --- Determine per-line cell counts & widths ------------------
        # Compute an automatic panel width based on measured text widths so
        # short info blocks produce narrower translucent backgrounds. For
        # each line we sum measured cell widths (text + 2*pad_x) and pick
        # the maximum across lines. Callers can still override with
        # self.width_px.
        measure = self._measure_fn
        line_widths: list[int] = []
        for cells in lines:
            total_w = 0
            for text in cells:
                # Cells may be plain strings or a tuple sentinel for special
                # rendering (e.g. ('_AGE', age_el)). Use the visible label
                # for measurement.
                if isinstance(text, tuple) and len(text) >= 2:
                    label = str(text[1])
                else:
                    label = str(text)
                try:
                    tw, _ = measure(label, self.font_px)
                except Exception:
                    tw = int(self.font_px * 0.6) * len(label)
                total_w += tw + 2 * self.pad_x
            # Ensure at least a tiny width to avoid zero / negative cases
            line_widths.append(max(1, int(total_w)))
        computed_width = max(line_widths) if line_widths else 1
        width = self.width_px or computed_width
        # TODO: better handle height of special characters (e.g. "-")
        line_height = self.font_px + 2 * self.pad_y
        # Include top/bottom padding in total panel height so the overlay
        #  visually separates from content above/below and scales with font.
        panel_h = self.pad_top + (line_height * len(lines)) + self.pad_bottom

        # --- Background fill ------------------------------------------
        for dy in range(panel_h):
            canvas.line((0, dy), (width - 1, dy), color=self.bg_color)

        # --- Draw each line's cells -----------------------------------
        y = self.pad_top
        for cells in lines:
            n = max(1, len(cells))
            cell_w = width // n
            for i, text in enumerate(cells):
                x0 = i * cell_w
                # If this is a special AGE cell, render a colored rounded
                # badge instead of plain text.
                # Freshness sentinel is ('AGE', label, age_s)
                if isinstance(text, tuple) and len(text) >= 2 and text[0] == "AGE":
                    label = str(text[1])
                    age_val = None
                    if len(text) >= 3:
                        try:
                            age_val = float(text[2]) if text[2] is not None else None
                        except Exception:
                            age_val = None
                    # Determine state from numeric age (if available)
                    if age_val is None:
                        state = "STALE"
                    else:
                        if age_val < 5.0:
                            state = "LIVE"
                        elif age_val < 10.0:
                            state = "DELAY"
                        else:
                            state = "STALE"

                    # Badge label and colors per rules
                    if state == "LIVE":
                        badge_text = "LIVE"
                        try:
                            bg = ThemeManager.color("status.badge.live.bg")
                            fg = ThemeManager.color("status.badge.live.text")
                        except Exception:
                            bg = (0, 160, 0, 255)
                            fg = (255, 255, 255, 255)
                    elif state == "DELAY":
                        badge_text = "DELAY"
                        try:
                            bg = ThemeManager.color("status.badge.delay.bg")
                            fg = ThemeManager.color("status.badge.delay.text")
                        except Exception:
                            bg = (255, 165, 0, 255)
                            fg = (0, 0, 0, 255)
                    else:
                        badge_text = "STALE"
                        try:
                            bg = ThemeManager.color("status.badge.stale.bg")
                            fg = ThemeManager.color("status.badge.stale.text")
                        except Exception:
                            bg = (200, 0, 0, 255)
                            fg = (255, 255, 255, 255)

                    # Measure badge text
                    try:
                        tw, th = measure(badge_text, self.font_px)
                    except Exception:
                        tw, th = (
                            int(self.font_px * 0.6) * len(badge_text),
                            self.font_px,
                        )

                    # Badge sizing
                    badge_pad_x = max(5, int(self.font_px * 0.4))
                    badge_h = max(self.font_px, th) + 3
                    badge_w = tw + 2 * badge_pad_x
                    badge_radius = badge_h // 2

                    # Center badge within its cell (not the whole panel)
                    cy = y + (line_height // 2)
                    inner_left = x0 + self.pad_x
                    inner_right = x0 + cell_w - self.pad_x
                    cell_avail_w = max(1, inner_right - inner_left)
                    # If badge wider than cell, clamp to left boundary (fallback)
                    if badge_w >= cell_avail_w:
                        cx = inner_left
                    else:
                        cx = inner_left + (cell_avail_w - badge_w) // 2

                    # Draw pill: two filled circles and a thick line between
                    left_center = (cx + badge_radius, cy)
                    right_center = (cx + badge_w - badge_radius, cy)
                    # central bar
                    canvas.line(
                        (left_center[0], cy),
                        (right_center[0], cy),
                        width=badge_h,
                        color=bg,
                    )
                    # end caps
                    canvas.filled_circle(left_center, badge_radius, bg)
                    canvas.filled_circle(right_center, badge_radius, bg)

                    # Draw text centered in badge (vertical center using text height)
                    text_tx = cx + max(0, (badge_w - tw) // 2)
                    text_ty = cy - (th // 2) - 3
                    canvas.text(
                        (text_tx, text_ty), badge_text, size_px=self.font_px, color=fg
                    )

                else:
                    # Plain text rendering
                    s = (
                        text[1]
                        if isinstance(text, tuple) and len(text) >= 2
                        else str(text)
                    )
                    if s == "":
                        # empty placeholder (used when AGE was already placed)
                        continue
                    try:
                        tw, th = measure(str(s), self.font_px)
                    except Exception:
                        tw, th = (int(self.font_px * 0.6) * len(str(s)), self.font_px)
                    inner_left = x0 + self.pad_x
                    inner_right = x0 + cell_w - self.pad_x
                    avail_w = max(1, inner_right - inner_left)
                    # Alignments:
                    # - first element: left-aligned with no extra padding
                    # - last element: right-aligned to inner_right
                    # - others: centered
                    if i == 0:
                        tx = x0
                    elif i == (n - 1):
                        # place text as far right as possible but don't overflow
                        tx = max(inner_left, inner_right - tw)
                    else:
                        tx = inner_left + max(0, (avail_w - tw) // 2)
                    ty = y + (line_height - th) // 2
                    canvas.text(
                        (tx, ty), str(text), size_px=self.font_px, color=self.text_color
                    )
            y += line_height

    # No border: overlay is a translucent band only

    # ------------------------------------------------------------------
    def _measure_text_internal(self, text: str, size_px: int) -> Tuple[int, int]:
        key = (text, size_px)
        cached = self._measure_cache.get(key)
        if cached is not None:
            return cached
        try:
            # Prefer Pillow measurement when available so measurements match
            # the Pillow-backed Canvas used by the ILI9341 backend.
            try:
                from PIL import ImageFont

                pil_font: Any
                try:
                    pil_font = ImageFont.truetype("DejaVuSansMono.ttf", size_px)
                except Exception:
                    try:
                        pil_font = ImageFont.load_default()
                    except Exception:
                        pil_font = None
                wh = (int(size_px * 0.6) * len(text), size_px)
                if pil_font is not None:
                    try:
                        m = pil_font.getmask(text)
                        wh = (m.size[0], m.size[1])
                    except Exception:
                        pass
            except Exception:
                # Fallback to pygame if Pillow not available / failed
                import pygame as _pg

                if not _pg.get_init():  # defensive init
                    _pg.init()
                if not _pg.font.get_init():
                    _pg.font.init()
                pg_font = _pg.font.Font(None, size_px)
                wh = pg_font.size(text)
        except Exception:
            wh = (int(size_px * 0.6) * len(text), size_px)
        self._measure_cache[key] = wh
        return wh
