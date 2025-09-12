"""Shared SPI bus lock for display + touch drivers.

The ILI9341 display (CE0) and XPT2046 touch (CE1) each open their own
spidev device. A mechanical bump on a loose breadboard can momentarily
float chip select lines causing interleaved bytes if both attempt
transfers concurrently. A process-level re‑entrant lock keeps transfers
atomic relative to each other, reducing protocol corruption and spurious
panel lockups.
"""
from __future__ import annotations

import threading

SPI_BUS_LOCK = threading.RLock()

__all__ = ["SPI_BUS_LOCK"]
