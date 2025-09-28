"""Structured JSON logging formatter for PocketScope."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable

from .context import get_context


class JsonFormatter(logging.Formatter):
    """Render log records as compact JSON lines."""

    DEFAULT_FIELDS = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
    }

    def __init__(
        self,
        *,
        utc: bool = True,
        context_fields: Iterable[str] | None = None,
    ) -> None:
        super().__init__()
        self._utc = utc
        self._context_fields = list(context_fields or ("session_id", "request_id"))

    def format(self, record: logging.LogRecord) -> str:
        if self._utc:
            timestamp = datetime.fromtimestamp(record.created, timezone.utc)
        else:
            timestamp = datetime.fromtimestamp(record.created)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        payload: dict[str, object] = {
            "ts": timestamp.isoformat(timespec="microseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "file": record.pathname,
            "line": record.lineno,
            "func": record.funcName,
            "pid": os.getpid(),
            "tid": record.thread,
        }
        ctx = get_context()
        for field in self._context_fields:
            if field in ctx:
                payload[field] = ctx[field]
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self.DEFAULT_FIELDS and not key.startswith("_")
        }
        if record.exc_info:
            extras["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            extras["stack"] = self.formatStack(record.stack_info)
        payload.update(extras)
        return json.dumps(payload, separators=(",", ":"), default=self._repr)

    @staticmethod
    def _repr(value: object) -> str:
        return repr(value)


class HumanFormatter(logging.Formatter):
    """Readable formatter that optionally colours log levels."""

    COLORS = {
        "DEBUG": "\033[36m",  # cyan
        "INFO": "\033[32m",  # green
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",  # red
        "CRITICAL": "\033[35m",  # magenta
    }
    RESET = "\033[0m"

    def __init__(self, *, use_color: bool = False) -> None:
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        super().__init__(fmt=fmt, datefmt="%Y-%m-%dT%H:%M:%S")
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        if self._use_color and record.levelname in self.COLORS:
            levelname = record.levelname
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"
            try:
                output = super().format(record)
            finally:
                record.levelname = levelname
            return output
        return super().format(record)


__all__ = ["JsonFormatter", "HumanFormatter"]
