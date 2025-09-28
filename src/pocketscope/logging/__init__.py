"""PocketScope logging and telemetry utilities."""

from __future__ import annotations

from .config import init_logging_and_telemetry, load_settings, runtime_settings
from .context import (
    context_scope,
    get_context,
    get_request_id,
    get_session_id,
    new_request_id,
    new_session_id,
    set_context,
)
from .instrumentation import count_exceptions, log_call, measure_latency, span
from .telemetry import TelemetryRegistry, get_registry

__all__ = [
    "init_logging_and_telemetry",
    "load_settings",
    "runtime_settings",
    "context_scope",
    "get_context",
    "get_request_id",
    "get_session_id",
    "new_request_id",
    "new_session_id",
    "set_context",
    "count_exceptions",
    "log_call",
    "measure_latency",
    "span",
    "TelemetryRegistry",
    "get_registry",
]
