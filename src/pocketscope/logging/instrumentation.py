"""Logging and telemetry instrumentation helpers."""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, Iterator, ParamSpec, TypeVar

from .telemetry import get_registry

P = ParamSpec("P")
T = TypeVar("T")


def _levelno(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, level.upper(), logging.INFO)


def _summarize_args(args: tuple[Any, ...], kwargs: dict[str, Any], redact: set[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for idx, arg in enumerate(args):
        key = f"arg{idx}"
        summary[key] = "<redacted>" if key in redact else _safe_repr(arg)
    for key, value in kwargs.items():
        summary[key] = "<redacted>" if key in redact else _safe_repr(value)
    return summary


def _safe_repr(value: Any) -> str:
    try:
        text = repr(value)
    except Exception:
        text = object.__repr__(value)
    if len(text) > 120:
        return text[:117] + "..."
    return text


def log_call(
    *,
    level: str | int = "DEBUG",
    redact: set[str] | None = None,
    min_duration_ms: float | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator that logs function calls with duration and arguments."""

    levelno = _levelno(level)
    redact = redact or set()

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        logger = logging.getLogger(func.__module__)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            enabled = logger.isEnabledFor(levelno)
            start = time.perf_counter() if enabled or min_duration_ms is not None else 0.0
            try:
                result = func(*args, **kwargs)
            except Exception:
                if logger.isEnabledFor(logging.ERROR):
                    logger.exception(
                        "call failed",
                        extra={
                            "function": func.__qualname__,
                            "args": _summarize_args(args, kwargs, redact),
                        },
                    )
                raise
            if enabled:
                duration_ms = (time.perf_counter() - start) * 1000.0
                emit_level = levelno
                if min_duration_ms is not None and duration_ms >= min_duration_ms:
                    emit_level = max(levelno, logging.INFO)
                if logger.isEnabledFor(emit_level):
                    logger.log(
                        emit_level,
                        "call",
                        extra={
                            "function": func.__qualname__,
                            "duration_ms": round(duration_ms, 3),
                            "args": _summarize_args(args, kwargs, redact),
                        },
                    )
            return result

        return wrapper

    return decorator


def measure_latency(
    *,
    name: str,
    buckets: tuple[float, ...] | None = None,
    level: str | int = "DEBUG",
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator that records histogram latency and logs duration."""

    levelno = _levelno(level)
    registry = get_registry()
    histogram = registry.histogram(name, buckets=buckets)

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        logger = logging.getLogger(func.__module__)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            enabled = logger.isEnabledFor(levelno)
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start
                histogram.observe(duration)
                if enabled:
                    logger.log(
                        levelno,
                        "latency",
                        extra={
                            "function": func.__qualname__,
                            "duration_ms": round(duration * 1000.0, 3),
                            "metric": name,
                        },
                    )
            return result

        return wrapper

    return decorator


def count_exceptions(
    *,
    etype: type[BaseException] = Exception,
    counter: str = "errors_total",
    level: str | int = "ERROR",
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator that increments a counter when exceptions are raised."""

    registry = get_registry()
    metric = registry.counter(counter)
    levelno = _levelno(level)

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        logger = logging.getLogger(func.__module__)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except etype:
                metric.inc()
                if logger.isEnabledFor(levelno):
                    logger.log(
                        levelno,
                        "exception",
                        exc_info=True,
                        extra={"function": func.__qualname__},
                    )
                raise

        return wrapper

    return decorator


@contextmanager
def span(name: str, **fields: Any) -> Iterator[None]:
    """Context manager that logs span start/stop and duration."""

    logger = logging.getLogger(name)
    start = time.perf_counter()
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("span.start", extra=fields)
    try:
        yield
    except Exception:
        if logger.isEnabledFor(logging.ERROR):
            logger.error("span.error", exc_info=True, extra=fields)
        raise
    else:
        duration_ms = (time.perf_counter() - start) * 1000.0
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("span.finish", extra={**fields, "duration_ms": round(duration_ms, 3)})


__all__ = ["log_call", "measure_latency", "count_exceptions", "span"]
