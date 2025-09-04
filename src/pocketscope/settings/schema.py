"""Pydantic model for user settings."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


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

    units: str = Field(default="nm_ft_kt")
    range_nm: float = Field(default=10.0)
    track_length_mode: str = Field(default="medium")
    demo_mode: bool = Field(default=False)
    # Altitude filter band. One of:
    #   "All" (no filtering)
    #   "0–5k" (0 ft  ≤ alt < 5000 ft)
    #   "5–10k" (5000 ft ≤ alt < 10000 ft)
    #   "10–20k" (10000 ft ≤ alt < 20000 ft)
    #   ">20k" (alt ≥ 20000 ft)
    altitude_filter: str = Field(default="All")
    # When true the PPI orientation is locked north-up (rotation_deg forced to 0).
    # When false the user may rotate the view with left/right arrow keys.
    north_up_lock: bool = Field(default=True)

    @field_validator("units")
    @classmethod
    def _chk_units(cls, v: str) -> str:  # pragma: no cover - trivial
        if v not in {"nm_ft_kt", "mi_ft_mph", "km_m_kmh"}:
            raise ValueError("invalid units")
        return v

    @field_validator("track_length_mode")
    @classmethod
    def _chk_tlm(cls, v: str) -> str:  # pragma: no cover - trivial
        if v not in {"short", "medium", "long"}:
            raise ValueError("invalid track length mode")
        return v

    @field_validator("altitude_filter")
    @classmethod
    def _chk_alt_filter(cls, v: str) -> str:  # pragma: no cover - trivial
        allowed = {"All", "0–5k", "5–10k", "10–20k", ">20k"}
        if v not in allowed:
            raise ValueError("invalid altitude filter")
        return v
