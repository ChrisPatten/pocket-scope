"""GPS data models."""

from dataclasses import dataclass


@dataclass
class GpsFix:
    """GPS position fix with timestamp."""

    time_utc_hms: str | None  # "HH:MM:SS"
    lat_deg: float | None
    lon_deg: float | None
    fix_type: str  # e.g. "RMC:A", "RMC:V", "GGA:1"
    age_s: float
    raw_last: str | None
    timestamp_ns: int


@dataclass
class PpsTick:
    """PPS pulse event with monotonic timestamp."""

    tick_ns: int
    tick_count: int

