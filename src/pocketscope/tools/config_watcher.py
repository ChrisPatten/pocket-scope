"""Settings file watcher.

Combines event-driven reloads (listening on ``config.reload``) with a lightweight
polling loop (default 0.3s) so that external writes to the settings file are
noticed automatically during tests or simple CLI usage where no explicit reload
event is published.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from pocketscope.core.events import EventBus, pack
from pocketscope.settings.store import SettingsStore

# Lazy import inside method for ThemeManager to avoid unnecessary cost at import time.

logger = logging.getLogger(__name__)


class ConfigWatcher:
    """Watches the settings file and publishes ``cfg.changed`` events.

    Backwards compatibility: older callers may pass ``poll_hz``. Prefer
    ``poll_interval_s`` (seconds). If ``poll_hz`` is provided it will be
    converted to an interval (1.0 / poll_hz).
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        poll_interval_s: float = 0.3,
        poll_hz: float | None = None,
    ) -> None:
        self._bus = bus
        self._config_path = SettingsStore.settings_path()
        # User-level themes file (only this one; packaged + module-local are static)
        home_dir = self._config_path.parent
        self._theme_path = home_dir / "themes.yml"
        self._last_cfg_mtime: float | None = None
        self._last_theme_mtime: float | None = None
        self._last_config: dict[str, Any] = {}
        self._run_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None

        # Support legacy callers that pass poll_hz (frequency in Hz).
        if poll_hz is not None:
            try:
                hz = float(poll_hz)
                # Guard against zero or negative hz values
                if hz <= 0:
                    raise ValueError("poll_hz must be > 0")
                self._poll_interval_s = 1.0 / hz
            except Exception:
                # Fall back to provided poll_interval_s on any conversion error
                self._poll_interval_s = float(poll_interval_s)
        else:
            self._poll_interval_s = float(poll_interval_s)

    async def run(self) -> None:
        if self._run_task:
            return

        async def _runner() -> None:
            logger.info("Config watcher started path=%s", self._config_path)
            self._check_and_publish()
            sub = self._bus.subscribe("config.reload")
            try:
                async for _ in sub:
                    self._check_and_publish()
            except asyncio.CancelledError:  # pragma: no cover
                pass

        async def _poller() -> None:
            try:
                while True:
                    await asyncio.sleep(self._poll_interval_s)
                    self._check_and_publish()
            except asyncio.CancelledError:  # pragma: no cover
                pass

        self._run_task = asyncio.create_task(_runner(), name="config_watcher")
        if self._poll_interval_s > 0:
            self._poll_task = asyncio.create_task(
                _poller(), name="config_watcher_poll"
            )

    async def stop(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
            tasks.append(self._run_task)
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            tasks.append(self._poll_task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._run_task = None
        self._poll_task = None
        logger.info("Config watcher stopped")

    # Internals -----------------------------------------------------------------
    def _check_and_publish(self) -> None:
        cfg_changed = False
        theme_changed = False

        try:
            cfg_mtime = os.path.getmtime(self._config_path)
        except OSError:
            cfg_mtime = 0.0
        if cfg_mtime and cfg_mtime != self._last_cfg_mtime:
            self._last_cfg_mtime = cfg_mtime
            cfg_changed = True

        try:
            theme_mtime = os.path.getmtime(self._theme_path)
        except OSError:
            theme_mtime = 0.0
        if theme_mtime and theme_mtime != self._last_theme_mtime:
            self._last_theme_mtime = theme_mtime
            theme_changed = True

        if not (cfg_changed or theme_changed):
            return

        # Always attempt to reload settings (provides theme overrides) when
        # either file changes for simplicity / correctness.
        try:
            settings = SettingsStore.load()
        except Exception as e:  # pragma: no cover - defensive
            logger.error("Settings reload failed: %s", e)
            return

        # Publish cfg.changed if settings file modified.
        if cfg_changed:
            try:
                asyncio.create_task(
                    self._bus.publish("cfg.changed", pack(settings.model_dump()))
                )
            except Exception as e:  # pragma: no cover
                logger.error("cfg.changed publish failed: %s", e)

        # Theme reload & publish theme.changed if theme file modified OR if
        # settings changed (since settings carry theme overrides & could alter
        # effective palette). We treat both cases the same for simplicity.
        if theme_changed or cfg_changed:
            try:
                from pocketscope.theme import ThemeManager

                # settings may be pydantic model with model_dump
                payload_settings: dict[str, Any]
                if hasattr(settings, "model_dump"):
                    payload_settings = settings.model_dump()
                elif isinstance(settings, dict):
                    payload_settings = settings
                else:  # fallback
                    payload_settings = {}
                ThemeManager.reload(payload_settings)
                theme_obj = ThemeManager.theme()
                # Minimal palette export (hex strings as originally defined)
                pal_export = {
                    k: f"#{v.r:02X}{v.g:02X}{v.b:02X}{v.a:02X}" for k, v in theme_obj.palette.items()
                }
                asyncio.create_task(
                    self._bus.publish(
                        "theme.changed",
                        pack(
                            {
                                "theme": theme_obj.name,
                                "palette": pal_export,
                            }
                        ),
                    )
                )
            except Exception as e:  # pragma: no cover - defensive
                logger.error("Theme reload failed: %s", e)
