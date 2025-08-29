# Copilot Repository Instructions — PocketScope


## Project overview
PocketScope is a handheld, Pi‑powered “ATC‑style” scope that decodes **1090 MHz ADS‑B** and renders a **north‑up polar (PPI)** display on a small TFT. It uses **GPS** for position/time and a **9‑axis IMU** for heading. The stack is **Python‑first** with clean architecture and ports/adapters.

## What to optimize for
- **Fast time‑to‑first‑aircraft**, stable heading, and readable info blocks.
- **Modularity**: keep domain logic independent from hardware/GUI frameworks.
- **Determinism**: everything should be runnable with **mocks** & desktop emulation.
- **Performance on Pi Zero 2 W**: avoid unnecessary allocations; prefer vectorized math.

## Architectural principles
- **Clean Architecture + Ports & Adapters**: domain has no dependency on I/O or UI.
- Events are **immutable, time‑stamped**; domain holds the state.
- **Async first** with `asyncio`; offload heavy decode to worker threads.
- **Dependency Injection** via factories/config; components behind ABC/Protocol.

## Languages & libraries
- **Python 3.11+** preferred.
- Use: `asyncio`, `pydantic` (data models), `numpy` (geometry), `msgpack` (bus serialization), `pytest` (tests).
- Optional backends: **Kivy** or **Pygame/Qt** for display; **pyserial** for GPS; I²C/SPI libs for IMU on device.

## Module boundaries (generate code to these interfaces)
- **Event Bus**: publish/subscribe, in‑process, backpressure aware.
- **Ingestion Sources**:
  - `AdsbSource` (SBS/Beast/JSON/playback)
  - `GpsSource` (NMEA serial/mock)
  - `ImuSource` (ICM‑20948/mock)
- **Domain Services**: `OwnshipService`, `TrackService`, `FocusService`, `Geo` utilities.
- **Rendering**: framework‑agnostic **Canvas API** (primitives + layers: PPI grid, rings, tracks, labels, status bar, soft keys).
- **PAL (Platform Abstraction Layer)**: `DisplayBackend`, `InputBackend`, `Storage`, `Network`.

## Coding style & quality
- Follow **PEP 8**; require **type hints** and `mypy`‑clean stubs.
- Prefer **pure functions** in domain; side effects live in adapters.
- Use **Pydantic v2** models for data contracts; validate at boundaries only.
- Format with **black**; import sort with **isort**; lint with **ruff**.
- Add **docstrings** with concise behavior + assumptions.

## Testing
- Use **pytest**; create **unit tests** for parsers/geo/fusion and **golden‑frame** tests for rendering.
- Provide **mocks/fakes** for ADS‑B, GPS, IMU; add deterministic **playback** fixtures.
- Favor **property‑based tests** (Hypothesis) for geometry/declutter.

## Performance & constraints
- UI ≥ **5 FPS** with 20 aircraft; RF‑to‑screen median ≤ **500 ms**.
- Keep allocations out of per‑frame paths; reuse buffers; use NumPy for vector math.

## Security & privacy
- Operate **offline**; **no outbound network** beyond local dump1090 unless explicitly configured.
- Run as non‑root; avoid embedding secrets; respect license headers.

## When Copilot writes code, prefer to
- Propose **interfaces first** (ABCs/Protocols) before implementations.
- Use **async generators** for streaming sources; expose **`run()`** tasks.
- Emit **configurable** parameters via TOML and **hot‑reload** hooks.
- Provide **docstring examples** and **minimal runnable snippets**.

## When Copilot should avoid
- Blocking I/O in the render loop; long CPU work on the event loop.
- Tying domain code to Kivy/Pygame; leaking device‑specific details into core.
- Adding internet dependencies, heavy map tiles, ML/analytics (v0 is simple PPI).

## File layout (scaffold targets)
```
pocketscope/
  app.py                # DI & wiring
  config/default.toml
  core/{domain,events}/
  ingest/{adsb,gps,imu}/
  platform/{display,input,io}/
  render/{canvas.py,view_ppi.py,layers/}
  ui/{controllers.py,softkeys.py}
  tools/record_replay.py
  tests/{unit,integration,golden_frames}/
```

## Commit & PR guidance for generated changes
- Keep changes **small and focused**; include/update tests and docs.
- Describe interfaces and trade‑offs in PR body; list performance impact.

## Helpful snippets Copilot can reuse
- **Event model skeletons** (Pydantic): `AdsbMessage`, `GpsFix`, `ImuSample`, `AircraftTrack`.
- **Async bus** with topic queues and backpressure.
- **Renderer** with layers and a frame‑tick.

## Example tasks to prioritize
- Implement `Dump1090SbsSource` (TCP 30003) as `AdsbSource`.
- Implement `FilePlaybackSource` and deterministic `TimeSource`.
- Implement `TrackService` with ring‑buffer trails and expiry.
- Implement `PpiView` with range rings, airport markers, decluttered labels.

## Non‑goals (v0)
- UAT 978, MLAT, feeder/cloud features, rich cartography, alerts.

