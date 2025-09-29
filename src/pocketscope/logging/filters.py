"""Logging filters for PocketScope."""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, Iterable, Tuple


class RateLimitFilter(logging.Filter):
    """Token bucket rate limiting per log level."""

    def __init__(self, *, debug_qps: float = 0.0, limits: Dict[int, float] | None = None) -> None:
        super().__init__()
        self._limits = limits or {}
        if debug_qps:
            self._limits[logging.DEBUG] = debug_qps
        self._tokens: Dict[int, float] = {level: limit for level, limit in self._limits.items()}
        self._last_check: Dict[int, float] = {level: time.monotonic() for level in self._limits}

    def filter(self, record: logging.LogRecord) -> bool:
        limit = self._limits.get(record.levelno)
        if not limit:
            return True
        now = time.monotonic()
        last = self._last_check.get(record.levelno, now)
        elapsed = now - last
        self._last_check[record.levelno] = now
        tokens = self._tokens.get(record.levelno, limit)
        tokens = min(limit, tokens + elapsed * limit)
        if tokens >= 1.0:
            tokens -= 1.0
            self._tokens[record.levelno] = tokens
            return True
        self._tokens[record.levelno] = tokens
        return False


class DuplicateFilter(logging.Filter):
    """Suppress duplicate log messages within a window."""

    def __init__(self, *, window_seconds: float = 5.0) -> None:
        super().__init__()
        self.window = window_seconds
        self._recent: Dict[Tuple[str, int, str], float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, _normalise(record.getMessage()))
        now = time.monotonic()
        last = self._recent.get(key)
        if last and (now - last) < self.window:
            return False
        self._recent[key] = now
        # purge stale entries occasionally
        if len(self._recent) > 1024:
            threshold = now - self.window
            self._recent = {k: t for k, t in self._recent.items() if t >= threshold}
        return True


def _normalise(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip())


class RedactionFilter(logging.Filter):
    """Apply regex-based redactions to log messages and arguments."""

    def __init__(self, *, rules: Iterable[Dict[str, str]] | None = None) -> None:
        super().__init__()
        self._rules: list[tuple[re.Pattern[str], str]] = []
        for rule in rules or []:
            pattern = rule.get("pattern")
            replacement = rule.get("replacement", "<redacted>")
            if not pattern:
                continue
            try:
                compiled = re.compile(pattern)
            except re.error:
                continue
            self._rules.append((compiled, replacement))

    def _apply(self, value: str) -> str:
        for pattern, replacement in self._rules:
            value = pattern.sub(replacement, value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._apply(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._apply(arg) if isinstance(arg, str) else arg for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: self._apply(value) if isinstance(value, str) else value for key, value in record.args.items()
            }
        return True


__all__ = ["RateLimitFilter", "DuplicateFilter", "RedactionFilter"]
