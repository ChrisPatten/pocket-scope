# PocketScope Architecture Overview

PocketScope is an event-driven application for decoding, processing, and visualising ADS-B traffic on Raspberry Pi–class hardware. Rather than duplicate every subsystem detail here, this overview orients you to the major building blocks and points you toward deeper guides for each component.

## High-Level Design Principles

- **Event-Oriented Pipelines** – Decoupled producers and consumers communicate exclusively through asynchronous topics on the internal event bus.
- **Deterministic Simulation Support** – Every time-dependent service can run against a simulated clock for reproducible testing and automation.
- **Composable Rendering Stack** – Drawing primitives, layers, and input handlers are kept modular so new displays or overlays can be added with minimal coupling.
- **Persistent, Observable Configuration** – User settings and theming changes are persisted to disk and broadcast back into the running UI without requiring a restart.

## Documentation Map

| Area | Summary |
| --- | --- |
| [Event Bus](event-bus.md) | Messaging infrastructure, envelopes, and subscription patterns. |
| [Time & Simulation](time-and-simulation.md) | Real versus simulated clocks, scheduling semantics, and testing tips. |
| [Data Ingestion](data-ingestion.md) | ADS-B playback, live dump1090 polling, GPS/IMU ingest, and data source hygiene. |
| [Track Management](track-service.md) | Aircraft state, history buffers, expiry, and integration with render layers. |
| [Rendering & Display](rendering.md) | Canvas abstraction, PPI view, input handling, and backend implementations. |
| [Settings & Soft Keys](settings-and-softkeys.md) | Configuration files, validation, hot reload, and soft key bar behaviour. |
| [Recording & Replay](recording-and-replay.md) | JSONL capture utilities, deterministic replays, and workflow integration. |

Additional domain-specific resources can be found in:

- [ADS-B data flow](adsb-data-flow.md)
- [Spatial utilities and GeoJSON ingestion](spatial.md)
- [Theme system reference](theming.md)
- [Screenshot automation](screenshots.md)
- [Systemd setup guide](systemd-setup.md)

Each guide focuses on concepts, typical usage, and extension points so you can confidently adapt PocketScope to new sensors, overlays, or deployment environments.
