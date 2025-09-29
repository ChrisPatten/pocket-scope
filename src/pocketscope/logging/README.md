# PocketScope Logging & Telemetry

This package provides structured logging, contextual tracing helpers, and a lightweight telemetry registry for PocketScope. Key pieces:

- `config.py` loads `settings.yml`, applies environment overrides (via `POCKETSCOPE_*` variables), and wires `logging.config.dictConfig` handlers/filters.
- `context.py` exposes correlation ID helpers. Use `context_scope(session_id=...)` when spawning new tasks. `new_request_id()` is useful for input handlers.
- `instrumentation.py` contains decorators for common patterns: `@log_call`, `@measure_latency`, `@count_exceptions`, and the `span()` context manager.
- `telemetry.py` manages in-process Counters, Gauges, and Histograms with optional exporters. Call `get_registry()` to access the singleton and register new metrics.

## Adding a metric

```python
from pocketscope.logging import get_registry

registry = get_registry()
frames = registry.counter("ui_frames_total", "Rendered frames")
frames.inc()
```

Histograms require bucket selection:

```python
render_hist = registry.histogram("ui_frame_seconds", buckets=(0.01, 0.02, 0.05))
render_hist.observe(duration_s)
```

## Context fields

`settings.yml` defines which contextual fields are injected into every log line. Populate them via:

```python
from pocketscope.logging import context_scope, new_request_id

with context_scope(request_id=new_request_id(), focus_icao=icao):
    logger.info("Pin toggled")
```

## Sampling helpers

For high-rate diagnostics, create a `Sampler` and gate emissions:

```python
from pocketscope.logging.telemetry import Sampler

sampler = Sampler(rate_per_sec=2.0)
if sampler.allow():
    logger.debug("hot loop stats", extra={"fps": fps})
```

See `src/pocketscope/settings/settings.yml` for the default configuration and examples.
