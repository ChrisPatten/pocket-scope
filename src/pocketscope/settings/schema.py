"""Pydantic model for user settings."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from .values import (
    ALTITUDE_FILTER_CYCLE_ORDER,
    PPI_CONFIG,
    TRACK_LENGTH_PRESETS_S,
    TRACK_SERVICE_DEFAULTS,
    UNITS_ORDER,
)


class Settings(BaseModel):
    """UI settings persisted to disk.

    Parameters
    ----------
    units: Display units identifier. One of ``nm_ft_kt``, ``mi_ft_mph``
        or ``km_m_kmh``.
    range_nm: PPI range in nautical miles.
    track_length_s: Trail length in seconds. Cycles among presets (15/45/120)
        in the UI but may be any positive float when edited directly in
        settings.json.
    demo_mode: When true a small ``DEMO`` badge is shown on the overlay.
    """

    units: str = Field(default=UNITS_ORDER[0])
    range_nm: float = Field(default=10.0)
    track_length_s: float = Field(
        default=(
            TRACK_LENGTH_PRESETS_S[1]
            if len(TRACK_LENGTH_PRESETS_S) > 1
            else TRACK_LENGTH_PRESETS_S[0]
        )
    )
    demo_mode: bool = Field(default=False)
    # Altitude filter band. One of:
    #   "All" (no filtering)
    #   "0–5k" (0 ft  ≤ alt < 5000 ft)
    #   "5–10k" (5000 ft ≤ alt < 10000 ft)
    #   "10–20k" (10000 ft ≤ alt < 20000 ft)
    #   ">20k" (alt ≥ 20000 ft)
    altitude_filter: str = Field(default=ALTITUDE_FILTER_CYCLE_ORDER[0])
    # Optional explicit altitude filter bounds (ft). When either value is not None
    # these override the band specified by altitude_filter and allow precise
    # tuning beyond the discrete UI cycle options. Semantics match band logic:
    # inclusive lower bound, exclusive upper bound. A None bound is unbounded.
    altitude_min_ft: float | None = Field(default=None)
    altitude_max_ft: float | None = Field(default=None)
    # When true the PPI orientation is locked north-up (rotation_deg forced to 0).
    # When false the user may rotate the view with left/right arrow keys.
    north_up_lock: bool = Field(default=True)
    # When true the final rendered output will be flipped/rotated to match
    # display hardware that requires the framebuffer orientation to be
    # inverted. This value is persisted to settings.json as ``flip_display``.
    flip_display: bool = Field(default=False)
    # Typography controls for PPI data-blocks (editable + persisted)
    label_font_px: int = Field(
        default=int(PPI_CONFIG.get("typography", {}).get("label_font_px", 12))
    )
    label_line_gap_px: int = Field(
        default=int(PPI_CONFIG.get("typography", {}).get("line_gap_px", 2))
    )
    label_block_pad_px: int = Field(
        default=int(PPI_CONFIG.get("typography", {}).get("block_pad_px", 2))
    )
    # Status overlay font size (separate from PPI label font)
    status_font_px: int = Field(default=12)
    # Optional explicit top/bottom padding for status overlay. When None the
    # overlay computes sensible defaults scaled to the font size.
    status_pad_top_px: int | None = Field(default=None)
    status_pad_bottom_px: int | None = Field(default=None)
    # Softkey bar typography/padding (persisted)
    softkeys_font_px: int = Field(default=12)
    softkeys_pad_x: int = Field(default=4)
    softkeys_pad_y: int = Field(default=2)
    # Sector label visibility
    sector_labels: bool = Field(default=True)
    # Track expiry window (seconds). When >0, tracks older than this are
    # removed by TrackService. Exposed in settings screen as Track Expiry.
    # Defaults to service default (300s) but may be customized.
    track_expiry_s: float = Field(
        default=float(TRACK_SERVICE_DEFAULTS.get("expiry_s", 300.0))
    )

    @field_validator("units")
    @classmethod
    def _chk_units(cls, v: str) -> str:  # pragma: no cover - trivial
        if v not in set(UNITS_ORDER):
            raise ValueError("invalid units: must be one of " + ", ".join(UNITS_ORDER))
        return v

    @field_validator("track_length_s")
    @classmethod
    def _chk_tls(cls, v: float) -> float:  # pragma: no cover - trivial
        try:
            v = float(v)
        except Exception:
            raise ValueError("track_length_s must be numeric") from None
        if v <= 0:
            raise ValueError("track_length_s must be > 0")
        return v

    @field_validator("track_expiry_s")
    @classmethod
    def _chk_te(cls, v: float) -> float:  # pragma: no cover - trivial
        try:
            v = float(v)
        except Exception:
            raise ValueError("track_expiry_s must be numeric") from None
        if v <= 0:
            raise ValueError("track_expiry_s must be > 0 (seconds)")
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

    @field_validator("altitude_min_ft", "altitude_max_ft")
    @classmethod
    def _chk_alt_bounds(
        cls, v: float | None
    ) -> float | None:  # pragma: no cover - trivial
        if v is None:
            return v
        try:
            v = float(v)
        except Exception:
            raise ValueError("altitude bounds must be numeric or null") from None
        if v < 0:
            raise ValueError("altitude bounds must be >= 0 ft")
        return v

    @model_validator(mode="after")
    def _chk_alt_range(self) -> "Settings":  # pragma: no cover - trivial
        if (
            self.altitude_min_ft is not None
            and self.altitude_max_ft is not None
            and self.altitude_min_ft >= self.altitude_max_ft
        ):
            raise ValueError(
                "altitude_min_ft must be < altitude_max_ft when both are set"
            )
        # Migration: if legacy track_length_mode present in input data, map to numeric
        # value using old canonical mapping (short=15, medium=45, long=120) unless
        # user also explicitly set track_length_s.
        legacy = getattr(self, "track_length_mode", None)
        if legacy is not None and not hasattr(self, "_migrated_track_len"):
            mapping = {"short": 15.0, "medium": 45.0, "long": 120.0}
            val = mapping.get(str(legacy), None)
            if val is not None:
                try:
                    if not getattr(self, "track_length_s", None):
                        object.__setattr__(self, "track_length_s", float(val))
                        object.__setattr__(self, "_migrated_track_len", True)
                except Exception:
                    pass
        return self
