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

_COLOR_BG: Color = (0, 0, 0, 255)
_COLOR_HILITE: Color = (0, 120, 0, 255)
_COLOR_TEXT: Color = (255, 255, 255, 255)
_COLOR_TITLE_BG: Color = (24, 24, 24, 255)
_COLOR_TITLE_FG: Color = (255, 255, 255, 255)


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
        self.font_px = int(font_px)
        # Horizontal padding used for left label inset and right value inset
        self.pad_px = max(0, int(pad_px))
        self._items: List[MenuItem] = [
            MenuItem("Units", "cycle", ("nm_ft_kt", "mi_ft_mph", "km_m_kmh")),
            MenuItem("Range Default", "cycle", ("2", "5", "10", "20", "40", "80")),
            MenuItem("Track Length", "cycle", ("short", "medium", "long")),
            MenuItem(
                "Altitude Filter",  # placeholder future feature
                "cycle",
                ("All", "0–5k", "5–10k", "10–20k", ">20k"),
            ),
            MenuItem("Demo Mode", "toggle"),
            MenuItem("North-up Lock", "toggle"),  # rotation feature TBD
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
            ladder = [2.0, 5.0, 10.0, 20.0, 40.0, 80.0]
            cur = float(self._settings.range_nm)
            idx = ladder.index(cur) if cur in ladder else 2
            cur = ladder[(idx + 1) % len(ladder)]
            self._settings.range_nm = cur
            controller._cfg.range_nm = cur  # sync immediate visual range
        elif item.label == "Track Length":
            controller.cycle_track_length(persist=False)
            self._settings.track_length_mode = controller.track_length_mode
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
            mapping = {"short": "15s", "medium": "45s", "long": "120s"}
            return mapping.get(controller.track_length_mode, "?")
        if item.label == "Altitude Filter":
            return getattr(controller, "altitude_filter", "All")
        if item.label == "Demo Mode":
            return "ON" if controller.demo_mode else "OFF"
        if item.label == "North-up Lock":
            return "ON" if getattr(controller, "north_up_lock", True) else "OFF"
        return None
