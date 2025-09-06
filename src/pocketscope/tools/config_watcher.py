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

logger = logging.getLogger(__name__)


class ConfigWatcher:
    """Watches the settings file and publishes ``cfg.changed`` events."""

    def __init__(self, bus: EventBus, *, poll_interval_s: float = 0.3) -> None:
        self._bus = bus
        self._config_path = SettingsStore.settings_path()
        self._last_mtime: float | None = None
        self._last_config: dict[str, Any] = {}
        self._run_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
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
        try:
            mtime = os.path.getmtime(self._config_path)
        except OSError:
            mtime = 0.0
        if mtime and mtime != self._last_mtime:
            self._last_mtime = mtime
            try:
                settings = SettingsStore.load()
                asyncio.create_task(
                    self._bus.publish("cfg.changed", pack(settings.model_dump()))
                )
            except Exception as e:  # pragma: no cover - defensive
                logger.error("Settings reload failed: %s", e)
