"""ICM-20948 and AK09916 data models."""

from dataclasses import dataclass


@dataclass
class ImuSample:
    """IMU accelerometer and gyroscope sample."""

    ax_g: float
    ay_g: float
    az_g: float
    gx_dps: float
    gy_dps: float
    gz_dps: float
    timestamp_ns: int


@dataclass
class MagSample:
    """Magnetometer sample with status flags."""

    mx_uT: float
    my_uT: float
    mz_uT: float
    dor: bool  # Data overrun
    ofl: bool  # Overflow
    dor_total: int
    timestamp_ns: int

