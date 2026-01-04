"""Timing utilities."""

import time


def monotonic_ns() -> int:
    """
    Get current timestamp in nanoseconds.

    Uses time.time_ns() for wall-clock monotonic-ish timing.
    Suitable for timestamping sensor samples.
    """
    return time.time_ns()

