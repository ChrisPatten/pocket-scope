"""
ps_sensors: Standardized GPS, IMU, and magnetometer access for Raspberry Pi.

Provides reusable interfaces to:
- GPS (UART NMEA + optional PPS)
- ICM-20948 IMU (accel/gyro over SPI)
- AK09916 magnetometer (via ICM I2C master)
"""

__version__ = "0.1.0"

from .errors import (
    DeviceNotFoundError,
    GpsPortError,
    MagnetometerIdError,
    PpsNotAvailableError,
    UnexpectedWhoAmIError,
)

__all__ = [
    "DeviceNotFoundError",
    "GpsPortError",
    "MagnetometerIdError",
    "PpsNotAvailableError",
    "UnexpectedWhoAmIError",
]

