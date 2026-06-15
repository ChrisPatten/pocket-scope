"""Pydantic model for user settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, cast

from pydantic import BaseModel, Field, field_validator, model_validator

# Constants for settings defaults
UNITS_ORDER = ("nm_ft_kt", "mi_ft_mph", "km_m_kmh")
TRACK_LENGTH_PRESETS_S = (15.0, 45.0, 120.0)
ALTITUDE_FILTER_CYCLE_ORDER = ("All", "0–5k", "5–10k", "10–20k", ">20k")
PPI_CONFIG = {
    "range_ring_label": {
        "offset_x_px": 4,
        "offset_y_px": -8,
        "char_width_em": 0.6,
        "padding_px": 4,
    },
    "typography": {
        "label_font_px": 12,
        "line_gap_px": 2,
        "block_pad_px": 2,
    },
    "rotation_step_deg": 5.0,
}
TRACK_SERVICE_DEFAULTS = {
    "trail_len_default_s": 60.0,
    "trail_len_pinned_s": 180.0,
    "expiry_s": 300.0,
}

# --- Logging and Telemetry models (migrated from settings_schema.py) ---

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogStyle = Literal["json", "human"]


class RotateSettings(BaseModel):
    max_bytes: int = Field(5 * 1024 * 1024, ge=0)
    backup_count: int = Field(3, ge=0, le=10)


class HandlerConfig(BaseModel):
    enabled: bool = True
    level: Optional[LogLevel] = None
    path: Optional[Path] = None
    rotate: Optional[RotateSettings] = None

    @model_validator(mode="after")
    def validate_path(self) -> "HandlerConfig":
        if self.enabled and self.path is not None:
            try:
                self.path = Path(self.path).expanduser()
            except Exception:
                self.path = Path(self.path)
        return self


class SamplingConfig(BaseModel):
    debug_qps: int = Field(20, ge=0)
    duplicate_suppression_window_sec: int = Field(5, ge=0)


class RedactionRule(BaseModel):
    pattern: str
    replacement: str


class LoggingSettings(BaseModel):
    level: LogLevel = "INFO"
    style: LogStyle = "json"
    utc: bool = True
    propagate: bool = False
    context_fields: List[str] = Field(
        default_factory=lambda: [
            "session_id",
            "request_id",
        ]
    )
    redactions: List[RedactionRule] = Field(default_factory=list)
    sampling: SamplingConfig = Field(default_factory=cast(Callable[[], SamplingConfig], SamplingConfig))
    handlers: Dict[str, HandlerConfig] = Field(default_factory=dict)
    loggers: Dict[str, LogLevel] = Field(default_factory=dict)


class PrometheusExporterSettings(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(9100, ge=0, le=65535)


class OtlpExporterSettings(BaseModel):
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:4317"


class ExportersSettings(BaseModel):
    prometheus: PrometheusExporterSettings = Field(
        default_factory=cast(Callable[[], PrometheusExporterSettings], PrometheusExporterSettings)
    )
    otlp: OtlpExporterSettings = Field(default_factory=cast(Callable[[], OtlpExporterSettings], OtlpExporterSettings))


class TelemetryThresholds(BaseModel):
    gps_stale_sec: int = Field(300, ge=0)
    decoder_offline_warn_sec: int = Field(3, ge=0)
    temp_warn_c: int = Field(75, ge=0)
    temp_crit_c: int = Field(85, ge=0)


class TelemetrySettings(BaseModel):
    enabled: bool = True
    exporters: ExportersSettings = Field(default_factory=ExportersSettings)
    fps_target: int = Field(5, ge=1)
    thresholds: TelemetryThresholds = Field(
        default_factory=cast(Callable[[], TelemetryThresholds], TelemetryThresholds)
    )
    sampler_interval_sec: float = Field(1.0, ge=0.1)


# --- end migrated logging/telemetry models ---

_SIDEBAR_MODES = {"vertical_profile", "hotkey_bar", "none"}
_SIDEBAR_SIDES = {"left", "right"}
_INFO_BLOCK_POLICIES = {"focus_and_closest", "all", "none"}


class _VsHysteresis(BaseModel):
    enter: int = Field(default=220)
    exit: int = Field(default=180)

    @model_validator(mode="after")
    def _check_bounds(self) -> "_VsHysteresis":
        ent = int(self.enter)
        ext = int(self.exit)
        if ent <= 0 or ext < 0:
            raise ValueError("vertical profile hysteresis must be positive")
        if ent <= ext:
            raise ValueError("vertical profile hysteresis enter must exceed exit")
        self.enter = ent
        self.exit = ext
        return self


class VerticalProfileSettings(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _chk_unknown(cls, data: Any) -> Any:  # pragma: no cover - trivial
        """Reject unknown keys for vertical_profile block to surface typos."""
        if isinstance(data, dict):
            allowed = set(cls.model_fields.keys())
            unknown = set(data.keys()) - allowed
            if unknown:
                raise ValueError("Unknown vertical_profile setting(s): " + ", ".join(sorted(unknown)))
        return data

    cycle_interval_sec: int = Field(default=60)
    vs_threshold_fpm: int = Field(default=200)
    vs_hysteresis: _VsHysteresis = Field(default_factory=_VsHysteresis)
    auto_resume_after_manual_ms: int = Field(default=30000)
    show_time_to_crossing: bool = Field(default=True)
    height_fraction: float = Field(default=0.35)

    @field_validator("cycle_interval_sec")
    @classmethod
    def _chk_cycle_interval(cls, v: int) -> int:  # pragma: no cover - trivial
        try:
            val = int(v)
        except Exception:
            raise ValueError("cycle_interval_sec must be an integer") from None
        if val < 30 or val > 120:
            raise ValueError("cycle_interval_sec must be between 30 and 120")
        return val

    @field_validator("vs_threshold_fpm")
    @classmethod
    def _chk_vs_threshold(cls, v: int) -> int:  # pragma: no cover - trivial
        try:
            val = int(v)
        except Exception:
            raise ValueError("vs_threshold_fpm must be an integer") from None
        if val <= 0:
            raise ValueError("vs_threshold_fpm must be > 0")
        return val

    @field_validator("auto_resume_after_manual_ms")
    @classmethod
    def _chk_auto_resume(cls, v: int) -> int:  # pragma: no cover - trivial
        try:
            val = int(v)
        except Exception:
            raise ValueError("auto_resume_after_manual_ms must be an integer") from None
        if val < 0:
            raise ValueError("auto_resume_after_manual_ms must be >= 0")
        return val

    @field_validator("height_fraction")
    @classmethod
    def _chk_height_fraction(cls, v: float) -> float:  # pragma: no cover - trivial
        try:
            val = float(v)
        except Exception:
            raise ValueError("height_fraction must be numeric") from None
        if not 0.1 <= val <= 0.6:
            raise ValueError("height_fraction must be between 0.1 and 0.6")
        return val


class Settings(BaseModel):
    """UI settings persisted to disk.

    Parameters
    ----------
    units: Display units identifier. One of ``nm_ft_kt``, ``mi_ft_mph``
        or ``km_m_kmh``.
    range_nm: PPI range in nautical miles.
    track_length_s: Trail length in seconds. Cycles among presets (15/45/120)
        in the UI but may be any positive float when edited directly in
        settings.yml.
    demo_mode: When true a small ``DEMO`` badge is shown on the overlay.
    """

    units: str = Field(default=UNITS_ORDER[0])
    range_nm: float = Field(default=10.0)
    autoscale_enabled: bool = Field(default=False)
    autoscale_target_visible: int = Field(default=12)
    autoscale_min_range_nm: float | None = Field(default=None)
    autoscale_max_range_nm: float | None = Field(default=None)
    track_length_s: float = Field(
        default=(TRACK_LENGTH_PRESETS_S[1] if len(TRACK_LENGTH_PRESETS_S) > 1 else TRACK_LENGTH_PRESETS_S[0])
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
    # inverted. This value is persisted to settings.yml as ``flip_display``.
    flip_display: bool = Field(default=False)
    # Display backlight brightness percentage (0-100). Persisted to
    # settings.yml as ``backlight_pct`` and applied to hardware when
    # supported.
    backlight_pct: float = Field(default=100.0)
    # Typography controls for PPI data-blocks (editable + persisted)
    label_font_px: int = Field(default=int(PPI_CONFIG.get("typography", {}).get("label_font_px", 12)))
    label_line_gap_px: int = Field(default=int(PPI_CONFIG.get("typography", {}).get("line_gap_px", 2)))
    label_block_pad_px: int = Field(default=int(PPI_CONFIG.get("typography", {}).get("block_pad_px", 2)))
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
    # Target frames-per-second anchor used by the UI and telemetry/logging
    # monitoring. This value is the single source-of-truth persisted to
    # settings.yml and is used throughout the runtime as the target FPS
    # for pacing, decimation and alert thresholds.
    target_fps: float = Field(default=10.0)
    # Track expiry window (seconds). When >0, tracks older than this are
    # removed by TrackService. Exposed in settings screen as Track Expiry.
    # Defaults to service default (300s) but may be customized.
    track_expiry_s: float = Field(default=float(TRACK_SERVICE_DEFAULTS.get("expiry_s", 300.0)))
    # Additional airport identifiers to display beyond 3-letter alpha codes.
    # This allows display of airports with numeric or longer identifiers.
    extra_airports: list[str] = Field(default_factory=list)
    # Primary sidebar UI mode (vertical profile, hotkey bar, none)
    primary_sidebar_mode: str = Field(default="vertical_profile")
    primary_sidebar_side: str = Field(default="right")
    info_blocks_policy: str = Field(default="focus_and_closest")
    vertical_profile: VerticalProfileSettings = Field(default_factory=VerticalProfileSettings)
    # Theme selection + per-key overrides (hex strings). Theme palette
    # lookups are handled by ui.theme.ThemeManager. Backward compatible:
    # missing fields fall back to atc_classic with no overrides.
    theme: str = Field(default="atc_classic")
    themeOverrides: dict[str, str] = Field(default_factory=dict)
    # Web UI image resolution (pixels). Used when running with --web-ui to
    # size the offscreen pygame surface that is captured and served.
    web_ui_width: int = Field(default=1280)
    web_ui_height: int = Field(default=800)
    # Unified logging and telemetry configuration. These were previously
    # defined in a separate settings_schema module; they are embedded here
    # so a single `settings.yml` contains all runtime configuration.
    logging: LoggingSettings = Field(default_factory=cast(Callable[[], LoggingSettings], LoggingSettings))
    telemetry: TelemetrySettings = Field(default_factory=cast(Callable[[], TelemetrySettings], TelemetrySettings))

    @model_validator(mode="before")
    @classmethod
    def _chk_unknown(cls, data: Any) -> Any:  # pragma: no cover - trivial
        """Reject unknown root-level keys except allowed legacy extras.

        We allow the legacy ``track_length_mode`` so that older persisted
        settings files still migrate cleanly; all other unexpected keys
        surface as validation errors so the user can correct typos.
        """
        if isinstance(data, dict):
            # Permit embedding logging + telemetry configuration in the same
            # user settings.yml so deployments can manage a single file.
            # These keys are ignored by this schema (handled by the separate
            # logging/telemetry settings loader) but must not raise an error.
            allowed = set(cls.model_fields.keys()) | {"track_length_mode"}
            unknown = set(data.keys()) - allowed
            if unknown:
                raise ValueError("Unknown settings field(s): " + ", ".join(sorted(unknown)))
        return data

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

    @field_validator("extra_airports")
    @classmethod
    def _chk_extra_airports(cls, v: list[str]) -> list[str]:  # pragma: no cover - trivial
        if not isinstance(v, list):
            raise ValueError("extra_airports must be a list")
        result = []
        for item in v:
            if isinstance(item, str):
                # Normalize to uppercase and strip whitespace
                normalized = str(item).strip().upper()
                if normalized:
                    result.append(normalized)
        return result

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

    @field_validator("autoscale_target_visible")
    @classmethod
    def _chk_autoscale_target(cls, v: int) -> int:  # pragma: no cover - trivial
        try:
            v = int(v)
        except Exception:
            raise ValueError("autoscale_target_visible must be an integer") from None
        if v <= 0:
            raise ValueError("autoscale_target_visible must be >= 1")
        return v

    @field_validator("autoscale_min_range_nm", "autoscale_max_range_nm")
    @classmethod
    def _chk_autoscale_range(cls, v: float | None) -> float | None:
        if v is None:
            return None
        try:
            fv = float(v)
        except Exception:
            raise ValueError("autoscale range limits must be numeric or null") from None
        if fv <= 0:
            raise ValueError("autoscale range limits must be > 0")
        return fv

    @field_validator("altitude_filter")
    @classmethod
    def _chk_alt_filter(cls, v: str) -> str:  # pragma: no cover - trivial
        allowed = set(ALTITUDE_FILTER_CYCLE_ORDER)
        if v not in allowed:
            raise ValueError("invalid altitude filter: must be one of " + ", ".join(ALTITUDE_FILTER_CYCLE_ORDER))
        return v

    @field_validator("web_ui_width", "web_ui_height")
    @classmethod
    def _chk_web_ui_dims(cls, v: int) -> int:  # pragma: no cover - trivial
        try:
            iv = int(v)
        except Exception:
            raise ValueError("web_ui dimensions must be integers") from None
        if iv <= 0 or iv > 8192:
            raise ValueError("web_ui dimensions must be between 1 and 8192")
        return iv

    @field_validator("altitude_min_ft", "altitude_max_ft")
    @classmethod
    def _chk_alt_bounds(cls, v: float | None) -> float | None:  # pragma: no cover - trivial
        if v is None:
            return v
        try:
            v = float(v)
        except Exception:
            raise ValueError("altitude bounds must be numeric or null") from None
        if v < 0:
            raise ValueError("altitude bounds must be >= 0 ft")
        return v

    @field_validator("backlight_pct")
    @classmethod
    def _chk_backlight_pct(cls, v: float) -> float:  # pragma: no cover - trivial
        try:
            v = float(v)
        except Exception:
            raise ValueError("backlight_pct must be numeric (0-100)") from None
        if v < 0 or v > 100:
            raise ValueError("backlight_pct must be between 0 and 100")
        return v

    @field_validator("primary_sidebar_mode")
    @classmethod
    def _chk_sidebar_mode(cls, v: str) -> str:  # pragma: no cover - trivial
        if v not in _SIDEBAR_MODES:
            raise ValueError("invalid primary_sidebar_mode: must be one of " + ", ".join(sorted(_SIDEBAR_MODES)))
        return v

    @field_validator("primary_sidebar_side")
    @classmethod
    def _chk_sidebar_side(cls, v: str) -> str:  # pragma: no cover - trivial
        if v not in _SIDEBAR_SIDES:
            raise ValueError("invalid primary_sidebar_side: must be one of " + ", ".join(sorted(_SIDEBAR_SIDES)))
        return v

    @field_validator("info_blocks_policy")
    @classmethod
    def _chk_info_blocks_policy(cls, v: str) -> str:  # pragma: no cover - trivial
        if v not in _INFO_BLOCK_POLICIES:
            raise ValueError("invalid info_blocks_policy: must be one of " + ", ".join(sorted(_INFO_BLOCK_POLICIES)))
        return v

    @model_validator(mode="after")
    def _chk_alt_range(self) -> "Settings":  # pragma: no cover - trivial
        if (
            self.altitude_min_ft is not None
            and self.altitude_max_ft is not None
            and self.altitude_min_ft >= self.altitude_max_ft
        ):
            raise ValueError("altitude_min_ft must be < altitude_max_ft when both are set")
        if (
            self.autoscale_min_range_nm is not None
            and self.autoscale_max_range_nm is not None
            and self.autoscale_min_range_nm > self.autoscale_max_range_nm
        ):
            raise ValueError("autoscale_min_range_nm must be <= autoscale_max_range_nm when both are set")  # noqa:E501
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


# Backwards-compatible container used by the logging/telemetry loader. The
# project historically validated a minimal schema containing only logging and
# telemetry keys; keep that behaviour available as `LoggingOnlySettings` so the
# logging initializer can continue to call `Settings.model_validate(...)` on a
# compact shape when needed.
class LoggingOnlySettings(BaseModel):
    logging: LoggingSettings = Field(default_factory=cast(Callable[[], LoggingSettings], LoggingSettings))
    telemetry: TelemetrySettings = Field(default_factory=cast(Callable[[], TelemetrySettings], TelemetrySettings))

    @model_validator(mode="after")
    def normalize_levels(self) -> "LoggingOnlySettings":
        self.logging.level = self.logging.level.upper()  # type: ignore[assignment]
        normalized: Dict[str, LogLevel] = {}
        for name, level in self.logging.loggers.items():
            normalized[name] = level.upper()  # type: ignore[assignment]
        self.logging.loggers = normalized
        return self
