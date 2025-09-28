"""Settings persistence helpers."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import ClassVar

from pydantic import ValidationError

from .schema import Settings


class SettingsLoadError(Exception):
    """Raised when settings.json exists but cannot be parsed or validated.

    This differentiates between a missing settings file (which is treated as
    "use defaults") and a present-but-bad file, which should surface as an
    actionable error to the caller / end user.
    """


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
        """Load settings from disk.

        Behavior:
        - If the settings file does not yet exist, return a Settings instance
          populated with defaults (this preserves first‑run UX).
        - If the file exists but cannot be read, parsed as JSON, or validated
          against the Settings schema, raise SettingsLoadError with context.
        """
        path = cls.settings_path()
        cls.ensure_home()
        if not path.exists():  # First run: no file yet -> defaults
            return Settings()
        try:
            raw = path.read_text()
        except Exception as e:  # IO error
            raise SettingsLoadError(f"Failed to read settings file: {path}: {e}") from e
        try:
            data = json.loads(raw)
        except Exception as e:  # JSON parse error
            raise SettingsLoadError(f"Invalid JSON in settings file {path}: {e}") from e
        try:
            return Settings.model_validate(data)
        except ValidationError as e:  # Schema validation error
            raise SettingsLoadError(
                f"Settings validation failed for {path}: {e}"
            ) from e
        except Exception as e:  # Any other unexpected error
            raise SettingsLoadError(
                f"Unexpected error validating settings file {path}: {e}"
            ) from e

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
