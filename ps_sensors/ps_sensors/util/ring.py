"""Ring buffer implementation."""

from collections import deque
from typing import Generic, TypeVar

T = TypeVar("T")


class RingBuffer(Generic[T]):
    """Fixed-size ring buffer with efficient append and iteration."""

    def __init__(self, maxsize: int) -> None:
        """
        Create a ring buffer with fixed maximum size.

        Args:
            maxsize: Maximum number of elements
        """
        self._buffer: deque[T] = deque(maxlen=maxsize)

    def append(self, item: T) -> None:
        """Add item to buffer (oldest item dropped if full)."""
        self._buffer.append(item)

    def __len__(self) -> int:
        """Return current number of items."""
        return len(self._buffer)

    def __iter__(self):
        """Iterate over items (oldest to newest)."""
        return iter(self._buffer)

    def latest(self) -> T | None:
        """Return most recent item or None if empty."""
        return self._buffer[-1] if self._buffer else None

    def clear(self) -> None:
        """Remove all items."""
        self._buffer.clear()

