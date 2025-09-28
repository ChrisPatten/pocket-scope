"""Logging configuration helpers."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import logging.config
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

from pocketscope.settings_schema import HandlerConfig, Settings

from .context import new_session_id, set_context
from .telemetry import configure_telemetry, disable_telemetry

DEFAULT_SETTINGS_LOCATIONS: tuple[Path, ...] = (
    Path(os.environ.get("POCKETSCOPE_SETTINGS", "")),
    Path.cwd() / "settings.yml",
    Path(__file__).resolve().parents[1] / "settings.yml",
)

runtime_settings: Settings | None = None
runtime_checksum: str | None = None


@dataclass(slots=True)
class LoadedSettings:
    settings: Settings
    path: Path | None
    checksum: str


def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if path and path.exists():
            return path
    return None


def _yaml_to_dict(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ValueError("settings.yml must be a mapping at the top level")
        return data


def _apply_env_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    prefix = "POCKETSCOPE_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].lower().split("_")
        cursor = data
        for token in path[:-1]:
            if token not in cursor or not isinstance(cursor[token], dict):
                cursor[token] = {}
            cursor = cursor[token]
        cursor[path[-1]] = _parse_env_value(value)
    return data


def _parse_env_value(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    try:
        if lowered.startswith("0x"):
            return int(lowered, 16)
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        return yaml.safe_load(raw)
    except Exception:
        return raw


def load_settings(path: str | Path | None = None) -> LoadedSettings:
    """Load :class:`Settings` from YAML and environment overrides."""

    settings_path: Path | None
    if path is not None:
        settings_path = Path(path)
    else:
        settings_path = _first_existing(DEFAULT_SETTINGS_LOCATIONS)

    data: Dict[str, Any] = {}
    checksum = hashlib.sha1()
    if settings_path and settings_path.exists():
        data = _yaml_to_dict(settings_path)
        checksum.update(settings_path.read_bytes())
    data = _apply_env_overrides(data)
    settings = Settings.model_validate(data)
    checksum.update(settings.model_dump_json().encode("utf-8"))
    return LoadedSettings(
        settings=settings, path=settings_path, checksum=checksum.hexdigest()
    )


def _context_fields(settings: Settings) -> Dict[str, Any]:
    context: Dict[str, Any] = {}
    if "session_id" in settings.logging.context_fields:
        context["session_id"] = new_session_id()
    set_context(**context)
    return context


def _human_formatter_settings(use_color: bool) -> Dict[str, Any]:
    return {
        "()": "pocketscope.logging.json_formatter.HumanFormatter",
        "use_color": use_color,
    }


def _json_formatter_settings(settings: Settings) -> Dict[str, Any]:
    return {
        "()": "pocketscope.logging.json_formatter.JsonFormatter",
        "utc": settings.logging.utc,
        "context_fields": settings.logging.context_fields,
    }


def _build_filters(settings: Settings) -> Dict[str, Any]:
    return {
        "rate_limit": {
            "()": "pocketscope.logging.filters.RateLimitFilter",
            "debug_qps": settings.logging.sampling.debug_qps,
        },
        "dedupe": {
            "()": "pocketscope.logging.filters.DuplicateFilter",
            "window_seconds": (
                settings.logging.sampling.duplicate_suppression_window_sec
            ),
        },
        "redact": {
            "()": "pocketscope.logging.filters.RedactionFilter",
            "rules": [rule.model_dump() for rule in settings.logging.redactions],
        },
    }


def _ensure_parent(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _journald_available() -> bool:
    try:
        spec = importlib.util.find_spec("systemd.journal")
    except (ImportError, ValueError):
        return False
    return spec is not None


def _build_console_handler(settings: Settings) -> tuple[Dict[str, Any], Dict[str, Any]]:
    use_color = False
    try:
        use_color = os.isatty(1)
    except Exception:
        pass
    formatter = (
        _json_formatter_settings(settings)
        if settings.logging.style == "json"
        else _human_formatter_settings(use_color)
    )
    console_handler = settings.logging.handlers.get("console", HandlerConfig())
    console_level = console_handler.level or settings.logging.level
    handler: Dict[str, Any] = {
        "class": "logging.StreamHandler",
        "level": console_level,
        "formatter": "structured",
        "filters": ["redact", "rate_limit", "dedupe"],
    }
    return handler, {"structured": formatter}


def _build_file_handler(
    settings: Settings,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    handler_cfg = settings.logging.handlers.get("file")
    if not handler_cfg or not handler_cfg.enabled or not handler_cfg.path:
        return None, {}
    _ensure_parent(handler_cfg.path)
    formatter = (
        _json_formatter_settings(settings)
        if settings.logging.style == "json"
        else _human_formatter_settings(False)
    )
    rotate = handler_cfg.rotate
    handler: Dict[str, Any] = {
        "class": "logging.handlers.RotatingFileHandler",
        "level": handler_cfg.level or settings.logging.level,
        "formatter": "structured",
        "filename": str(handler_cfg.path),
        "filters": ["redact", "rate_limit", "dedupe"],
        "encoding": "utf-8",
        "maxBytes": rotate.max_bytes if rotate else 5 * 1024 * 1024,
        "backupCount": rotate.backup_count if rotate else 3,
    }
    return handler, {"structured": formatter}


def _build_journald_handler(
    settings: Settings,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    handler_cfg = settings.logging.handlers.get("journald")
    if not handler_cfg or not handler_cfg.enabled:
        return None, {}
    if not _journald_available():
        return None, {}
    formatter = _json_formatter_settings(settings)
    handler = {
        "class": "systemd.journal.JournalHandler",
        "level": handler_cfg.level or settings.logging.level,
        "formatter": "structured",
        "filters": ["redact", "rate_limit", "dedupe"],
    }
    return handler, {"structured": formatter}


def _build_dict_config(settings: Settings) -> Dict[str, Any]:
    filters = _build_filters(settings)
    console_handler, console_formatter = _build_console_handler(settings)
    handlers: Dict[str, Any] = {}
    formatters: Dict[str, Any] = {}
    if console_handler:
        handlers["console"] = console_handler
        formatters.update(console_formatter)
    file_handler, file_formatter = _build_file_handler(settings)
    if file_handler:
        handlers["file"] = file_handler
        formatters.update(file_formatter)
    journald_handler, journald_formatter = _build_journald_handler(settings)
    if journald_handler:
        handlers["journald"] = journald_handler
        formatters.update(journald_formatter)
    if "structured" not in formatters:
        formatters["structured"] = _json_formatter_settings(settings)
    root_filters = ["redact", "rate_limit", "dedupe"]
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": filters,
        "formatters": formatters,
        "handlers": handlers,
        "root": {
            "level": settings.logging.level,
            "handlers": list(handlers.keys()),
            "filters": root_filters,
        },
        "loggers": {
            name: {
                "level": level,
                "handlers": list(handlers.keys()),
                "propagate": settings.logging.propagate,
            }
            for name, level in settings.logging.loggers.items()
        },
    }


def init_logging_and_telemetry(settings_path: str | Path | None = None) -> Settings:
    """Load settings, initialise logging + telemetry, and expose runtime state."""

    global runtime_settings, runtime_checksum
    loaded = load_settings(settings_path)
    runtime_settings = loaded.settings
    runtime_checksum = loaded.checksum

    _context_fields(runtime_settings)
    dict_config = _build_dict_config(runtime_settings)
    logging.config.dictConfig(dict_config)

    if runtime_settings.telemetry.enabled:
        configure_telemetry(runtime_settings.telemetry)
    else:
        disable_telemetry()

    return runtime_settings


__all__ = [
    "init_logging_and_telemetry",
    "load_settings",
    "runtime_settings",
    "runtime_checksum",
]
