"""Runtime configuration helpers.

Small aggregator that centralizes defaults from settings.values and the
persisted Settings store, and provides a factory to build the UiConfig used
by callers like the examples. This is intentionally minimal: it returns a
UiConfig merged from CLI args (when provided) and persisted settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .settings.store import SettingsStore
from .settings.values import PPI_CONFIG, SETTINGS_SCREEN_CONFIG, THEME, ZOOM_LIMITS


@dataclass(slots=True)
class UIData:
    range_nm: float = 10.0
    min_range_nm: float = float(ZOOM_LIMITS.get("min_range_nm", 2.0))
    max_range_nm: float = float(ZOOM_LIMITS.get("max_range_nm", 80.0))
    target_fps: float = 30.0
    overlay: bool = True


@dataclass(slots=True)
class RuntimeConfig:
    ui: UIData
    theme: dict[str, Any]
    ppi_config: dict[str, Any]
    settings_screen_cfg: dict[str, Any]


def make_ui_config(*, args: Optional[object] = None) -> RuntimeConfig:
    """Build a RuntimeConfig merging values defaults, persisted settings,
    and optional CLI overrides passed in *args* (argparse.Namespace-like).

    Rules:
    - Persisted Settings (SettingsStore.load()) provide user defaults for
      runtime settings like range_nm; CLI args (when provided) override for
      the current session.
    - Only a small set of commonly overridden fields are merged here: range,
      target_fps, overlay, and font_px (the latter returned indirectly).
    """
    # Baseline defaults from values
    ui_cfg = UIData()
    ui_cfg.min_range_nm = float(ZOOM_LIMITS.get("min_range_nm", ui_cfg.min_range_nm))
    ui_cfg.max_range_nm = float(ZOOM_LIMITS.get("max_range_nm", ui_cfg.max_range_nm))

    # Load persisted settings (may be defaults)
    settings = SettingsStore.load()
    try:
        ui_cfg.range_nm = float(settings.range_nm)
    except Exception:
        pass

    # Apply CLI overrides when provided (Namespace-like)
    if args is not None:
        try:
            a_range = getattr(args, "range", None)
            if a_range is not None:
                ui_cfg.range_nm = float(a_range)
        except Exception:
            pass
        try:
            a_fps = getattr(args, "fps", None)
            if a_fps is not None:
                ui_cfg.target_fps = float(a_fps)
        except Exception:
            pass
        try:
            a_overlay = getattr(args, "overlay", None)
            if a_overlay is not None:
                ui_cfg.overlay = bool(a_overlay)
        except Exception:
            pass

    return RuntimeConfig(
        ui=ui_cfg,
        theme=dict(THEME) if isinstance(THEME, dict) else {},
        ppi_config=dict(PPI_CONFIG) if isinstance(PPI_CONFIG, dict) else {},
        settings_screen_cfg=dict(SETTINGS_SCREEN_CONFIG)
        if isinstance(SETTINGS_SCREEN_CONFIG, dict)
        else {},
    )


# Runtime singleton + listener API -------------------------------------
_RUNTIME: RuntimeConfig | None = None
_LISTENERS: list[Callable[[RuntimeConfig], None]] = []


def get_runtime() -> RuntimeConfig:
    """Return the current runtime config, creating a default if needed."""
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = make_ui_config()
    return _RUNTIME


def register_listener(cb: Callable[[RuntimeConfig], None]) -> None:
    """Register a callback to be invoked when runtime config updates.

    Callback receives the RuntimeConfig as the only argument.
    """
    if cb not in _LISTENERS:
        _LISTENERS.append(cb)


def unregister_listener(cb: Callable[[RuntimeConfig], None]) -> None:
    if cb in _LISTENERS:
        _LISTENERS.remove(cb)


def update_from_settings(settings: object) -> None:
    """Apply persisted Settings to the runtime config and notify listeners.

    This intentionally only updates the small runtime subset that might be
    derived from persisted settings (range_nm here). Callers may expand the
    merge logic later.
    """
    global _RUNTIME
    rc = get_runtime()
    try:
        # Update range if present on settings
        if hasattr(settings, "range_nm"):
            rc.ui.range_nm = float(getattr(settings, "range_nm"))
    except Exception:
        pass
    _RUNTIME = rc
    # Notify listeners (best-effort). Defer callbacks to the event loop to
    # avoid synchronous side-effects while callers (like UiController) are
    # still updating their local state.
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except Exception:
        loop = None

    if loop is not None:
        for cb in list(_LISTENERS):
            try:
                loop.call_soon(cb, rc)
            except Exception:
                pass
    else:
        for cb in list(_LISTENERS):
            try:
                cb(rc)
            except Exception:
                pass
