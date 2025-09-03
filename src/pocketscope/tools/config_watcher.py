"""Async settings file watcher publishing on ``cfg.changed``."""

from __future__ import annotations

import asyncio
import os

from pocketscope.core.events import EventBus, pack
from pocketscope.settings.store import SettingsStore


class ConfigWatcher:
    """Poll the settings file and publish updates when it changes."""

    def __init__(self, bus: EventBus, poll_hz: float = 2.0) -> None:
        self._bus = bus
        self._poll = max(0.1, float(poll_hz))
        self._path = SettingsStore.settings_path()
        self._task: asyncio.Task[None] | None = None
        self._last_mtime: float = 0.0
        self._running = False

    async def run(self) -> None:
        """Start the watcher loop until :meth:`stop` is called."""
        if self._running:
            return
        self._running = True
        try:
            while self._running:
                await asyncio.sleep(1.0 / self._poll)
                try:
                    mtime = os.path.getmtime(self._path)
                except OSError:
                    mtime = 0.0
                if mtime and mtime != self._last_mtime:
                    self._last_mtime = mtime
                    settings = SettingsStore.load()
                    await self._bus.publish("cfg.changed", pack(settings.model_dump()))
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancel
            pass
        finally:
            self._running = False

    async def stop(self) -> None:
        """Request the watcher loop to exit."""
        self._running = False
