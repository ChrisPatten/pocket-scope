"""ICM-20948 SPI interface with thread-safe bank switching."""

import logging
import struct
import threading
import time
from typing import Any

from ..errors import DeviceNotFoundError, UnexpectedWhoAmIError
from ..util.timing import monotonic_ns
from .model import ImuSample
from .regs import (
    ACCEL_CONFIG,
    ACCEL_SCALE_2G,
    ACCEL_SMPLRT_DIV_1,
    ACCEL_SMPLRT_DIV_2,
    ACCEL_XOUT_H,
    GYRO_CONFIG_1,
    GYRO_SCALE_250DPS,
    GYRO_SMPLRT_DIV,
    ICM20948_WHO_AM_I_VALUE,
    PWR_MGMT_1,
    PWR_MGMT_2,
    REG_BANK_SEL,
    USER_CTRL,
    WHO_AM_I,
)

logger = logging.getLogger(__name__)

# Try to import spidev, allow graceful degradation for testing
try:
    import spidev

    HAS_SPIDEV = True
except ImportError:
    HAS_SPIDEV = False
    logger.warning("spidev not available, ICM-20948 will not work")


class ICM20948:
    """
    ICM-20948 IMU interface over SPI.

    Provides thread-safe access to accelerometer and gyroscope data.
    Bank switching is serialized with internal lock.
    """

    def __init__(
        self,
        bus: int = 0,
        device: int = 0,
        max_speed_hz: int = 1_000_000,
        verify_who_am_i: bool = True,
    ) -> None:
        """
        Create ICM-20948 interface.

        Args:
            bus: SPI bus number
            device: SPI device number
            max_speed_hz: SPI clock speed (1 MHz known good)
            verify_who_am_i: Check WHO_AM_I register on init

        Raises:
            DeviceNotFoundError: If spidev not available
        """
        if not HAS_SPIDEV:
            raise DeviceNotFoundError("spidev module not available")

        self.bus = bus
        self.device = device
        self.max_speed_hz = max_speed_hz
        self.verify_who_am_i = verify_who_am_i

        self._spi: Any = spidev.SpiDev()
        self._lock = threading.Lock()
        self._current_bank = -1  # Track current bank to minimize switching

    def open(self) -> None:
        """Open SPI device."""
        try:
            self._spi.open(self.bus, self.device)
            self._spi.max_speed_hz = self.max_speed_hz
            self._spi.mode = 0  # CPOL=0, CPHA=0
            logger.info(
                f"Opened ICM-20948 on SPI bus {self.bus} device {self.device} "
                f"at {self.max_speed_hz} Hz"
            )
        except Exception as e:
            raise DeviceNotFoundError(f"Cannot open SPI device: {e}") from e

    def close(self) -> None:
        """Close SPI device."""
        try:
            self._spi.close()
            logger.info("Closed ICM-20948 SPI")
        except Exception as e:
            logger.error(f"Error closing SPI: {e}")

    def initialize(self) -> None:
        """
        Initialize ICM-20948: power on, verify WHO_AM_I, configure ranges.

        Raises:
            UnexpectedWhoAmIError: If WHO_AM_I does not match
        """
        # Power on and exit sleep mode
        self.write_reg(0, PWR_MGMT_1, 0x01)  # Auto-select clock
        time.sleep(0.01)  # Wait for power-up

        # Verify WHO_AM_I
        if self.verify_who_am_i:
            who_am_i = self.read_reg(0, WHO_AM_I)
            if who_am_i != ICM20948_WHO_AM_I_VALUE:
                raise UnexpectedWhoAmIError(
                    f"Expected WHO_AM_I 0x{ICM20948_WHO_AM_I_VALUE:02X}, "
                    f"got 0x{who_am_i:02X}"
                )
            logger.info(f"ICM-20948 WHO_AM_I verified: 0x{who_am_i:02X}")

        # Enable accel and gyro
        self.write_reg(0, PWR_MGMT_2, 0x00)  # Enable all axes

        # Configure accelerometer (bank 2)
        # ±2g range, default sample rate
        self.write_reg(2, ACCEL_CONFIG, 0x00)  # ±2g, 1.125 kHz ODR

        # Configure gyroscope (bank 2)
        # ±250 dps range, default sample rate
        self.write_reg(2, GYRO_CONFIG_1, 0x00)  # ±250 dps, 1.1 kHz ODR

        logger.info("ICM-20948 initialized: ±2g accel, ±250 dps gyro")

    def read_imu(self) -> ImuSample:
        """
        Read accelerometer and gyroscope data.

        Returns:
            ImuSample with accel (g) and gyro (deg/s) values
        """
        # Read 12 bytes starting from ACCEL_XOUT_H
        data = self.read_bytes(0, ACCEL_XOUT_H, 12)

        # Parse as signed 16-bit big-endian
        ax_raw, ay_raw, az_raw, gx_raw, gy_raw, gz_raw = struct.unpack(">hhhhhh", data)

        # Convert to physical units
        ax_g = ax_raw / ACCEL_SCALE_2G
        ay_g = ay_raw / ACCEL_SCALE_2G
        az_g = az_raw / ACCEL_SCALE_2G

        gx_dps = gx_raw / GYRO_SCALE_250DPS
        gy_dps = gy_raw / GYRO_SCALE_250DPS
        gz_dps = gz_raw / GYRO_SCALE_250DPS

        return ImuSample(
            ax_g=ax_g,
            ay_g=ay_g,
            az_g=az_g,
            gx_dps=gx_dps,
            gy_dps=gy_dps,
            gz_dps=gz_dps,
            timestamp_ns=monotonic_ns(),
        )

    def set_bank(self, bank: int) -> None:
        """
        Switch to specified register bank.

        Thread-safe with 2ms delay for stability.

        Args:
            bank: Bank number (0-3)
        """
        with self._lock:
            if self._current_bank == bank:
                return  # Already in correct bank

            # Write bank select register
            tx = [REG_BANK_SEL & 0x7F, (bank << 4) & 0xFF]
            self._spi.xfer2(tx)
            self._current_bank = bank
            time.sleep(0.002)  # 2ms delay for bank switch

            logger.debug(f"Switched to bank {bank}")

    def read_reg(self, bank: int, reg: int) -> int:
        """
        Read single register from specified bank.

        Args:
            bank: Register bank (0-3)
            reg: Register address

        Returns:
            Register value (0-255)
        """
        with self._lock:
            self.set_bank(bank)
            tx = [reg | 0x80, 0x00]  # Set MSB for read
            rx = self._spi.xfer2(tx)
            return rx[1]

    def write_reg(self, bank: int, reg: int, value: int) -> None:
        """
        Write single register in specified bank.

        Args:
            bank: Register bank (0-3)
            reg: Register address
            value: Value to write (0-255)
        """
        with self._lock:
            self.set_bank(bank)
            tx = [reg & 0x7F, value & 0xFF]  # Clear MSB for write
            self._spi.xfer2(tx)

    def read_bytes(self, bank: int, start_reg: int, count: int) -> bytes:
        """
        Read multiple consecutive registers.

        Args:
            bank: Register bank (0-3)
            start_reg: Starting register address
            count: Number of bytes to read

        Returns:
            Bytes read from registers
        """
        with self._lock:
            self.set_bank(bank)
            tx = [start_reg | 0x80] + [0x00] * count  # Set MSB for read
            rx = self._spi.xfer2(tx)
            return bytes(rx[1:])  # Skip first dummy byte

