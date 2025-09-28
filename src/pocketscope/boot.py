"""Early bootstrapping for PocketScope."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from . import __version__
from .logging.config import init_logging_and_telemetry, runtime_checksum
from .logging.context import set_context

logger = logging.getLogger("pocketscope.boot")


def _git_sha() -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


def boot(settings_path: str | Path | None = None) -> None:
    settings = init_logging_and_telemetry(settings_path)
    checksum = runtime_checksum
    git_sha = _git_sha()
    set_context(app_version=__version__)
    logger.info(
        "PocketScope start",
        extra={
            "version": __version__,
            "git_sha": git_sha,
            "settings_checksum": checksum,
        },
    )
    if not settings.telemetry.enabled:
        logger.info("Telemetry disabled by configuration")


__all__ = ["boot"]
