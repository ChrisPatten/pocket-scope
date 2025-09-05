"""Pydantic model for user settings."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .values import ALTITUDE_FILTER_CYCLE_ORDER, TRACK_LENGTH_CYCLE_ORDER, UNITS_ORDER


class Settings(BaseModel):
    """UI settings persisted to disk.

    Parameters
    ----------
    units: Display units identifier. One of ``nm_ft_kt``, ``mi_ft_mph``
        or ``km_m_kmh``.
    range_nm: PPI range in nautical miles.
    track_length_mode: Trail length preset (``short``, ``medium`` or
        ``long``).
    demo_mode: When true a small ``DEMO`` badge is shown on the overlay.
    """

    units: str = Field(default=UNITS_ORDER[0])
    range_nm: float = Field(default=10.0)
    track_length_mode: str = Field(
        default=TRACK_LENGTH_CYCLE_ORDER[1]
        if len(TRACK_LENGTH_CYCLE_ORDER) > 1
        else TRACK_LENGTH_CYCLE_ORDER[0]
    )
    demo_mode: bool = Field(default=False)
    # Altitude filter band. One of:
    #   "All" (no filtering)
    #   "0–5k" (0 ft  ≤ alt < 5000 ft)
    #   "5–10k" (5000 ft ≤ alt < 10000 ft)
    #   "10–20k" (10000 ft ≤ alt < 20000 ft)
    #   ">20k" (alt ≥ 20000 ft)
    altitude_filter: str = Field(default=ALTITUDE_FILTER_CYCLE_ORDER[0])
    # When true the PPI orientation is locked north-up (rotation_deg forced to 0).
    # When false the user may rotate the view with left/right arrow keys.
    north_up_lock: bool = Field(default=True)

    @field_validator("units")
    @classmethod
    def _chk_units(cls, v: str) -> str:  # pragma: no cover - trivial
        if v not in set(UNITS_ORDER):
            raise ValueError("invalid units: must be one of " + ", ".join(UNITS_ORDER))
        return v

    @field_validator("track_length_mode")
    @classmethod
    def _chk_tlm(cls, v: str) -> str:  # pragma: no cover - trivial
        if v not in set(TRACK_LENGTH_CYCLE_ORDER):
            raise ValueError(
                "invalid track length mode: must be one of "
                + ", ".join(TRACK_LENGTH_CYCLE_ORDER)
            )
        return v

    @field_validator("altitude_filter")
    @classmethod
    def _chk_alt_filter(cls, v: str) -> str:  # pragma: no cover - trivial
        allowed = set(ALTITUDE_FILTER_CYCLE_ORDER)
        if v not in allowed:
            raise ValueError(
                "invalid altitude filter: must be one of "
                + ", ".join(ALTITUDE_FILTER_CYCLE_ORDER)
            )
        return v
