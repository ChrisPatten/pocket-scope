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
