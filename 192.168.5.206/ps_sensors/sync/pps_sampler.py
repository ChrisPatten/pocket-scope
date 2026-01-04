"""PPS-aligned sampling coordinator."""

import logging
from dataclasses import dataclass

from ..gps.model import GpsFix, PpsTick
from ..gps.pps import PpsListener
from ..gps.uart import GpsUartReader
from ..icm20948.mag_ak09916 import AK09916Stream
from ..icm20948.model import ImuSample, MagSample
from ..icm20948.spi import ICM20948

logger = logging.getLogger(__name__)


@dataclass
class TickSample:
    """Combined sensor sample aligned to PPS tick."""

    tick: PpsTick
    gps: GpsFix
    imu: ImuSample
    mag: MagSample | None


class PpsSampler:
    """
    Coordinate sampling of GPS+IMU+MAG on PPS boundaries.

    Waits for PPS ticks and captures synchronized sensor readings
    suitable for logging or live display at 1 Hz.
    """

    def __init__(
        self,
        gps: GpsUartReader,
        icm: ICM20948,
        mag: AK09916Stream,
        pps: PpsListener,
    ) -> None:
        """
        Create PPS sampler.

        Args:
            gps: GPS UART reader (must be started)
            icm: ICM-20948 IMU (must be opened and initialized)
            mag: AK09916 magnetometer stream (must be started)
            pps: PPS listener (must be started)
        """
        self.gps = gps
        self.icm = icm
        self.mag = mag
        self.pps = pps

    def wait_and_sample(self, timeout: float | None = 2.0) -> TickSample | None:
        """
        Wait for next PPS tick and capture synchronized sensor readings.

        Args:
            timeout: Maximum time to wait for PPS tick (None = infinite)

        Returns:
            TickSample with all sensor data, or None on timeout
        """
        # Wait for PPS tick
        tick = self.pps.wait_for_tick(timeout)
        if not tick:
            logger.warning("PPS timeout")
            return None

        # Capture sensor readings as quickly as possible after tick
        # GPS: snapshot latest parsed fix (non-blocking)
        gps_fix = self.gps.latest()

        # IMU: read current accel/gyro (blocking SPI read ~1ms)
        imu_sample = self.icm.read_imu()

        # Mag: get latest cached sample from background poller (non-blocking)
        mag_sample = self.mag.latest()

        sample = TickSample(
            tick=tick,
            gps=gps_fix,
            imu=imu_sample,
            mag=mag_sample,
        )

        logger.debug(
            f"Tick {tick.tick_count}: "
            f"GPS={gps_fix.fix_type} age={gps_fix.age_s:.2f}s, "
            f"IMU ax={imu_sample.ax_g:.3f}g, "
            f"MAG={'OK' if mag_sample else 'n/a'}"
        )

        return sample

    def sample_now(self) -> TickSample | None:
        """
        Capture sensor readings immediately without waiting for PPS.

        Returns:
            TickSample with current sensor data (tick from latest PPS)
        """
        # Get latest PPS tick (may be stale)
        tick = self.pps.latest_tick()
        if not tick:
            logger.warning("No PPS tick received yet")
            return None

        # Capture sensor readings
        gps_fix = self.gps.latest()
        imu_sample = self.icm.read_imu()
        mag_sample = self.mag.latest()

        return TickSample(
            tick=tick,
            gps=gps_fix,
            imu=imu_sample,
            mag=mag_sample,
        )

