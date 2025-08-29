"""Time abstraction for deterministic and real-time clock sources.

This module provides a TimeSource protocol that can be implemented by either
real-time or simulated time sources, enabling deterministic testing and
replay functionality.

Usage examples:

Real-time usage:
    ts = RealTimeSource()
    start = ts.monotonic()
    await ts.sleep(1.0)
    elapsed = ts.monotonic() - start  # ~1.0 seconds

Simulated time usage:
    ts = SimTimeSource(start=0.0)
    task = asyncio.create_task(ts.sleep(5.0))
    ts.advance(5.0)  # Advances time and wakes up sleepers
    await task  # Completes immediately
"""

from __future__ import annotations

import asyncio
import heapq
import time
from typing import Protocol

__all__ = [
    "TimeSource",
    "RealTimeSource",
    "SimTimeSource",
]


class TimeSource(Protocol):
    """
    Protocol for time sources supporting monotonic time, wall time, and
    async sleep.
    """

    def monotonic(self) -> float:
        """Return monotonic time in seconds (suitable for measuring durations)."""
        ...

    def wall_time(self) -> float:
        """Return wall-clock time as seconds since Unix epoch."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Sleep for the specified number of seconds."""
        ...


class RealTimeSource:
    """Real-time implementation using system clocks and asyncio.sleep."""

    def monotonic(self) -> float:
        """Return monotonic time from time.monotonic()."""
        return time.monotonic()

    def wall_time(self) -> float:
        """Return wall time from time.time()."""
        return time.time()

    async def sleep(self, seconds: float) -> None:
        """Sleep using asyncio.sleep()."""
        await asyncio.sleep(seconds)


class SimTimeSource:
    """Deterministic simulated clock.

    Features:
    - Starts at configurable time (default t0=0.0 monotonic)
    - advance(dt) steps time forward and resolves scheduled sleepers
    - set_time(t) sets absolute sim time (forward only)
    - sleep(sec) registers a waiter until current_time >= due_time
    - No busy loops; all awaits resolve via task notifications

    Example:
        ts = SimTimeSource(start=100.0)
        task = asyncio.create_task(ts.sleep(1.5))
        assert not task.done()
        ts.advance(1.5)  # Time advances to 101.5, task completes
        await task  # Returns immediately
    """

    def __init__(self, *, start: float = 0.0) -> None:
        """Initialize simulated time source.

        Args:
            start: Starting monotonic time value
        """
        self._monotonic_time: float = float(start)
        self._wall_time: float = time.time()  # Snapshot real wall time at creation
        # Priority queue of (due_time, task_id, future) for pending sleeps
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._task_counter: int = 0

    def monotonic(self) -> float:
        """Return current simulated monotonic time."""
        return self._monotonic_time

    def wall_time(self) -> float:
        """Return wall time (real time at creation + simulated elapsed)."""
        return self._wall_time + self._monotonic_time

    def set_time(self, t: float) -> None:
        """Set absolute simulated time (forward only).

        Args:
            t: New monotonic time value (must be >= current time)

        Raises:
            ValueError: If t < current monotonic time
        """
        if t < self._monotonic_time:
            raise ValueError(f"Cannot set time backwards: {t} < {self._monotonic_time}")

        self._monotonic_time = t
        self._wake_due_sleepers()

    def advance(self, dt: float) -> None:
        """Advance simulated time by delta.

        Args:
            dt: Time delta to advance (must be >= 0)

        Raises:
            ValueError: If dt < 0
        """
        if dt < 0:
            raise ValueError(f"Cannot advance time backwards: dt={dt}")

        self._monotonic_time += dt
        self._wake_due_sleepers()

    async def sleep(self, seconds: float) -> None:
        """Sleep for simulated seconds.

        Registers a waiter that will be resolved when time advances
        past the due time.

        Args:
            seconds: Duration to sleep (must be >= 0)

        Raises:
            ValueError: If seconds < 0
        """
        if seconds < 0:
            raise ValueError(f"Sleep duration must be non-negative: {seconds}")

        if seconds == 0:
            # Allow other tasks to run
            await asyncio.sleep(0)
            return

        due_time = self._monotonic_time + seconds

        # Create future that will be resolved when time advances
        future: asyncio.Future[None] = asyncio.Future()

        # Add to priority queue with unique task ID to handle ties
        self._task_counter += 1
        heapq.heappush(self._sleepers, (due_time, self._task_counter, future))

        # Wait for the future to be resolved
        await future

    def _wake_due_sleepers(self) -> None:
        """Wake up all sleepers whose due time has passed."""
        current_time = self._monotonic_time

        # Process all sleepers whose time has come
        while self._sleepers and self._sleepers[0][0] <= current_time:
            due_time, task_id, future = heapq.heappop(self._sleepers)

            # Only resolve if not already done (handles cancellation)
            if not future.done():
                future.set_result(None)

    def next_due_monotonic(self) -> float | None:
        """Return the monotonic time of the next scheduled sleeper, if any.

        This is useful for tests that need to advance time precisely to the
        next event without overshooting.

        Returns:
            Next due time or None if no sleepers pending
        """
        if not self._sleepers:
            return None
        return self._sleepers[0][0]
