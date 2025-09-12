"""Settings persistence helpers."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import ClassVar

from .schema import Settings


class SettingsStore:
    """Load and save :class:`Settings` to disk."""

    _debounce: ClassVar[asyncio.TimerHandle | None] = None

    @staticmethod
    def settings_path() -> Path:
        """Return the path to the settings JSON file."""
        home = os.environ.get("POCKETSCOPE_HOME")
        if home:
            base = Path(home).expanduser()
        else:
            base = Path(os.path.expanduser("~/.pocketscope"))
        return base / "settings.json"

    @classmethod
    def ensure_home(cls) -> Path:
        """Ensure the settings directory exists and return it."""
        path = cls.settings_path().parent
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def load(cls) -> Settings:
        """Load settings from disk, returning defaults on error."""
        path = cls.settings_path()
        cls.ensure_home()
        try:
            data = json.loads(path.read_text())
            return Settings.model_validate(data)
        except Exception:
            return Settings()

    @classmethod
    def save(cls, settings: Settings) -> None:
        """Atomically persist *settings* to disk."""
        path = cls.settings_path()
        cls.ensure_home()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(settings.model_dump_json(indent=2))
        os.replace(tmp, path)

    @classmethod
    def save_debounced(cls, settings: Settings, delay_s: float = 0.3) -> None:
        """Debounce successive saves with *delay_s* seconds."""
        loop = asyncio.get_event_loop()
        if cls._debounce is not None:
            cls._debounce.cancel()

        def _cb() -> None:
            cls.save(settings)

        cls._debounce = loop.call_later(delay_s, _cb)
