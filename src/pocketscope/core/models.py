from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

AdsbSrc = Literal["SBS", "BEAST", "JSON", "PLAYBACK"]


class AdsbMessage(BaseModel):
    """
    ADS-B message normalized for the domain layer.
    Missing numeric fields are None until observed.
    """

    ts: datetime = Field(..., description="Event timestamp (UTC)")
    icao24: str = Field(..., description="Hex ICAO24 (6 hex chars, lowercase)")
    callsign: Optional[str] = Field(None, description="Flight callsign, if known")

    lat: Optional[float] = None
    lon: Optional[float] = None

    baro_alt: Optional[float] = Field(
        None, description="Barometric altitude in feet, if provided"
    )
    geo_alt: Optional[float] = Field(
        None, description="Geometric altitude in feet, if provided"
    )
    ground_speed: Optional[float] = Field(
        None, description="Ground speed (knots) if provided"
    )
    track_deg: Optional[float] = Field(
        None, description="Course over ground in degrees true"
    )
    vertical_rate: Optional[float] = Field(
        None, description="Vertical rate (ft/min) if provided"
    )
    squawk: Optional[str] = None
    nic: Optional[int] = None
    nacp: Optional[int] = None

    src: AdsbSrc = "JSON"

    @field_validator("icao24")
    @classmethod
    def _normalize_icao24(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) != 6 or any(ch not in "0123456789abcdef" for ch in v):
            raise ValueError("icao24 must be 6 hex characters")
        return v

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    def __repr__(self) -> str:  # pragma: no cover
        return f"AdsbMessage({self.icao24} @ {self.ts.isoformat(timespec='seconds')})"


class GpsFix(BaseModel):
    """GNSS position/time fix."""

    ts: datetime
    lat: float
    lon: float
    alt_m: Optional[float] = None
    speed_mps: Optional[float] = None
    track_deg: Optional[float] = None
    hdop: Optional[float] = None

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


class ImuSample(BaseModel):
    """IMU sample with accel/gyro/mag in sensor frame."""

    ts: datetime

    # Accelerometer (m/s^2)
    ax: float
    ay: float
    az: float

    # Gyroscope (rad/s)
    gx: float
    gy: float
    gz: float

    # Magnetometer (uT)
    mx: float
    my: float
    mz: float

    temp_c: Optional[float] = None

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


# History point: (ts, lat, lon, alt_ft | None)
HistoryPoint = Tuple[datetime, float, float, Optional[float]]


class AircraftTrack(BaseModel):
    """
    Aggregated, time-evolving state per ICAO24.
    history holds recent points (ts, lat, lon, alt_ft).
    """

    icao24: str
    callsign: Optional[str] = None
    last_ts: datetime
    history: List[HistoryPoint] = Field(default_factory=list)
    state: Dict[str, object] = Field(
        default_factory=dict,
        description="Arbitrary derived state (heading, gs, vs, flags)",
    )

    @field_validator("icao24")
    @classmethod
    def _normalize_icao24(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) != 6 or any(ch not in "0123456789abcdef" for ch in v):
            raise ValueError("icao24 must be 6 hex characters")
        return v

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    def add_point(self, p: HistoryPoint) -> None:
        self.history.append(p)
        self.last_ts = p[0]


__all__ = [
    "AdsbSrc",
    "AdsbMessage",
    "GpsFix",
    "ImuSample",
    "HistoryPoint",
    "AircraftTrack",
]
