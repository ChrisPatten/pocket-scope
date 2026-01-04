"""PPS (Pulse Per Second) capture using GPIO."""

import logging
import threading
import time
from typing import Any

from ..errors import PpsNotAvailableError
from ..util.timing import monotonic_ns
from .model import PpsTick

logger = logging.getLogger(__name__)

# Try to import RPi.GPIO, but allow graceful degradation
try:
    import RPi.GPIO as GPIO

    HAS_GPIO = True
except (ImportError, RuntimeError):
    HAS_GPIO = False
    logger.warning("RPi.GPIO not available, PPS will not work")


class PpsListener:
    """
    PPS pulse capture using GPIO edge detection.

    Listens for rising edge on configured GPIO pin and captures
    precise timestamp for each pulse.
    """

    def __init__(
        self,
        gpio_pin: int = 18,
        required: bool = False,
    ) -> None:
        """
        Create PPS listener.

        Args:
            gpio_pin: BCM GPIO pin number for PPS input
            required: If True, raise error if GPIO not available

        Raises:
            PpsNotAvailableError: If required=True and GPIO unavailable
        """
        self.gpio_pin = gpio_pin
        self.required = required

        if required and not HAS_GPIO:
            raise PpsNotAvailableError("RPi.GPIO not available but PPS required")

        self._active = False
        self._lock = threading.Lock()
        self._event = threading.Event()

        self._tick_count = 0
        self._last_tick_ns = 0

    def start(self) -> None:
        """Start PPS capture."""
        if not HAS_GPIO:
            if self.required:
                raise PpsNotAvailableError("RPi.GPIO not available")
            logger.warning("PPS not available, start() ignored")
            return

        if self._active:
            logger.warning("PPS already active")
            return

        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(
                self.gpio_pin,
                GPIO.RISING,
                callback=self._edge_callback,
            )
            self._active = True
            logger.info(f"PPS capture started on GPIO {self.gpio_pin}")
        except Exception as e:
            raise PpsNotAvailableError(f"Cannot setup PPS GPIO: {e}") from e

    def stop(self) -> None:
        """Stop PPS capture."""
        if not HAS_GPIO or not self._active:
            return

        try:
            GPIO.remove_event_detect(self.gpio_pin)
            GPIO.cleanup(self.gpio_pin)
            self._active = False
            logger.info("PPS capture stopped")
        except Exception as e:
            logger.error(f"Error stopping PPS: {e}")

    def wait_for_tick(self, timeout: float | None = None) -> PpsTick | None:
        """
        Wait for next PPS tick.

        Args:
            timeout: Maximum time to wait in seconds (None = infinite)

        Returns:
            PpsTick with timestamp and count, or None on timeout
        """
        if not HAS_GPIO or not self._active:
            return None

        self._event.clear()
        if self._event.wait(timeout):
            with self._lock:
                return PpsTick(
                    tick_ns=self._last_tick_ns,
                    tick_count=self._tick_count,
                )
        return None

    def latest_tick(self) -> PpsTick | None:
        """
        Get most recent tick without waiting.

        Returns:
            PpsTick or None if no tick received yet
        """
        with self._lock:
            if self._tick_count == 0:
                return None
            return PpsTick(
                tick_ns=self._last_tick_ns,
                tick_count=self._tick_count,
            )

    def _edge_callback(self, channel: int) -> None:
        """GPIO interrupt callback for rising edge."""
        tick_ns = monotonic_ns()
        with self._lock:
            self._tick_count += 1
            self._last_tick_ns = tick_ns
        self._event.set()
        logger.debug(f"PPS tick {self._tick_count} at {tick_ns}")

