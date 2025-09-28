"""Helpers for file rotation configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from pocketscope.settings_schema import HandlerConfig


def build_rotating_file_handler(handler: HandlerConfig) -> Dict[str, object]:
    if not handler.path:
        raise ValueError("Handler path required for rotating file handler")
    path = Path(handler.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate = handler.rotate
    return {
        "class": "logging.handlers.RotatingFileHandler",
        "level": handler.level or "INFO",
        "formatter": "structured",
        "filename": str(path),
        "encoding": "utf-8",
        "maxBytes": rotate.max_bytes if rotate else 5 * 1024 * 1024,
        "backupCount": rotate.backup_count if rotate else 3,
    }


__all__ = ["build_rotating_file_handler"]
