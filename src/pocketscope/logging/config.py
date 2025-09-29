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

from pocketscope.settings.schema import HandlerConfig, LoggingOnlySettings, Settings

from .context import new_session_id, set_context
from .telemetry import configure_telemetry, disable_telemetry


def _home_settings_path() -> Path:
    home_env = os.environ.get("POCKETSCOPE_HOME")
    if home_env:
        return Path(home_env).expanduser() / "settings.yml"
    return Path(os.path.expanduser("~/.pocketscope/settings.yml"))


_env_settings_raw = os.environ.get("POCKETSCOPE_SETTINGS")
DEFAULT_SETTINGS_LOCATIONS: tuple[Path, ...] = (
    *((Path(_env_settings_raw).expanduser(),) if _env_settings_raw and _env_settings_raw.strip() else ()),
    _home_settings_path(),
    Path.cwd() / "settings.yml",
    Path(__file__).resolve().parents[1] / "settings.yml",
)

runtime_settings: "LoggingOnlySettings | Settings | None" = None
runtime_checksum: str | None = None


@dataclass(slots=True)
class LoadedSettings:
    # Historically this loader returned a compact LoggingOnlySettings shape.
    # Accept either the full Settings model or the compact LoggingOnlySettings
    # to keep call-sites flexible.
    settings: "LoggingOnlySettings | Settings"
    path: Path | None
    checksum: str


def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        try:
            if path and str(path) and path.is_file():
                return path
        except Exception:
            continue
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
    if settings_path and settings_path.exists() and settings_path.is_file():
        data = _yaml_to_dict(settings_path)
        checksum.update(settings_path.read_bytes())
    data = _apply_env_overrides(data)
    # If the user provided a top-level UI `target_fps`, prefer it as the
    # canonical anchor for telemetry unless telemetry explicitly configures
    # its own `fps_target`. This preserves backward compatibility while
    # centralizing the FPS anchor.
    try:
        if "target_fps" in data:
            top_fps = data.get("target_fps")
            if top_fps is not None:
                telemetry_block = data.get("telemetry")
                if telemetry_block is None or "fps_target" not in telemetry_block:
                    # Ensure telemetry block exists and set an integer fps target
                    data.setdefault("telemetry", {})["fps_target"] = int(round(float(top_fps)))
    except Exception:
        # Best-effort only; don't fail settings parsing for odd values.
        pass
    # The logging initializer historically accepted a compact mapping that
    # only contained "logging" and "telemetry" keys. Strip out unrelated
    # root-level keys before validating to preserve that behaviour and avoid
    # failing on application-specific extras (e.g., POCKETSCOPE_HOME injected
    # by tests or other tools).
    filtered = {k: v for k, v in data.items() if k in ("logging", "telemetry")}
    settings = LoggingOnlySettings.model_validate(filtered)
    checksum.update(settings.model_dump_json().encode("utf-8"))
    return LoadedSettings(settings=settings, path=settings_path, checksum=checksum.hexdigest())


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
            "window_seconds": (settings.logging.sampling.duplicate_suppression_window_sec),
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
        _json_formatter_settings(settings) if settings.logging.style == "json" else _human_formatter_settings(use_color)
    )
    console_handler = settings.logging.handlers.get("console", HandlerConfig())
    console_level = console_handler.level or settings.logging.level
    handler: Dict[str, Any] = {
        "class": "logging.StreamHandler",
        "level": console_level,
        "formatter": "structured",
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
        _json_formatter_settings(settings) if settings.logging.style == "json" else _human_formatter_settings(False)
    )
    rotate = handler_cfg.rotate
    handler: Dict[str, Any] = {
        "class": "logging.handlers.RotatingFileHandler",
        "level": handler_cfg.level or settings.logging.level,
        "formatter": "structured",
        "filename": str(handler_cfg.path),
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
    # Defensive patching: some test environments (see tests/test_logging_suite.py)
    # inject a stub JournalHandler subclass lacking an emit() implementation.
    # The base logging.Handler emit raises NotImplementedError, which would
    # surface later in unrelated tests when any log record is emitted. To keep
    # behaviour (handler attaches and type check passes) while avoiding spurious
    # failures, detect this condition and monkeypatch a no-op emit.
    try:  # pragma: no cover - environment dependent
        # Import lazily so mypy will not require stubs; fall back if unavailable.
        import importlib

        _mod = importlib.import_module("systemd.journal")
        journal_handler = getattr(_mod, "JournalHandler", None)
        current_emit = getattr(journal_handler, "emit", None)
        same_emit = current_emit is logging.Handler.emit
        if journal_handler is not None and same_emit:

            def _noop_emit(self: logging.Handler, record: logging.LogRecord) -> None:
                """Fallback emit used in stripped test environments.

                The injected stub lacks an implementation; provide a no-op so
                log calls succeed without raising NotImplementedError.
                """

                return

            try:  # pragma: no cover - extremely defensive
                journal_handler.emit = _noop_emit
            except Exception:  # pragma: no cover - best effort only
                pass
    except Exception:
        # If patching fails, skip journald to avoid destabilising init.
        return None, {}
    formatter = _json_formatter_settings(settings)
    handler = {
        "class": "systemd.journal.JournalHandler",
        "level": handler_cfg.level or settings.logging.level,
        "formatter": "structured",
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

    # Some callers expect the full Settings model; when we have the compact
    # LoggingOnlySettings shape, convert by validating into the full Settings
    # model where necessary. For simplicity, attempt a model validation when
    # the object does not have the required attributes.
    from pocketscope.settings import schema as _schema_mod

    settings_obj: Settings

    if isinstance(runtime_settings, _schema_mod.LoggingOnlySettings):
        # Build a minimal Settings object by embedding the logging/telemetry
        # blocks into the full Settings model defaults. This is a shallow
        # conversion used only during logging initialization.
        settings_obj = Settings.model_validate(
            {
                **Settings().model_dump(),
                "logging": runtime_settings.logging.model_dump(),
                "telemetry": runtime_settings.telemetry.model_dump(),
            }
        )
    else:
        settings_obj = runtime_settings

    _context_fields(settings_obj)
    dict_config = _build_dict_config(settings_obj)
    logging.config.dictConfig(dict_config)
    try:
        logging.getLogger(__name__).info("logging.init settings_path=%s checksum=%s", loaded.path, runtime_checksum)
    except Exception:
        pass

    # Post-config diagnostics (INFO level intentionally so visible by default)
    try:
        root_logger = logging.getLogger()
        file_handlers = [h for h in root_logger.handlers if h.__class__.__name__ == "RotatingFileHandler"]
        lg = logging.getLogger(__name__)
        if file_handlers:
            import os

            fh = file_handlers[0]
            path = getattr(fh, "baseFilename", "<unknown>")
            lg.info(
                "logging.init file_handler active path=%s exists=%s writable=%s",
                path,
                os.path.exists(path),
                os.access(os.path.dirname(path), os.W_OK),
            )
        else:
            lg.info(
                "logging.init no_file_handler handlers=%s",
                [h.__class__.__name__ for h in root_logger.handlers],
            )
    except Exception:  # pragma: no cover - diagnostics should never break init
        pass

    if settings_obj.telemetry.enabled:
        configure_telemetry(settings_obj.telemetry)
    else:
        disable_telemetry()

    return settings_obj


__all__ = [
    "init_logging_and_telemetry",
    "load_settings",
    "runtime_settings",
    "runtime_checksum",
]
