"""Settings schema for PocketScope logging and telemetry."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, cast

from pydantic import BaseModel, Field, HttpUrl, model_validator

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
    sampling: SamplingConfig = Field(
        default_factory=cast(Callable[[], SamplingConfig], SamplingConfig)
    )
    handlers: Dict[str, HandlerConfig] = Field(default_factory=dict)
    loggers: Dict[str, LogLevel] = Field(default_factory=dict)


class PrometheusExporterSettings(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(9100, ge=0, le=65535)


class OtlpExporterSettings(BaseModel):
    enabled: bool = False
    endpoint: HttpUrl | str = "http://127.0.0.1:4317"


class ExportersSettings(BaseModel):
    prometheus: PrometheusExporterSettings = Field(
        default_factory=cast(
            Callable[[], PrometheusExporterSettings], PrometheusExporterSettings
        )
    )
    otlp: OtlpExporterSettings = Field(
        default_factory=cast(Callable[[], OtlpExporterSettings], OtlpExporterSettings)
    )


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


class Settings(BaseModel):
    logging: LoggingSettings = Field(
        default_factory=cast(Callable[[], LoggingSettings], LoggingSettings)
    )
    telemetry: TelemetrySettings = Field(
        default_factory=cast(Callable[[], TelemetrySettings], TelemetrySettings)
    )

    @model_validator(mode="after")
    def normalize_levels(self) -> "Settings":
        self.logging.level = self.logging.level.upper()  # type: ignore[assignment]
        normalized: Dict[str, LogLevel] = {}
        for name, level in self.logging.loggers.items():
            normalized[name] = level.upper()  # type: ignore[assignment]
        self.logging.loggers = normalized
        return self


__all__ = ["Settings", "LoggingSettings", "TelemetrySettings"]
