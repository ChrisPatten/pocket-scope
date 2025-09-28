from __future__ import annotations

import json
import logging
import socket
import sys
import types
from pathlib import Path
from typing import Iterator
from urllib import request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from pocketscope.logging.config import init_logging_and_telemetry
from pocketscope.logging.filters import (
    DuplicateFilter,
    RateLimitFilter,
    RedactionFilter,
)
from pocketscope.logging.instrumentation import measure_latency
from pocketscope.logging.rotation import build_rotating_file_handler
from pocketscope.logging.telemetry import (
    Sampler,
    configure_telemetry,
    disable_telemetry,
    get_registry,
)
from pocketscope.settings_schema import HandlerConfig, RotateSettings, Settings


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    logging.shutdown()
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(level=logging.NOTSET)
    yield
    logging.shutdown()
    disable_telemetry()


def _write_settings(path: Path, *, telemetry_enabled: bool = False) -> None:
    data = {
        "logging": {
            "level": "INFO",
            "style": "json",
            "utc": True,
            "propagate": False,
            "context_fields": ["session_id", "request_id"],
            "redactions": [],
            "sampling": {"debug_qps": 20, "duplicate_suppression_window_sec": 5},
            "handlers": {
                "console": {"enabled": True, "level": "DEBUG"},
            },
            "loggers": {"pocketscope": "INFO"},
        },
        "telemetry": {
            "enabled": telemetry_enabled,
            "exporters": {
                "prometheus": {"enabled": False, "host": "127.0.0.1", "port": 0},
                "otlp": {"enabled": False, "endpoint": "http://127.0.0.1:4317"},
            },
            "fps_target": 5,
            "thresholds": {
                "gps_stale_sec": 300,
                "decoder_offline_warn_sec": 3,
                "temp_warn_c": 75,
                "temp_crit_c": 85,
            },
            "sampler_interval_sec": 1.0,
        },
    }
    path.write_text(json.dumps(data))


def test_env_override_updates_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path)
    monkeypatch.setenv("POCKETSCOPE_LOGGING_LEVEL", "DEBUG")
    monkeypatch.setenv("POCKETSCOPE_TELEMETRY_ENABLED", "false")
    init_logging_and_telemetry(settings_path)
    assert logging.getLogger().level == logging.DEBUG


def test_journald_handler_attaches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path)
    base = json.loads(settings_path.read_text())
    base["logging"]["handlers"]["journald"] = {"enabled": True, "level": "INFO"}
    settings_path.write_text(json.dumps(base))

    fake_module = types.ModuleType("systemd")

    class FakeJournalHandler(logging.Handler):
        pass

    journal_module = types.ModuleType("systemd.journal")
    journal_module.JournalHandler = FakeJournalHandler  # type: ignore[attr-defined]
    sys.modules["systemd"] = fake_module
    sys.modules["systemd"].journal = journal_module  # type: ignore[attr-defined]
    sys.modules["systemd.journal"] = journal_module
    monkeypatch.setattr(
        "importlib.util.find_spec", lambda name: types.SimpleNamespace()
    )
    try:
        init_logging_and_telemetry(settings_path)
        handler_types = {type(h) for h in logging.getLogger().handlers}
        assert FakeJournalHandler in handler_types
    finally:
        sys.modules.pop("systemd", None)
        sys.modules.pop("systemd.journal", None)


def test_rotating_file_helper(tmp_path: Path) -> None:
    config = HandlerConfig(
        enabled=True,
        level="DEBUG",
        path=tmp_path / "log.txt",
        rotate=RotateSettings(max_bytes=1024, backup_count=2),
    )
    handler_dict = build_rotating_file_handler(config)
    assert handler_dict["maxBytes"] == 1024
    assert handler_dict["backupCount"] == 2


def test_filters_behaviour() -> None:
    rate = RateLimitFilter(debug_qps=1)
    record = logging.LogRecord("test", logging.DEBUG, __file__, 1, "msg", (), None)
    assert rate.filter(record)
    assert not rate.filter(record)

    dup = DuplicateFilter(window_seconds=1.0)
    record = logging.LogRecord("test", logging.WARNING, __file__, 1, "warn", (), None)
    assert dup.filter(record)
    assert not dup.filter(record)

    redact = RedactionFilter(rules=[{"pattern": "secret", "replacement": "***"}])
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "secret value", (), None
    )
    redact.filter(record)
    assert "***" in record.msg


def test_measure_latency_updates_histogram(caplog: pytest.LogCaptureFixture) -> None:
    registry = get_registry()
    registry.histogram("test_latency")

    @measure_latency(name="test_latency")
    def do_work() -> None:
        pass

    caplog.set_level(logging.DEBUG)
    do_work()
    assert any("latency" in message for message in caplog.messages)
    snapshot = registry.snapshot()["test_latency"]
    total = sum(snapshot["buckets"].values()) + snapshot["inf"]
    assert total == 1


def test_prometheus_exporter() -> None:
    registry = get_registry()
    counter = registry.counter("prom_test_total")
    counter.inc(2)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = Settings.model_validate(
        {
            "logging": {
                "handlers": {"console": {"enabled": False}},
            },
            "telemetry": {
                "enabled": True,
                "exporters": {
                    "prometheus": {"enabled": True, "host": "127.0.0.1", "port": port},
                    "otlp": {"enabled": False, "endpoint": "http://127.0.0.1:4317"},
                },
                "fps_target": 5,
                "thresholds": {
                    "gps_stale_sec": 300,
                    "decoder_offline_warn_sec": 3,
                    "temp_warn_c": 75,
                    "temp_crit_c": 85,
                },
                "sampler_interval_sec": 0.2,
            },
        }
    )
    configure_telemetry(settings.telemetry)

    resp = request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2)
    body = resp.read().decode()
    assert "prom_test_total" in body
    disable_telemetry()


def test_sampler_gate() -> None:
    sampler = Sampler(rate_per_sec=10)
    assert sampler.allow()
    assert not sampler.allow()
