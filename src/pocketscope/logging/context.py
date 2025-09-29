"""Context helpers for logging correlation identifiers."""

from __future__ import annotations

import contextlib
import uuid
from contextvars import ContextVar
from typing import Dict, Iterator, Optional

_ContextVar: ContextVar[dict[str, str]] = ContextVar("pocketscope_logging_context", default={})


def get_context() -> Dict[str, str]:
    ctx = _ContextVar.get()
    if not isinstance(ctx, dict):
        return {}
    return dict(ctx)


def set_context(**fields: str | None) -> Dict[str, str]:
    ctx = get_context()
    for key, value in fields.items():
        if value is None:
            ctx.pop(key, None)
        else:
            ctx[key] = value
    _ContextVar.set(ctx)
    return ctx


def new_session_id() -> str:
    return uuid.uuid4().hex


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_session_id(default: Optional[str] = None) -> Optional[str]:
    return get_context().get("session_id", default)


def get_request_id(default: Optional[str] = None) -> Optional[str]:
    return get_context().get("request_id", default)


@contextlib.contextmanager
def context_scope(**fields: str | None) -> Iterator[Dict[str, str]]:
    previous = get_context()
    merged = previous.copy()
    for key, value in fields.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    _ContextVar.set(merged)
    try:
        yield merged
    finally:
        _ContextVar.set(previous)


__all__ = [
    "context_scope",
    "get_context",
    "get_request_id",
    "get_session_id",
    "new_request_id",
    "new_session_id",
    "set_context",
]
