"""GPS UART NMEA reader."""

import logging
import threading
import time
from typing import Any

import serial

from ..errors import GpsPortError
from ..util.nmea import is_nmea_sentence, parse_gga, parse_rmc
from ..util.timing import monotonic_ns
from .model import GpsFix

logger = logging.getLogger(__name__)


class GpsUartReader:
    """
    Background thread reading NMEA sentences from GPS UART.

    Supports both GP and GN talkers (GPS and GLONASS).
    Merges RMC and GGA sentences to provide best available fix.
    """

    def __init__(
        self,
        port: str = "/dev/serial0",
        baudrate: int = 9600,
        timeout: float = 1.0,
    ) -> None:
        """
        Create GPS UART reader.

        Args:
            port: Serial device path
            baudrate: Baud rate (default 9600 for most GPS modules)
            timeout: Read timeout in seconds
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

        self._serial: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

        # Latest parsed data
        self._last_update_ns = 0
        self._time_utc: str | None = None
        self._lat_deg: float | None = None
        self._lon_deg: float | None = None
        self._fix_type = "UNKNOWN"
        self._raw_last: str | None = None

    def start(self) -> None:
        """Start background reader thread."""
        if self._running:
            logger.warning("GPS reader already running")
            return

        try:
            self._serial = serial.Serial(
                self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
            )
            logger.info(f"Opened GPS port {self.port} at {self.baudrate} baud")
        except (serial.SerialException, OSError) as e:
            raise GpsPortError(f"Cannot open GPS port {self.port}: {e}") from e

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        logger.info("GPS reader thread started")

    def stop(self) -> None:
        """Stop background reader thread."""
        if not self._running:
            return

        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._serial:
            self._serial.close()
        logger.info("GPS reader stopped")

    def latest(self) -> GpsFix:
        """
        Get latest GPS fix snapshot.

        Returns:
            GpsFix with current best position and age
        """
        with self._lock:
            age_s = (
                (monotonic_ns() - self._last_update_ns) / 1e9
                if self._last_update_ns > 0
                else 999.0
            )
            return GpsFix(
                time_utc_hms=self._time_utc,
                lat_deg=self._lat_deg,
                lon_deg=self._lon_deg,
                fix_type=self._fix_type,
                age_s=age_s,
                raw_last=self._raw_last,
                timestamp_ns=self._last_update_ns,
            )

    def _read_loop(self) -> None:
        """Background thread: read and parse NMEA sentences."""
        if not self._serial:
            logger.error("Serial port not opened")
            return

        logger.debug("GPS read loop started")
        while self._running:
            try:
                line = self._serial.readline()
                if not line:
                    continue

                try:
                    sentence = line.decode("ascii", errors="ignore").strip()
                except UnicodeDecodeError:
                    continue

                if not is_nmea_sentence(sentence):
                    continue

                # Parse sentence based on type
                if "RMC" in sentence:
                    self._handle_rmc(sentence)
                elif "GGA" in sentence:
                    self._handle_gga(sentence)

            except (serial.SerialException, OSError) as e:
                if self._running:
                    logger.error(f"GPS serial error: {e}")
                break
            except Exception as e:
                logger.error(f"Unexpected GPS error: {e}")

        logger.debug("GPS read loop exited")

    def _handle_rmc(self, sentence: str) -> None:
        """Process RMC sentence."""
        data = parse_rmc(sentence)
        if not data.get("valid"):
            return

        with self._lock:
            now_ns = monotonic_ns()
            self._raw_last = sentence

            # Update time if available
            if data.get("time_utc"):
                self._time_utc = data["time_utc"]

            # Update position if active and coordinates valid
            if data.get("status") == "A":
                if data.get("lat_deg") is not None and data.get("lon_deg") is not None:
                    self._lat_deg = data["lat_deg"]
                    self._lon_deg = data["lon_deg"]
                    self._fix_type = "RMC:A"
                    self._last_update_ns = now_ns
            else:
                # RMC void, but don't clear position if we have GGA fix
                if self._fix_type not in ("GGA:1", "GGA:2"):
                    self._fix_type = "RMC:V"

    def _handle_gga(self, sentence: str) -> None:
        """Process GGA sentence."""
        data = parse_gga(sentence)
        if not data.get("valid"):
            return

        with self._lock:
            now_ns = monotonic_ns()
            self._raw_last = sentence

            # Update time if available
            if data.get("time_utc"):
                self._time_utc = data["time_utc"]

            # Update position if fix quality indicates valid fix
            fix_quality = data.get("fix_quality", "0")
            if fix_quality in ("1", "2", "4", "5"):  # GPS, DGPS, RTK, Float RTK
                if data.get("lat_deg") is not None and data.get("lon_deg") is not None:
                    self._lat_deg = data["lat_deg"]
                    self._lon_deg = data["lon_deg"]
                    self._fix_type = f"GGA:{fix_quality}"
                    self._last_update_ns = now_ns

