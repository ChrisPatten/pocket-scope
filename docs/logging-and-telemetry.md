# Logging & Telemetry

PocketScope provides structured logging plus a lightweight in‑process telemetry
registry for counters, gauges, and histograms. This page summarises how to
configure, extend, and consume those facilities.

## Goals

- Human + machine friendly log output (console rich formatting or JSON)
- Consistent contextual fields (session, request, focus aircraft, etc.)
- Low‑overhead metrics for UI/render and ingestion hot paths
- Deterministic + testable (telemetry registry is pure in‑process state)

## Initialisation

`init_logging_and_telemetry()` loads settings (optionally from an explicit
path), wires handlers, then configures the telemetry registry.

```python
from pocketscope.logging import init_logging_and_telemetry

settings = init_logging_and_telemetry()  # side effects: logging + metrics ready
```

## Context Fields

Use a context scope to inject per‑task metadata. These keys appear on every log
record emitted inside the scope and can be filtered/queried downstream.

```python
import logging
from pocketscope.logging import context_scope, new_request_id

logger = logging.getLogger("pocketscope.demo")

with context_scope(request_id=new_request_id(), focus_icao="ABC123"):
    logger.info("Focus set")
```

## Instrumentation Helpers

Decorators in `instrumentation.py` simplify common patterns:

- `@log_call` – emit start/stop + duration with optional argument redaction
- `@measure_latency("metric_name")` – record function runtime histogram
- `@count_exceptions("metric_name")` – increment counter on raised exception
- `span(name="...")` – manual context manager for ad‑hoc timing/log grouping

## Metrics API

Access the singleton registry via `get_registry()` and declare metrics once.
Subsequent calls with the same name/type return the existing instance.

```python
from pocketscope.logging import get_registry

registry = get_registry()
frames = registry.counter("ui_frames_total", "Rendered UI frames")
frames.inc()

lat_hist = registry.histogram(
    "ui_frame_seconds", buckets=(0.01, 0.02, 0.05, 0.1)
)
lat_hist.observe(frame_time_s)
```

### Sampling High‑Rate Diagnostics

Use `Sampler` to gate debug emissions inside hot loops:

```python
from pocketscope.logging.telemetry import Sampler

sampler = Sampler(rate_per_sec=2.0)
if sampler.allow():
    logger.debug("loop stats", extra={"fps": fps})
```

## Journald Handler (Linux)

If `journald` logging is enabled in settings and the `systemd` Python bindings
are importable, a `JournalHandler` attaches automatically. Test environments
that provide a stripped stub lacking an `emit` implementation are defensively
patched with a no‑op to avoid spurious failures.

## Configuration Reference

See `examples/settings.example.yml` for a full reference including:

- Global log level + per‑handler overrides
- Human vs JSON style selection
- Context field inclusion / exclusion
- Telemetry enable flag and optional thresholds

## Testing & Determinism

Unit tests assert that handlers configure without external dependencies. The
telemetry registry is reset per‑process start; tests may obtain a clean state
by spawning fresh processes or, for fine‑grained cases, calling internal reset
helpers (not part of public API) guarded behind test fixtures.

## Future Enhancements (Not Implemented Yet)

- Export adapters (e.g. Prometheus scrape endpoint)
- Persistent ring buffer for recent structured events (debug UI pane)
- Adaptive sampling based on dynamic load heuristics
