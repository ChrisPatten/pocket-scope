"""Compatibility shim: re-export logging/telemetry models from the
central `pocketscope.settings.schema` module so existing imports continue to
work.

This file intentionally exposes a minimal, backwards-compatible surface.
Prefer importing from `pocketscope.settings.schema` for any new code.
"""

from pocketscope.settings.schema import (
    HandlerConfig,
    LoggingOnlySettings,
    LoggingSettings,
    RotateSettings,
    TelemetrySettings,
)

# Historical alias (some callers import `Settings` from this module)
Settings = LoggingOnlySettings

__all__ = [
    "Settings",
    "LoggingSettings",
    "TelemetrySettings",
    "HandlerConfig",
    "RotateSettings",
]
