"""Settings screen overlay.

Overlay menu for persistent configuration; triggered by ⚙ soft key.

This module implements a very small immediate-mode menu rendered over
the PPI view. It does not allocate per-frame aside from a couple of
short lived tuples. All state (current selection + live Settings model)
is stored on the ``SettingsScreen`` instance. The controller owns one
instance and toggles its visibility.

Key bindings
------------
Up / Down        : move highlighted menu row
Enter / Right    : activate (cycle / toggle) the current row
S                : show/hide the screen (same as soft key)
ESC / Q          : exit the screen (return to scope) without quitting

Persistence rules
-----------------
Each activation mutates the underlying ``UiController`` runtime state
and in‑memory ``Settings`` model but does NOT write to disk until the
user presses the Save softkey. This allows the user to make multiple
changes and either Save (persist + close) or Back (close without an
immediate write). External file modifications while the screen is
visible should still call ``refresh_from_controller`` so displayed
values stay in sync. Any controller operations performed while the
settings screen is visible are treated as "staged" and persistence is
suppressed until Save.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - for type checking only
    from pocketscope.ui.controllers import UiController

from pocketscope.render.canvas import Canvas, Color
from pocketscope.settings.schema import Settings
from pocketscope.settings.values import (
    ALTITUDE_FILTER_CYCLE_ORDER,
    RANGE_LADDER_NM,
    SETTINGS_SCREEN_CONFIG,
    THEME,
    TRACK_LENGTH_CYCLE_ORDER,
    TRACK_LENGTH_MODES,
    UNITS_ORDER,
)

# Explicit choices for typography controls (kept small and sensible for Pi)
LABEL_FONT_SIZES = (8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 48)
# Line gap choices in pixels (relative offset between data-block lines)
LABEL_LINE_GAP_VALUES = tuple(range(-6, 7))  # -6..6
# Status overlay font sizes (small set)
STATUS_FONT_SIZES = (8, 10, 12, 14, 16)
# Status pad choices (in pixels). Include None option to use automatic scaling.
STATUS_PAD_CHOICES = (None, 0, 2, 4, 6, 8)

# Resolve themed colors with sensible fallbacks
_SC_THEME = (
    THEME.get("colors", {}).get("settings_screen", {})
    if isinstance(THEME, dict)
    else {}
)


def _c(v: object, fb: tuple[int, int, int, int]) -> Color:
    if (
        isinstance(v, (list, tuple))
        and len(v) == 4
        and all(isinstance(c, (int, float)) for c in v)
    ):
        return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))
    return fb


_COLOR_BG: Color = _c(_SC_THEME.get("bg"), (0, 0, 0, 255))
_COLOR_HILITE: Color = _c(_SC_THEME.get("hilite"), (0, 120, 0, 255))
_COLOR_TEXT: Color = _c(_SC_THEME.get("text"), (255, 255, 255, 255))
_COLOR_TITLE_BG: Color = _c(_SC_THEME.get("title_bg"), (24, 24, 24, 255))
_COLOR_TITLE_FG: Color = _c(_SC_THEME.get("title_fg"), (255, 255, 255, 255))


@dataclass(slots=True)
class MenuItem:
    label: str
    kind: str  # cycle | toggle | back
    values: Sequence[str] | None = None


class SettingsScreen:
    """Interactive full screen settings overlay.

    The screen is inert unless ``visible`` is True. Rendering is done via
    :meth:`draw` which fills the display with a dark background and draws
    a simple list UI sized for a ~240x320 portrait target but will scale
    to any provided display size.
    """

    def __init__(
        self, settings: Settings, *, font_px: int = 12, pad_px: int = 6
    ) -> None:
        self.visible: bool = False
        self._settings = settings
        base_px = SETTINGS_SCREEN_CONFIG.get("base_font_px")
        if isinstance(base_px, (int, float)):
            font_px = int(base_px)
        self.font_px = int(font_px)
        # Horizontal padding used for left label inset and right value inset
        self.pad_px = max(0, int(pad_px))
        self._items: List[MenuItem] = [
            MenuItem("Units", "cycle", tuple(UNITS_ORDER)),
            MenuItem(
                "Range Default",
                "cycle",
                tuple(str(int(r)) for r in RANGE_LADDER_NM),
            ),
            MenuItem("Track Length", "cycle", tuple(TRACK_LENGTH_CYCLE_ORDER)),
            MenuItem(
                "Altitude Filter",
                "cycle",
                tuple(ALTITUDE_FILTER_CYCLE_ORDER),
            ),
            MenuItem("Demo Mode", "toggle"),
            MenuItem("North-up Lock", "toggle"),
            # Typography controls (appended so legacy menu indices remain stable)
            MenuItem(
                "Label Font", "cycle", tuple(str(int(x)) for x in LABEL_FONT_SIZES)
            ),
            MenuItem(
                "Label Line Gap",
                "cycle",
                tuple(str(int(x)) for x in LABEL_LINE_GAP_VALUES),
            ),
            MenuItem(
                "Status Font",
                "cycle",
                tuple(str(int(x)) for x in STATUS_FONT_SIZES),
            ),
            MenuItem(
                "Status Pad Top",
                "cycle",
                tuple(str(x) if x is not None else "auto" for x in STATUS_PAD_CHOICES),
            ),
            MenuItem(
                "Status Pad Bottom",
                "cycle",
                tuple(str(x) if x is not None else "auto" for x in STATUS_PAD_CHOICES),
            ),
            # Softkeys controls
            MenuItem(
                "Softkeys Font",
                "cycle",
                tuple(str(int(x)) for x in STATUS_FONT_SIZES),
            ),
            MenuItem(
                "Softkeys Pad X",
                "cycle",
                tuple(str(int(x)) for x in (0, 2, 4, 6, 8)),
            ),
            MenuItem(
                "Softkeys Pad Y",
                "cycle",
                tuple(str(int(x)) for x in (0, 2, 4, 6, 8)),
            ),
        ]
        self._sel: int = 0

    # North-up state now lives on controller (persisted); local copy removed

    # --- External API -------------------------------------------------
    def refresh_from_controller(self, settings: Settings) -> None:
        """Update internal copy after external file change."""
        self._settings = settings

    def set_font_px(self, size: int) -> None:
        """Update menu font size (applies immediately)."""
        self.font_px = max(6, int(size))

    # --- Input handling -----------------------------------------------
    def on_key(self, key: str, controller: "UiController") -> None:
        key_l = key.lower()
        if key_l in {"s"}:
            self.visible = not self.visible
            return
        if not self.visible:
            return
        if key_l in {"escape", "esc", "q"}:
            self.visible = False
            return
        if key_l in {"up"}:
            self._sel = (self._sel - 1) % len(self._items)
            return
        if key_l in {"down"}:
            self._sel = (self._sel + 1) % len(self._items)
            return
        if key_l in {"return", "enter", "right"}:
            self._activate(controller)

    # --- Mouse handling -----------------------------------------------
    def on_mouse(
        self,
        x: int,
        y: int,
        size: Tuple[int, int],
        controller: "UiController",
    ) -> bool:
        """Handle a mouse press.

        Returns True if the event was consumed. A single click on a row both
        selects and immediately activates it (cycles / toggles). This keeps
        the UX simple for touch / pointer users (no need for a separate
        confirmation like the Enter key path).

        Implementation mirrors the geometry math in :meth:`draw` so we do not
        need to store per-frame rectangles. This function is cheap (a handful
        of integer ops) and only called on clicks.
        """
        if not self.visible:
            return False
        w, h = size
        # Reconstruct layout metrics (must match draw())
        title_h = int(self.font_px + 8)
        row_h = int(self.font_px + 6)
        start_y = title_h + 2
        if y < start_y or y >= h:
            return False
        # Compute index and validate
        idx = (y - start_y) // row_h
        if idx < 0 or idx >= len(self._items):
            return False
        self._sel = int(idx)
        self._activate(controller)
        return True

    def _activate(self, controller: "UiController") -> None:
        item = self._items[self._sel]
        if item.kind == "back":
            self.visible = False
            return
        if item.label == "Units":
            controller.cycle_units(persist=False)
            self._settings.units = controller.units
        elif item.label == "Range Default":
            ladder = list(RANGE_LADDER_NM)
            cur_range = float(self._settings.range_nm)
            try:
                idx = ladder.index(cur_range)
            except ValueError:
                # Fallback to first if out of ladder
                idx = 0
            cur_range = ladder[(idx + 1) % len(ladder)]
            self._settings.range_nm = cur_range
            controller._cfg.range_nm = cur_range
        elif item.label == "Track Length":
            controller.cycle_track_length(persist=False)
            self._settings.track_length_mode = controller.track_length_mode
        elif item.label == "Label Font":
            # Cycle through explicit font-size choices
            try:
                cur_label_font = int(
                    getattr(self._settings, "label_font_px", self.font_px)
                )
            except Exception:
                cur_label_font = int(self.font_px)
            # Find current index in canonical list or fallback to nearest
            sizes = list(LABEL_FONT_SIZES)
            # Find current index robustly without relying on .index to avoid
            # narrow Literal typing on the tuple which causes mypy to complain.
            idx = next((i for i, s in enumerate(sizes) if s == cur_label_font), -1)
            if idx == -1:
                # choose closest larger or fallback to first
                idx = 0
                for i, s in enumerate(sizes):
                    if s >= cur_label_font:
                        idx = i
                        break
            idx = (idx + 1) % len(sizes)
            new = int(sizes[idx])
            self._settings.label_font_px = new
            # Apply immediately to controller/view
            try:
                controller._view.label_font_px = int(new)
            except Exception:
                pass
        elif item.label == "Label Line Gap":
            try:
                cur_label_line_gap = int(
                    getattr(self._settings, "label_line_gap_px", 0)
                )
            except Exception:
                cur_label_line_gap = 0
            gaps = list(LABEL_LINE_GAP_VALUES)
            # Use enumerate-based search to avoid Literal typing issues
            idx = next((i for i, g in enumerate(gaps) if g == cur_label_line_gap), -1)
            if idx == -1:
                idx = 0
            idx = (idx + 1) % len(gaps)
            new = int(gaps[idx])
            self._settings.label_line_gap_px = new
            try:
                controller._view.label_line_gap_px = int(new)
            except Exception:
                pass
        elif item.label == "Status Font":
            try:
                cur_status_font = int(
                    getattr(self._settings, "status_font_px", self.font_px)
                )
            except Exception:
                cur_status_font = int(self.font_px)
            sizes = list(STATUS_FONT_SIZES)
            idx = next((i for i, s in enumerate(sizes) if s == cur_status_font), -1)
            if idx == -1:
                idx = 0
                for i, s in enumerate(sizes):
                    if s >= cur_status_font:
                        idx = i
                        break
            idx = (idx + 1) % len(sizes)
            new = int(sizes[idx])
            self._settings.status_font_px = new
            try:
                controller._overlay.font_px = int(new)
            except Exception:
                pass
        elif item.label == "Status Pad Top":
            try:
                cur_status_pad_top = getattr(self._settings, "status_pad_top_px", None)
            except Exception:
                cur_status_pad_top = None
            choices = list(STATUS_PAD_CHOICES)
            idx = next(
                (i for i, s in enumerate(choices) if s == cur_status_pad_top), -1
            )
            if idx == -1:
                idx = 0
            idx = (idx + 1) % len(choices)
            new_status_pad_top: int | None = choices[idx]
            self._settings.status_pad_top_px = new_status_pad_top
            try:
                if new_status_pad_top is not None:
                    controller._overlay.pad_top = int(new_status_pad_top)
            except Exception:
                pass
        elif item.label == "Status Pad Bottom":
            try:
                cur_status_pad_bottom = getattr(
                    self._settings, "status_pad_bottom_px", None
                )
            except Exception:
                cur_status_pad_bottom = None
            choices = list(STATUS_PAD_CHOICES)
            idx = next(
                (i for i, s in enumerate(choices) if s == cur_status_pad_bottom), -1
            )
            if idx == -1:
                idx = 0
            idx = (idx + 1) % len(choices)
            new_status_pad_bottom: int | None = choices[idx]
            self._settings.status_pad_bottom_px = new_status_pad_bottom
            try:
                if new_status_pad_bottom is not None:
                    controller._overlay.pad_bottom = int(new_status_pad_bottom)
            except Exception:
                pass
        elif item.label == "Softkeys Font":
            try:
                cur_softkeys_font = int(
                    getattr(self._settings, "softkeys_font_px", self.font_px)
                )
            except Exception:
                cur_softkeys_font = int(self.font_px)
            sizes = list(STATUS_FONT_SIZES)
            idx = next((i for i, s in enumerate(sizes) if s == cur_softkeys_font), -1)
            if idx == -1:
                idx = 0
                for i, s in enumerate(sizes):
                    if s >= cur_softkeys_font:
                        idx = i
                        break
            idx = (idx + 1) % len(sizes)
            new = int(sizes[idx])
            self._settings.softkeys_font_px = new
            try:
                if controller._softkeys:
                    controller._softkeys._requested_font_px = int(new)
                    controller._softkeys.layout()
            except Exception:
                pass
        elif item.label == "Softkeys Pad X":
            try:
                cur_softkeys_pad_x = int(getattr(self._settings, "softkeys_pad_x", 4))
            except Exception:
                cur_softkeys_pad_x = 4
            choices_pad = [0, 2, 4, 6, 8]
            idx = next(
                (i for i, s in enumerate(choices_pad) if s == cur_softkeys_pad_x), -1
            )
            if idx == -1:
                idx = 0
            idx = (idx + 1) % len(choices_pad)
            new_softkeys_pad_x: int = choices_pad[idx]
            self._settings.softkeys_pad_x = new_softkeys_pad_x
            try:
                if controller._softkeys:
                    controller._softkeys.pad_x = int(new_softkeys_pad_x)
                    controller._softkeys.layout()
            except Exception:
                pass
        elif item.label == "Softkeys Pad Y":
            try:
                cur_softkeys_pad_y = int(getattr(self._settings, "softkeys_pad_y", 2))
            except Exception:
                cur_softkeys_pad_y = 2
            choices_pad = [0, 2, 4, 6, 8]
            idx = next(
                (i for i, s in enumerate(choices_pad) if s == cur_softkeys_pad_y), -1
            )
            if idx == -1:
                idx = 0
            idx = (idx + 1) % len(choices_pad)
            new_softkeys_pad_y: int = choices_pad[idx]
            self._settings.softkeys_pad_y = new_softkeys_pad_y
            try:
                if controller._softkeys:
                    controller._softkeys.pad_y = int(new_softkeys_pad_y)
                    controller._softkeys.layout()
            except Exception:
                pass
        elif item.label == "Altitude Filter":
            controller.cycle_altitude_filter(persist=False)
            # Mirror into settings model
            self._settings.altitude_filter = controller.altitude_filter
        elif item.label == "Demo Mode":
            controller.toggle_demo(persist=False)
            self._settings.demo_mode = controller.demo_mode
        elif item.label == "North-up Lock":
            controller.toggle_north_up_lock(persist=False)
            self._settings.north_up_lock = controller.north_up_lock
        # Persistence deferred until explicit Save

    # --- Rendering ----------------------------------------------------
    def draw(
        self, canvas: Canvas, size: Tuple[int, int], controller: "UiController"
    ) -> None:
        if not self.visible:
            return
        w, h = size
        # Background fill
        for y in range(h):
            canvas.line((0, y), (w - 1, y), color=_COLOR_BG)
        # Title bar
        title_h = int(self.font_px + 8)
        for y in range(title_h):
            canvas.line((0, y), (w - 1, y), color=_COLOR_TITLE_BG)
        title = "SETTINGS"
        tw = int(len(title) * self.font_px * 0.6)
        tx = max(0, (w - tw) // 2)
        canvas.text(
            (tx, max(0, (title_h - self.font_px) // 2)),
            title,
            size_px=self.font_px,
            color=_COLOR_TITLE_FG,
        )
        # Menu
        row_h = int(self.font_px + 6)
        start_y = title_h + 2
        for i, item in enumerate(self._items):
            y0 = start_y + i * row_h
            if y0 + row_h >= h:
                break
            if i == self._sel:
                for yy in range(row_h):
                    canvas.line((0, y0 + yy), (w - 1, y0 + yy), color=_COLOR_HILITE)
            label = item.label
            val = self._current_value(item, controller)
            canvas.text(
                (self.pad_px, y0 + 3), label, size_px=self.font_px, color=_COLOR_TEXT
            )
            if val:
                # Prefer precise measurement when backend supports text_size
                try:
                    vw, _vh = canvas.text_size(  # type: ignore[attr-defined]
                        val, size_px=self.font_px
                    )
                except Exception:
                    vw = int(len(val) * self.font_px * 0.6)
                vx = max(0, w - vw - self.pad_px)
                canvas.text((vx, y0 + 3), val, size_px=self.font_px, color=_COLOR_TEXT)

    # Softkey integration helpers ------------------------------------
    def softkey_actions(
        self, controller: "UiController"
    ) -> dict[str, Callable[[], None]]:
        """Return softkey actions when menu visible.

        Provides Back (close menu) and Save (force flush settings now).
        Save is mostly redundant (changes auto-debounce) but offers an
        explicit user affordance.
        """

        return {
            "Back": lambda: self._back(),
            "Save": lambda: (self._save_and_close()),
        }

    def _back(self) -> None:
        self.visible = False

    def _save_and_close(self) -> None:
        # Explicit immediate save + dismiss screen
        from pocketscope.settings.store import SettingsStore as _SS  # local import

        _SS.save(self._settings)
        self.visible = False

    def _current_value(self, item: MenuItem, controller: "UiController") -> str | None:
        if item.label == "Units":
            return controller.units
        if item.label == "Range Default":
            return f"{int(self._settings.range_nm):d}nm"
        if item.label == "Track Length":
            # Derive human-readable seconds from central config
            secs = TRACK_LENGTH_MODES.get(controller.track_length_mode)
            if isinstance(secs, (int, float)):
                return f"{int(secs)}s"
            return "?"
        if item.label == "Altitude Filter":
            return getattr(controller, "altitude_filter", "All")
        if item.label == "Demo Mode":
            return "ON" if controller.demo_mode else "OFF"
        if item.label == "North-up Lock":
            return "ON" if getattr(controller, "north_up_lock", True) else "OFF"
        if item.label == "Label Font":
            return f"{int(getattr(self._settings, 'label_font_px', self.font_px))}px"
        if item.label == "Label Line Gap":
            return f"{int(getattr(self._settings, 'label_line_gap_px', 0))}px"
        if item.label == "Softkeys Font":
            return f"{int(getattr(self._settings, 'softkeys_font_px', self.font_px))}px"
        if item.label == "Softkeys Pad X":
            return f"{int(getattr(self._settings, 'softkeys_pad_x', 4))}px"
        if item.label == "Softkeys Pad Y":
            return f"{int(getattr(self._settings, 'softkeys_pad_y', 2))}px"
        return None
