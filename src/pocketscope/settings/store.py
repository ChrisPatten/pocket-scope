"""Settings persistence helpers."""

from __future__ import annotations

import asyncio
import os
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path
from typing import Any, ClassVar, Mapping, cast

import yaml
from pydantic import ValidationError

from .schema import Settings


class SettingsLoadError(Exception):
    """Raised when settings.yml exists but cannot be parsed or validated.

    This differentiates between a missing settings file (which is treated as
    "use defaults") and a present-but-bad file, which should surface as an
    actionable error to the caller / end user.
    """


def _home_dir() -> Path:
    home = os.environ.get("POCKETSCOPE_HOME")
    if home:
        return Path(home).expanduser()
    return Path(os.path.expanduser("~/.pocketscope"))


def _load_yaml(path: Path | Traversable) -> Mapping[str, Any]:
    if isinstance(path, Path):
        if not path.exists():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:  # pragma: no cover - pass through as load error
            raise SettingsLoadError(
                f"Failed to read settings file: {path}: {exc}"
            ) from exc
    else:
        if not path.is_file():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:  # pragma: no cover - pass through as load error
            raise SettingsLoadError(
                "Failed to read packaged settings defaults"
            ) from exc
    try:
        data = yaml.safe_load(text) or {}
    except Exception as exc:
        raise SettingsLoadError(f"Invalid YAML in settings file {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise SettingsLoadError(f"Settings file {path} must contain a mapping")
    return data


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = {k: v for k, v in base.items()}
    for key, value in override.items():
        if isinstance(value, Mapping):
            existing = merged.get(key)
            if isinstance(existing, Mapping):
                merged[key] = _deep_merge(cast(Mapping[str, Any], existing), value)
                continue
        merged[key] = value
    return merged


def _default_settings_path() -> Path | Traversable:
    try:
        return resources.files(__package__).joinpath("settings.yml")
    except Exception:  # pragma: no cover - importlib.resources fallback
        return Path(__file__).with_name("settings.yml")


class SettingsStore:
    """Load and save :class:`Settings` to disk."""

    _debounce: ClassVar[asyncio.TimerHandle | None] = None

    @staticmethod
    def settings_path() -> Path:
        """Return the path to the settings YAML file."""
        return _home_dir() / "settings.yml"

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
        - If the file exists but cannot be read, parsed as YAML, or validated
          against the Settings schema, raise SettingsLoadError with context.
        """
        path = cls.settings_path()
        cls.ensure_home()
        defaults_path = _default_settings_path()
        defaults = _load_yaml(defaults_path)
        data = defaults
        if path.exists():
            overrides = _load_yaml(path)
            data = _deep_merge(defaults, overrides)
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
        payload = settings.model_dump(mode="python")
        tmp.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
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
