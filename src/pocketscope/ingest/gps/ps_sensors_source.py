"""
GPS source adapter for ps_sensors module.

Polls GPS UART reader and publishes GpsFix events to the event bus.
Compatible with ps_sensors GPS module using NMEA serial interface.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from pocketscope.core.events import EventBus, pack
from pocketscope.core.models import GpsFix

logger = logging.getLogger(__name__)

__all__ = ["PsSensorsGpsSource"]


class PsSensorsGpsSource:
    """
    Polls ps_sensors GPS module and publishes position fixes to event bus.
    
    Requires ps_sensors package to be installed. If hardware is not available
    or ps_sensors cannot be imported, run() will log a warning and exit gracefully.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        poll_hz: float = 1.0,
        topic: str = "gps.position",
        port: str = "/dev/serial0",
        baudrate: int = 9600,
    ) -> None:
        """
        Create GPS source adapter.
        
        Args:
            bus: EventBus to publish GPS fixes to
            poll_hz: Polling frequency in Hz (default: 1.0)
            topic: EventBus topic for GPS fixes (default: "gps.position")
            port: Serial device path for GPS (default: "/dev/serial0")
            baudrate: GPS module baud rate (default: 9600)
        """
        self._bus = bus
        self._topic = topic
        self._interval = 1.0 / max(0.1, float(poll_hz))
        self._port = port
        self._baudrate = baudrate
        
        self._running = False
        self._stop_event = asyncio.Event()
        self._gps_reader: Optional[object] = None  # ps_sensors.gps.uart.GpsUartReader

    async def run(self) -> None:
        """Start GPS polling loop."""
        if self._running:
            logger.warning("GPS source already running")
            return
        
        self._running = True
        
        # Try to import and initialize ps_sensors GPS module
        try:
            from ps_sensors.gps.uart import GpsUartReader  # type: ignore[import-untyped]
            from ps_sensors.errors import GpsPortError  # type: ignore[import-untyped]
            
            try:
                self._gps_reader = GpsUartReader(
                    port=self._port,
                    baudrate=self._baudrate,
                    timeout=1.0,
                )
                self._gps_reader.start()  # type: ignore[attr-defined]
                logger.info(f"GPS reader started on {self._port}")
            except GpsPortError as e:
                logger.warning(f"Cannot open GPS port {self._port}: {e}")
                self._running = False
                return
            except Exception as e:
                logger.warning(f"Failed to initialize GPS reader: {e}")
                self._running = False
                return
                
        except ImportError as e:
            logger.warning(f"ps_sensors module not available: {e}")
            self._running = False
            return
        
        # Start polling loop
        try:
            await self._polling_loop()
        finally:
            # Cleanup
            if self._gps_reader is not None:
                try:
                    self._gps_reader.stop()  # type: ignore[attr-defined]
                    logger.info("GPS reader stopped")
                except Exception as e:
                    logger.error(f"Error stopping GPS reader: {e}")
            self._running = False

    async def stop(self) -> None:
        """Stop GPS polling loop."""
        self._stop_event.set()

    async def _polling_loop(self) -> None:
        """Loop that polls GPS and publishes fixes."""
        logger.debug("GPS polling loop started")
        
        # Track statistics for periodic status logging
        _last_status_log = 0.0
        _status_interval = 30.0  # Log status every 30 seconds
        _fixes_published = 0
        _invalid_fixes = 0
        
        import time
        
        while not self._stop_event.is_set():
            try:
                # Get latest GPS fix from ps_sensors
                ps_fix = self._gps_reader.latest()  # type: ignore[attr-defined]
                
                # Check if fix is valid before publishing
                if self._is_valid_fix(ps_fix):
                    # Convert ps_sensors GpsFix to PocketScope GpsFix
                    pocketscope_fix = self._convert_fix(ps_fix)
                    
                    if pocketscope_fix is not None:
                        # Serialize and publish
                        fix_dict = pocketscope_fix.model_dump()
                        fix_dict["ts"] = pocketscope_fix.ts.isoformat()
                        payload = pack(fix_dict)
                        await self._bus.publish(self._topic, payload)
                        
                        # Log at INFO level so it's visible in production logs
                        logger.info(
                            f"GPS fix published: lat={pocketscope_fix.lat:.6f}, "
                            f"lon={pocketscope_fix.lon:.6f}, age={ps_fix.age_s:.1f}s, "
                            f"fix_type={ps_fix.fix_type}"
                        )
                        _fixes_published += 1
                    else:
                        _invalid_fixes += 1
                else:
                    _invalid_fixes += 1
            except Exception as e:
                logger.error(f"Error polling GPS: {e}", exc_info=True)
            
            # Periodic status logging
            now = time.monotonic()
            if now - _last_status_log >= _status_interval:
                logger.info(
                    f"GPS status: {_fixes_published} fixes published, "
                    f"{_invalid_fixes} invalid/stale fixes filtered "
                    f"(last {_status_interval:.0f}s)"
                )
                _last_status_log = now
                _fixes_published = 0
                _invalid_fixes = 0
            
            # Wait for next poll
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval
                )
                break  # Stop event was set
            except asyncio.TimeoutError:
                pass  # Continue polling
        
        logger.debug("GPS polling loop exited")

    def _is_valid_fix(self, ps_fix: object) -> bool:
        """
        Check if ps_sensors GPS fix is valid for use.
        
        Valid fix criteria:
        - fix_type must indicate active fix (RMC:A or GGA:1+)
        - age must be < 10 seconds
        - lat_deg and lon_deg must not be None
        
        Args:
            ps_fix: ps_sensors GpsFix object
            
        Returns:
            True if fix is valid and fresh
        """
        try:
            # Check fix type
            fix_type = getattr(ps_fix, "fix_type", "UNKNOWN")
            if fix_type == "RMC:A":
                valid_type = True
            elif fix_type.startswith("GGA:") and len(fix_type) > 4:
                # GGA:1, GGA:2, etc. (quality > 0)
                try:
                    quality = int(fix_type[4:])
                    valid_type = quality >= 1
                except (ValueError, IndexError):
                    valid_type = False
            else:
                valid_type = False
            
            if not valid_type:
                return False
            
            # Check age
            age_s = getattr(ps_fix, "age_s", 999.0)
            if age_s > 10.0:
                return False
            
            # Check coordinates
            lat = getattr(ps_fix, "lat_deg", None)
            lon = getattr(ps_fix, "lon_deg", None)
            if lat is None or lon is None:
                return False
            
            # Basic sanity check on coordinates
            if not (-90 <= lat <= 90):
                return False
            if not (-180 <= lon <= 180):
                return False
            
            return True
            
        except Exception as e:
            logger.debug(f"Error validating GPS fix: {e}")
            return False

    def _convert_fix(self, ps_fix: object) -> Optional[GpsFix]:
        """
        Convert ps_sensors GpsFix to PocketScope GpsFix.
        
        Args:
            ps_fix: ps_sensors GpsFix object
            
        Returns:
            PocketScope GpsFix or None if conversion fails
        """
        try:
            # Get coordinates (already validated in _is_valid_fix)
            lat = float(getattr(ps_fix, "lat_deg"))
            lon = float(getattr(ps_fix, "lon_deg"))
            
            # Convert timestamp_ns to datetime
            timestamp_ns = getattr(ps_fix, "timestamp_ns", 0)
            if timestamp_ns > 0:
                # Convert nanoseconds since epoch to datetime
                timestamp_s = timestamp_ns / 1e9
                ts = datetime.fromtimestamp(timestamp_s, tz=timezone.utc)
            else:
                # Fallback to current time if no timestamp
                ts = datetime.now(timezone.utc)
            
            # Create PocketScope GpsFix
            # Note: ps_sensors doesn't provide altitude, speed, track, hdop
            # so these fields are left as None
            return GpsFix(
                ts=ts,
                lat=lat,
                lon=lon,
                alt_m=None,
                speed_mps=None,
                track_deg=None,
                hdop=None,
            )
            
        except Exception as e:
            logger.error(f"Error converting GPS fix: {e}")
            return None

