"""PocketScope telemetry registry and exporters."""

from __future__ import annotations

import http.server
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, cast

from pocketscope.settings_schema import TelemetrySettings

logger = logging.getLogger(__name__)


class Metric:
    def __init__(
        self, name: str, description: str = "", unit: str | None = None
    ) -> None:
        self.name = name
        self.description = description
        self.unit = unit
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, object]:  # pragma: no cover - overridden
        raise NotImplementedError


class Counter(Metric):
    def __init__(
        self, name: str, description: str = "", unit: str | None = None
    ) -> None:
        super().__init__(name, description, unit)
        self._value = 0.0

    def inc(self, amount: float = 1.0) -> None:
        if amount <= 0:
            return
        with self._lock:
            self._value += amount

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            value = self._value
        return {"type": "counter", "value": value}


class Gauge(Metric):
    def __init__(
        self, name: str, description: str = "", unit: str | None = None
    ) -> None:
        super().__init__(name, description, unit)
        self._value = 0.0

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def inc(self, value: float = 1.0) -> None:
        with self._lock:
            self._value += value

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            value = self._value
        return {"type": "gauge", "value": value}


class Histogram(Metric):
    def __init__(
        self,
        name: str,
        *,
        buckets: Iterable[float] | None = None,
        description: str = "",
        unit: str | None = None,
    ) -> None:
        super().__init__(name, description, unit)
        bucket_list = sorted(
            set(buckets or [0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0])
        )
        self._buckets = bucket_list
        self._counts = {boundary: 0 for boundary in bucket_list}
        self._inf = 0

    def observe(self, value: float) -> None:
        with self._lock:
            placed = False
            for boundary in self._buckets:
                if value <= boundary:
                    self._counts[boundary] += 1
                    placed = True
                    break
            if not placed:
                self._inf += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            buckets = dict(self._counts)
            inf = self._inf
        return {"type": "histogram", "buckets": buckets, "inf": inf}


@dataclass(slots=True)
class Sampler:
    """Token bucket sampler for high-rate events."""

    rate_per_sec: float
    capacity: float | None = None
    tokens: float = 1.0
    last: float = 0.0

    def allow(self) -> bool:
        now = time.monotonic()
        if self.last == 0.0:
            self.last = now
        elapsed = now - self.last
        self.last = now
        cap = self.capacity or self.rate_per_sec
        self.tokens = min(cap, self.tokens + elapsed * self.rate_per_sec)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class TelemetryRegistry:
    def __init__(self) -> None:
        self._metrics: Dict[str, Metric] = {}
        self._lock = threading.Lock()
        self._settings: TelemetrySettings | None = None
        self._prometheus_server: ThreadingServer | None = None
        self._sampler_thread: _SystemSampler | None = None

    def configure(self, settings: TelemetrySettings) -> None:
        self._settings = settings
        if settings.exporters.prometheus.enabled:
            self._start_prometheus(settings)
        else:
            self._stop_prometheus()
        if settings.exporters.otlp.enabled:
            logger.info(
                "OTLP exporter requested but not implemented; stub active for %s",
                settings.exporters.otlp.endpoint,
            )
        if settings.enabled:
            self._start_sampler(settings)
        else:
            self.stop()

    def counter(
        self, name: str, description: str = "", unit: str | None = None
    ) -> Counter:
        with self._lock:
            metric = self._metrics.get(name)
            if isinstance(metric, Counter):
                return metric
            counter = Counter(name, description, unit)
            self._metrics[name] = counter
            return counter

    def gauge(self, name: str, description: str = "", unit: str | None = None) -> Gauge:
        with self._lock:
            metric = self._metrics.get(name)
            if isinstance(metric, Gauge):
                return metric
            gauge = Gauge(name, description, unit)
            self._metrics[name] = gauge
            return gauge

    def histogram(
        self,
        name: str,
        *,
        buckets: Iterable[float] | None = None,
        description: str = "",
        unit: str | None = None,
    ) -> Histogram:
        with self._lock:
            metric = self._metrics.get(name)
            if isinstance(metric, Histogram):
                return metric
            hist = Histogram(name, buckets=buckets, description=description, unit=unit)
            self._metrics[name] = hist
            return hist

    def snapshot(self) -> Dict[str, dict[str, object]]:
        with self._lock:
            metrics = dict(self._metrics)
        return {name: metric.snapshot() for name, metric in metrics.items()}

    def _start_prometheus(self, settings: TelemetrySettings) -> None:
        if self._prometheus_server and self._prometheus_server.running:
            return
        try:
            self._prometheus_server = ThreadingServer(
                host=settings.exporters.prometheus.host,
                port=settings.exporters.prometheus.port,
                registry=self,
            )
            self._prometheus_server.start()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to start Prometheus exporter: %s", exc)

    def _stop_prometheus(self) -> None:
        if self._prometheus_server:
            self._prometheus_server.stop()
            self._prometheus_server = None

    def _start_sampler(self, settings: TelemetrySettings) -> None:
        if self._sampler_thread and self._sampler_thread.is_alive():
            self._sampler_thread.update_interval(settings.sampler_interval_sec)
            return
        self._sampler_thread = _SystemSampler(self, settings.sampler_interval_sec)
        self._sampler_thread.start()

    def stop(self) -> None:
        self._stop_prometheus()
        if self._sampler_thread:
            self._sampler_thread.stop()
            self._sampler_thread = None


class _SystemSampler(threading.Thread):
    def __init__(self, registry: TelemetryRegistry, interval: float) -> None:
        super().__init__(name="telemetry-sampler", daemon=True)
        self._registry = registry
        self._interval = interval
        self._stop_event = threading.Event()

    def update_interval(self, interval: float) -> None:
        self._interval = interval

    def stop(self) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=1.0)

    def run(self) -> None:  # pragma: no cover - timing dependent
        cpu_temp = self._registry.gauge("system_cpu_temp_c")
        mem_used = self._registry.gauge("system_memory_used_pct")
        disk_used = self._registry.gauge("system_disk_used_pct")
        while not self._stop_event.is_set():
            cpu_temp.set(_read_cpu_temp())
            mem_used.set(_read_memory_usage())
            disk_used.set(_read_disk_usage())
            self._stop_event.wait(self._interval)


class _MetricsHandler(http.server.BaseHTTPRequestHandler):
    registry: TelemetryRegistry

    def do_GET(self) -> None:  # pragma: no cover - simple passthrough
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        snapshot = self.registry.snapshot()
        body = _prometheus_from_snapshot(snapshot)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(
        self, format: str, *args: object
    ) -> None:  # pragma: no cover - silence
        logger.debug("Prometheus exporter: %s", format % args)


class ThreadingServer:
    def __init__(self, host: str, port: int, registry: TelemetryRegistry) -> None:
        self.host = host
        self.port = port
        self.registry = registry
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.running = False

    def start(self) -> None:
        handler = type(
            "RegistryHandler",
            (_MetricsHandler,),
            {"registry": self.registry},
        )
        self._server = http.server.ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="telemetry-prometheus",
            daemon=True,
        )
        self._thread.start()
        self.running = True
        logger.info("Prometheus exporter listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        if not self._server:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=1.0)
        self.running = False
        self._server = None
        self._thread = None


def _read_cpu_temp() -> float:
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        raw = path.read_text().strip()
        return float(raw) / 1000.0
    except Exception:
        return 0.0


def _read_memory_usage() -> float:
    try:
        meminfo = Path("/proc/meminfo").read_text().splitlines()
        values = {}
        for line in meminfo:
            parts = line.split(":")
            if len(parts) == 2:
                key, value = parts
                values[key.strip()] = float(value.strip().split()[0])
        total = values.get("MemTotal")
        free = values.get("MemAvailable")
        if total and free:
            used = max(total - free, 0)
            return used / total * 100.0
    except Exception:
        pass
    return 0.0


def _read_disk_usage() -> float:
    try:
        stat = os.statvfs("/")
        total = stat.f_frsize * stat.f_blocks
        available = stat.f_frsize * stat.f_bavail
        used = total - available
        if total > 0:
            return used / total * 100.0
    except Exception:
        pass
    return 0.0


def _prometheus_from_snapshot(snapshot: Dict[str, dict[str, object]]) -> str:
    lines: list[str] = []
    for name, data in snapshot.items():
        if data["type"] == "counter":
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {data['value']}")
        elif data["type"] == "gauge":
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {data['value']}")
        elif data["type"] == "histogram":
            lines.append(f"# TYPE {name} histogram")
            counts = cast(dict[float, int], data["buckets"])
            cumulative = 0
            for boundary in sorted(counts):
                cumulative += int(counts[boundary])
                lines.append(f'{name}_bucket{{le="{boundary}"}} {cumulative}')
            inf_total = cast(int, data["inf"])
            lines.append(f'{name}_bucket{{le="+Inf"}} {cumulative + inf_total}')
        lines.append("")
    return "\n".join(lines)


_registry: TelemetryRegistry | None = None


def get_registry() -> TelemetryRegistry:
    global _registry
    if _registry is None:
        _registry = TelemetryRegistry()
    return _registry


def configure_telemetry(settings: TelemetrySettings) -> None:
    registry = get_registry()
    registry.configure(settings)


def disable_telemetry() -> None:
    global _registry
    if _registry is not None:
        _registry.stop()


__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "Sampler",
    "TelemetryRegistry",
    "configure_telemetry",
    "disable_telemetry",
    "get_registry",
]
