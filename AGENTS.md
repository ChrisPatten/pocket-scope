# PocketScope - GitHub Copilot Instructions

> A handheld, Pi-powered ATC-style radar scope for visualizing nearby aircraft via ADS-B decoding, with north-up polar display, GPS positioning, and IMU heading—entirely offline.

**Version:** 0.3.0 | **Python:** 3.11+ | **Architecture:** Clean Architecture + Event-Driven

---

## 1. Project Overview

### Purpose & Scope
PocketScope decodes 1090 MHz ADS-B signals to display real-time aircraft traffic on a north-up polar (PPI) radar display. Designed for Raspberry Pi hardware with TFT screens, it also runs on desktop for development and testing with full deterministic simulation support.

### Primary Use Cases
- **Embedded deployment**: Raspberry Pi with ILI9341 SPI TFT + XPT2046 touch + RTL-SDR dongle
- **Desktop development**: Pygame window with mouse input and keyboard shortcuts
- **Automated testing**: Deterministic playback with simulated time and golden-frame validation
- **Remote monitoring**: Web UI with WebSocket streaming

### Tech Stack at a Glance
- **Language**: Python 3.11+ (strict type hints, mypy-clean)
- **Core Libraries**: `asyncio`, `pydantic` v2, `numpy`, `msgpack`, `PyYAML`
- **Display**: `pygame` (desktop) or `pygame-ce` (3.13+), `spidev` + `RPi.GPIO` (Pi)
- **Data Processing**: `aiohttp` (dump1090 polling), NumPy (geometry/spatial)
- **Quality Tools**: `pytest`, `black`, `ruff`, `isort`, `mypy`, `hypothesis`

### Design Philosophy
- **Modularity**: Domain logic independent of I/O, GUI, or hardware
- **Determinism**: Everything runnable with mocks and simulated time
- **Performance**: Target ≥5 FPS with 20 aircraft on Pi Zero 2 W
- **Offline-first**: No internet dependencies beyond local dump1090

---

## 2. Architecture

### High-Level Pattern
**Clean Architecture + Ports & Adapters + Event-Driven**

```
┌─────────────────────────────────────────────────────────────┐
│                      Application Layer                      │
│                (CLI, Boot, DI/Wiring)                       │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
┌──────────────────┐        ┌──────────────────┐
│   UI Layer       │        │   Ingest Layer   │
│ (Controllers,    │        │ (ADS-B, GPS,     │
│  Soft Keys,      │◄──────►│  IMU Sources)    │
│  Status)         │  Bus   │                  │
└────────┬─────────┘        └────────┬─────────┘
         │                           │
         └────────────┬──────────────┘
                      ▼
              ┌──────────────┐
              │  Event Bus   │
              │ (Topics, Sub)│
              └──────┬───────┘
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
┌──────────────────┐    ┌──────────────────┐
│  Domain Layer    │    │  Render Layer    │
│ (Tracks, Geo,    │    │ (Canvas, PPI,    │
│  Models, Time)   │    │  Layers, Labels) │
└──────────────────┘    └────────┬─────────┘
                                 │
                         ┌───────┴────────┐
                         ▼                ▼
                 ┌───────────┐    ┌───────────┐
                 │  Platform │    │   Data    │
                 │ (Display, │    │ (Spatial, │
                 │  Input,   │    │  Map DB,  │
                 │  Storage) │    │  Cache)   │
                 └───────────┘    └───────────┘
```

### Core Components

#### Event Bus (`core/events.py`)
- **Purpose**: Async pub/sub messaging with backpressure
- **Features**: Topic-based, bounded queues, msgpack serialization, graceful shutdown
- **Topics**: `"adsb.raw"`, `"tracks.updates"`, `"gps.position"`, `"imu.heading"`, etc.

#### Domain Services (`core/`)
- **TrackService** (`tracks.py`): Aircraft state management, history buffers, expiry
- **GeoUtilities** (`geo.py`): Haversine distance, bearing, coordinate transforms
- **TimeProvider** (`time.py`): Real vs simulated clock, deterministic scheduling
- **Models** (`models.py`): Pydantic data contracts (Aircraft, Position, Track)

#### Ingestion Sources (`ingest/`)
- **ADS-B**: dump1090 JSON polling, SBS TCP, BEAST binary, JSONL playback
- **GPS**: NMEA serial parser, mock GPS for testing
- **IMU**: ICM-20948 9-axis sensor, heading fusion

#### Rendering Stack (`render/`)
- **Canvas API** (`canvas.py`): Framework-agnostic drawing primitives
- **PPI View** (`view_ppi.py`): North-up polar display, range rings, aircraft glyphs
- **Layers**: Airports, sectors, aircraft trails, labels (collision-avoiding)
- **Labels** (`labels.py`): ATC-style data blocks, typography, layout

#### Platform Abstraction (`platform/`)
- **Display Backends**: Pygame (desktop), ILI9341 SPI TFT (Pi), Web (WebSocket)
- **Input Backends**: Mouse, XPT2046 touch, soft keys
- **Storage**: Settings persistence, SQLite caching

#### Data Management (`data/`)
- **Spatial DB** (`spatial.py`, `db.py`): R-tree indexed GeoJSON (airports, sectors)
- **Map Ingestion**: Convert GeoJSON to SQLite with spatial queries
- **Cache**: Persistent track history, rendering optimizations

---

## 3. Directory Structure

### `/src/pocketscope` - Main Application Code

#### `__init__.py`, `__main__.py`
Package root and CLI entry point. Version defined in `__init__.__version__`.

#### `boot.py`, `cli.py`
- **boot.py**: Application bootstrapping, dependency injection, wiring
- **cli.py**: Command-line argument parsing (`--playback`, `--fps`, `--tft`, etc.)

#### `config.py`, `settings_schema.py`
- Configuration loading (TOML, YAML, JSON)
- Pydantic schemas for settings validation
- Hot-reload support with file watchers

#### `theme.py`, `themes.yml`
- Palette management (colors for aircraft, UI elements, overlays)
- Theme inheritance and merging
- Live reload during development

---

### `/src/pocketscope/core` - Domain Logic (Pure, No I/O)

#### `events.py`
Event bus implementation (EventBus, Envelope, pack/unpack utilities)

#### `models.py`
Pydantic v2 data models (Aircraft, Position, Track, Ownship, etc.)

#### `tracks.py`
TrackService for managing aircraft state, history buffers, expiry timers

#### `geo.py`
Geometric utilities: haversine distance, bearing, coordinate conversion, polar ↔ cartesian

#### `time.py`
TimeProvider abstraction (real clock vs simulated clock for deterministic testing)

#### `domain/`
Additional domain services and value objects

---

### `/src/pocketscope/ingest` - Data Sources (Adapters)

#### `adsb/`
- `dump1090_json.py`: HTTP polling from dump1090 `/data/aircraft.json`
- `sbs_tcp.py`: SBS-1 BaseStation format over TCP:30003
- `beast.py`: Binary BEAST protocol decoder
- `playback.py`: JSONL file replay with simulated time

#### `gps/`
- `nmea_serial.py`: UART/USB GPS receiver (GPGGA, GPRMC)
- `mock_gps.py`: Fixed position for testing

#### `imu/`
- `icm20948.py`: I²C 9-axis IMU driver
- `fusion.py`: Sensor fusion for heading/attitude
- `mock_imu.py`: Simulated heading for desktop

---

### `/src/pocketscope/render` - Display Rendering (Framework-Agnostic)

#### `canvas.py`
Abstract canvas API (draw_line, draw_circle, draw_text, etc.). All backends implement this protocol.

#### `view_ppi.py`
North-up PPI radar display. Draws range rings, ownship marker, aircraft glyphs, trails.

#### `labels.py`
ATC-style label rendering: multi-line data blocks, collision avoidance, leader lines.

#### `geo_cache.py`
Caching for geographic projections and viewport clipping.

#### `airports_layer.py`, `sectors_layer.py`
Overlay layers for airports and airspace sectors from spatial DB.

#### `layers/`
Additional overlay implementations (trails, focus rings, etc.)

---

### `/src/pocketscope/platform` - Hardware/OS Abstraction

#### `display/`
- `pygame_backend.py`: Desktop windowing (dev mode)
- `ili9341_backend.py`: SPI TFT for Raspberry Pi (RGB565, DMA transfers)
- `web_backend.py`: WebSocket streaming for remote monitoring

#### `input/`
- `mouse_input.py`: Desktop mouse/keyboard
- `xpt2046_touch.py`: Resistive touchscreen for Pi
- `softkey_input.py`: Physical buttons or on-screen soft keys

#### `io/`
Storage, file watchers, network utilities

---

### `/src/pocketscope/ui` - User Interface Controllers

#### `controllers.py`
Main UI controller: focus management, gesture handling, layer composition

#### `softkeys.py`
Soft key bar (range, mode, filter controls)

#### `status_overlay.py`
Top bar displaying ownship position, heading, GPS status, FPS

#### `vertical_profile.py`
Side panel showing altitude distribution of nearby aircraft

#### `theme.py`
UI-specific theme application and color utilities

---

### `/src/pocketscope/data` - Persistent Data & Spatial Queries

#### `db.py`, `schema.sql`
SQLite database schema for airports, sectors, runways

#### `spatial.py`
R-tree spatial indexing for efficient viewport queries

#### `ingest_geojson_to_sqlite.py`
Tool to convert GeoJSON datasets to SQLite with spatial indexes

#### `sectors.py`, `runways_store.py`
Data access layers for airspace and airport data

#### `cache.py`
In-memory caching for frequently accessed spatial data

---

### `/src/pocketscope/app` - Application Assembly

#### `live_view.py`
Main application entry: wires together all services, starts event loop

---

### `/src/pocketscope/tools` - Utilities & Scripts

Recording/replay, data conversion, performance profiling, screenshot automation

---

### `/src/pocketscope/settings` - Configuration Management

Settings persistence, validation, hot-reload, migration utilities

---

### `/tests` - Test Suites

#### `tests/core/`
Unit tests for domain logic (geo, tracks, models, time, events)

#### `tests/ingest/`
Tests for ADS-B parsers, GPS, IMU, playback

#### `tests/render/`
Golden-frame tests, label layout, layer composition

#### `tests/platform/`
Display backend tests, input handling, integration smoke tests

#### `tests/data/`
Spatial DB queries, GeoJSON ingestion

#### `tests/ui/`
Controller behavior, soft key actions, theme application

#### `tests/fixtures/`
Sample data files (JSONL traces, GeoJSON, airports, sectors)

---

### `/docs` - Comprehensive Documentation

- `overview.md`: Architecture summary and doc map
- `event-bus.md`: Messaging patterns and topics
- `time-and-simulation.md`: Deterministic testing with simulated time
- `data-ingestion.md`: Source adapters and parsers
- `track-service.md`: Aircraft state management
- `rendering.md`: Canvas API, layers, backends
- `settings-and-softkeys.md`: Configuration and UI controls
- `recording-and-replay.md`: JSONL capture and playback
- `spatial.md`: GeoJSON ingestion and spatial queries
- `theming.md`: Palette system and customization
- `logging-and-telemetry.md`: Structured logging, performance metrics
- `systemd-setup.md`: Pi deployment and service configuration
- `feeder-setup.md`: Mac-side ADS-B feeder (readsb + JSON server) via pm2

---

### `/bootstrap_assets` - Deployment Files

- `pocketscope@.service`: templated systemd unit for Pi (instance per user, e.g. `pocketscope@pocketscope.service`)
- `pocketscope.env`: Environment variables for production
- `settings.yml`: Default user settings template

---

### `/sample_data` - Demo Data

- `demo_adsb.jsonl`: Sample ADS-B trace for playback mode
- `airports_ma.json`: Massachusetts airports GeoJSON
- `sectors_sample.json`: Airspace sectors GeoJSON

---

### `/examples` - Usage Examples

- `settings.example.yml`: Annotated configuration file

---

## 4. Coding Conventions

### Code Style (PEP 8 + Project Rules)
- **Line length**: 88 characters (enforced by ruff E501)
- **Formatter**: `black` (line-length=120 in pyproject.toml, but E501 enforces 88)
- **Import sorting**: `isort` (black profile)
- **Linting**: `ruff` (checks E501, type annotations, unused imports)

### Pre-commit Hooks (must pass)
- **black**: Code formatting (auto-fix)
- **ruff**: Linting (line length, type hints)
- **isort**: Import sorting (auto-fix)
- **mypy**: Optional but recommended for type checks in CI
- **pytest**: Tests must pass before merging

### Additional Style Rules
- Prefer **pure functions** in `core/` and keep side effects in adapters.
- **Type annotations required** for all functions; async functions that
    don't return should use `-> None`.
- **Handle Optional types explicitly**: check `if obj is not None:` before use.

### Type Annotations (Required)
```python
# ✅ Good: Explicit return types, Optional handling
def calculate_bearing(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Compute initial bearing between two points."""
    ...

async def publish_track(track: Track) -> None:
    """Publish track update to event bus."""
    ...

def get_subscription() -> Optional[AsyncIterator[Envelope]]:
    """Return subscription or None if unavailable."""
    ...

# ❌ Bad: Missing return type, unhandled Optional
def some_function():
    subscription = get_subscription()
    async for item in subscription:  # Error if None!
        pass
```

### Optional Type Handling (Critical)
```python
# ✅ Always check for None before accessing Optional types
subscription = bus.subscribe("adsb.raw")
if subscription is not None:
    async for envelope in subscription:
        process(envelope)

# ✅ Use walrus operator for concise checks
if (track := track_service.get(icao)) is not None:
    return track.altitude
```

### Docstrings (Concise + Assumptions)
```python
def haversine_distance(lat1: float, lon1: float, 
                       lat2: float, lon2: float) -> float:
    """
    Compute great-circle distance in nautical miles.
    
    Assumes WGS84 ellipsoid and inputs in decimal degrees.
    """
    ...
```

### Function Design
- **Pure functions in domain**: No side effects, no I/O, testable
- **Async sources**: Use `async def` generators for streaming data
- **Dependency injection**: Pass interfaces (ABC/Protocol), not concrete classes

### Common Pre-commit Issues (from `AGENTS.md`)
- **E501 Line too long**: Keep lines ≤88 chars; break long docstrings and calls.
- **Missing return type annotations**: Add `-> None` or the correct type.
- **Optional/Union misuse**: Check for `None` before iterating or accessing.
- **Untyped function calls**: Ensure functions have type annotations to avoid mypy issues.

---

## 5. Key Concepts & Patterns

### Event Envelope
Immutable message wrapper with metadata:
- `topic`: String identifier (e.g., `"adsb.raw"`)
- `timestamp`: Unix epoch seconds (real or simulated)
- `sender`: Source component identifier
- `payload`: msgpack-encoded bytes

### Track Management
- **State**: Current position, altitude, speed, heading
- **History**: Ring buffer of recent positions for trail rendering
- **Expiry**: Tracks removed after N seconds without updates
- **Focus**: User can "pin" an aircraft for detailed info

### Simulated Time
For deterministic testing, replace real clock with `SimulatedClock`:
```python
clock = SimulatedClock(start_time=0.0)
await clock.sleep(1.0)  # Instant in test, pauses event loop
clock.advance(10.0)     # Jump forward 10 seconds
```

### Canvas Protocol
All rendering code uses abstract canvas API. Backends implement:
- `draw_line(x1, y1, x2, y2, color, width)`
- `draw_circle(x, y, radius, color, fill)`
- `draw_text(x, y, text, font, color, align)`
- `clear(color)`, `present()`

### Layers
Rendering is compositional. Each layer:
1. Subscribes to relevant topics (tracks, airports, etc.)
2. Computes drawable elements in `update()`
3. Renders to canvas in `draw(canvas)`

### Soft Keys
On-screen or physical buttons for common actions:
- **Range +/-**: Zoom in/out (5nm, 10nm, 25nm, 50nm)
- **Mode**: Switch label format (simple, full, callsign-only)
- **Filter**: Altitude/distance thresholds
- **Screenshot**: Capture current frame

---

## 6. Development Workflows

### Adding a New ADS-B Source
1. Create class in `ingest/adsb/` implementing async generator
2. Publish decoded messages to `"adsb.raw"` topic
3. Add CLI flag in `cli.py` to select source
4. Wire in `boot.py` dependency injection
5. Add unit tests with fixtures in `tests/ingest/`

### Adding a New Render Layer
1. Create class in `render/layers/` with `update()` and `draw(canvas)`
2. Subscribe to relevant topics in `__init__`
3. Register layer in `view_ppi.py` layer stack
4. Add theme colors in `themes.yml` if needed
5. Add golden-frame test in `tests/render/`

### Adding a New Display Backend
1. Implement canvas protocol in `platform/display/`
2. Add CLI flag for backend selection
3. Wire in `boot.py` with conditional import
4. Test with deterministic playback: `--playback --backend new_backend`

### Adding a Configuration Setting
1. Add field to Pydantic schema in `settings_schema.py`
2. Update default `settings.yml` and `examples/settings.example.yml`
3. Add validation/migration logic if needed
4. Wire to relevant service in `boot.py`
5. Add test in `tests/settings/`

### Running Tests
```bash
# Full suite
pytest

# Specific module
pytest tests/core/test_geo_unit.py -v

# With coverage
pytest --cov=src/pocketscope --cov-report=html

# Property-based tests only
pytest -k "property" -v
```

### Pre-Commit Checks (Required Before Commit)
```bash
pre-commit run --all-files
# OR manually:
black .
isort .
ruff check .
mypy src/
pytest
```

### Running the Application
```bash
# Desktop with demo data
python -m pocketscope --playback sample_data/demo_adsb.jsonl

# Live from dump1090
python -m pocketscope --adsb-src JSON

# Embedded on Pi
python -m pocketscope --tft --adsb-src JSON --gps /dev/ttyUSB0

# Web UI for remote monitoring
python -m pocketscope --web-ui --playback sample_data/demo_adsb.jsonl

# Performance profiling (5 seconds at 12 FPS)
POCKETSCOPE_LOGGING_LEVEL=INFO python -m pocketscope \
  --playback sample_data/demo_adsb.jsonl \
  --fps 12 --run-seconds 5 | grep 'ui.perf'
```

---

## 7. Important Files & Locations

### Entry Points
- `src/pocketscope/__main__.py`: CLI entry (`python -m pocketscope`)
- `src/pocketscope/cli.py`: Argument parsing
- `src/pocketscope/boot.py`: Dependency injection and wiring
- `src/pocketscope/app/live_view.py`: Main application loop

### Configuration
- `~/.pocketscope/settings.json`: User settings (runtime editable)
- `src/pocketscope/themes.yml`: Color palettes
- `bootstrap_assets/settings.yml`: Production defaults
- `pyproject.toml`: Build config, tool settings, dependencies

### Critical Domain Files
- `src/pocketscope/core/events.py`: Event bus (hub of all communication)
- `src/pocketscope/core/tracks.py`: Aircraft state management
- `src/pocketscope/core/geo.py`: Geometric calculations
- `src/pocketscope/core/time.py`: Real vs simulated clock

### Key Rendering Files
- `src/pocketscope/render/canvas.py`: Drawing abstraction
- `src/pocketscope/render/view_ppi.py`: PPI display logic
- `src/pocketscope/render/labels.py`: Label layout and collision

### Platform Backends
- `src/pocketscope/platform/display/pygame_backend.py`: Desktop
- `src/pocketscope/platform/display/ili9341_backend.py`: Raspberry Pi TFT
- `src/pocketscope/platform/input/xpt2046_touch.py`: Pi touchscreen

### Data & Spatial
- `src/pocketscope/data/db.py`: SQLite schema and queries
- `src/pocketscope/data/spatial.py`: R-tree spatial indexing
- `src/pocketscope/data/ingest_geojson_to_sqlite.py`: Data import tool

---

## 8. Copilot Context Hints

### When Working in `/src/pocketscope/core`
- **Assume**: Pure domain logic, no I/O, no framework imports
- **Pattern**: Pure functions or stateless services with dependency injection
- **Testing**: Unit tests with mocks, property-based tests preferred
- **Avoid**: Importing from `platform/`, `ingest/`, `render/` (except interfaces)

### When Working in `/src/pocketscope/ingest`
- **Assume**: Adapter pattern, async generators, publish to event bus
- **Pattern**: `async def run(bus: EventBus) -> None:` with `async for` loops
- **Testing**: Use fixtures from `tests/fixtures/`, deterministic playback
- **Avoid**: Direct calls to domain services (use event bus)

### When Working in `/src/pocketscope/render`
- **Assume**: Framework-agnostic, use canvas protocol only
- **Pattern**: Layers with `update()` and `draw(canvas)` methods
- **Testing**: Golden-frame comparisons with simulated time
- **Avoid**: Direct hardware access, blocking I/O in draw loops

### When Working in `/src/pocketscope/platform`
- **Assume**: Hardware/OS-specific code, conditional imports for Pi
- **Pattern**: Implement abstract protocols from `core/` or `render/`
- **Testing**: Mocks for hardware, integration tests on target device
- **Avoid**: Business logic (delegate to domain services)

### When Working in `/src/pocketscope/ui`
- **Assume**: Coordinating layers, handling input events, theme application
- **Pattern**: Controllers subscribe to input events, dispatch to services
- **Testing**: Event-driven tests with simulated input
- **Avoid**: Direct rendering (use canvas layers)

### When Working in `/src/pocketscope/data`
- **Assume**: Persistent storage, spatial queries, caching
- **Pattern**: Repository pattern, R-tree indexing, SQLite transactions
- **Testing**: Fixtures with sample GeoJSON, query result validation
- **Avoid**: Mutable global state (use dependency injection)

### When Writing Tests
- **Always**: Use simulated time for deterministic behavior
- **Always**: Run with timeout (10s default) to catch hangs
- **Prefer**: Property-based tests (Hypothesis) for geometry/math
- **Prefer**: Golden-frame tests for rendering validation
- **Use**: Fixtures from `tests/fixtures/` for sample data
- **Avoid**: Real hardware in unit tests (use mocks)

### Common Pre-Commit Failures to Avoid
1. **E501 Line too long**: Break long lines (especially comments) at 88 chars
2. **Missing return type**: Add `-> None` or explicit return type to all functions
3. **Optional without None check**: Always check `if obj is not None:` before use
4. **Unused imports**: Remove or use all imported names
5. **Import sorting**: Let `isort` handle it automatically

### Performance Considerations
- **Hot paths**: Avoid allocations in per-frame rendering (reuse buffers)
- **Geometry**: Use NumPy for vectorized calculations
- **Spatial queries**: Use R-tree indexes, not linear scans
- **Event bus**: Tune queue sizes for backpressure (default 256)
- **Rendering**: Target ≥5 FPS with 20 aircraft on Pi Zero 2 W

### Security & Privacy
- **Offline-first**: No outbound network except local dump1090
- **No secrets**: Don't embed API keys or credentials
- **Run as non-root**: Use systemd user services on Pi
- **License headers**: Respect MIT license in all new files

---

## 9. Quick Reference

### Common CLI Commands
```bash
# Development with demo data
python -m pocketscope --playback sample_data/demo_adsb.jsonl

# Live on desktop
python -m pocketscope --adsb-src JSON

# Raspberry Pi embedded
python -m pocketscope --tft --adsb-src JSON --gps /dev/ttyUSB0

# Web monitoring
python -m pocketscope --web-ui --playback sample_data/demo_adsb.jsonl

# Performance profiling
POCKETSCOPE_LOGGING_LEVEL=INFO python -m pocketscope \
  --playback sample_data/demo_adsb.jsonl --fps 12 --run-seconds 5
```

### Testing Commands
```bash
pytest                              # Full suite
pytest -v -k "geo"                  # Geometry tests only
pytest --cov=src/pocketscope        # With coverage
pytest -x                           # Stop on first failure
pytest tests/render/test_golden_ppi.py -v  # Specific test file
```

### Code Quality Commands
```bash
black .                             # Auto-format
isort .                             # Sort imports
ruff check .                        # Lint
ruff check --fix .                  # Auto-fix lint issues
mypy src/                           # Type check
pre-commit run --all-files          # All checks
```

### Event Bus Topics (Reference)
- `"adsb.raw"`: Raw ADS-B messages (JSON/SBS/BEAST decoded)
- `"tracks.updates"`: Aircraft track state changes
- `"gps.position"`: Ownship GPS position
- `"imu.heading"`: Heading from IMU sensor
- `"ui.input"`: User input events (touch, keys)
- `"ui.screenshot"`: Screenshot capture requests
- `"settings.changed"`: Configuration hot-reload events
- `"theme.changed"`: Palette update events

### File Extensions by Purpose
- `.py`: Python source (strict type hints required)
- `.jsonl`: JSONL traces for playback (one JSON per line)
- `.json`: GeoJSON data, settings files
- `.yml`: Theme definitions, configuration
- `.toml`: Build config (`pyproject.toml`)
- `.sql`: Database schema
- `.md`: Documentation

### Environment Variables
- `POCKETSCOPE_HOME`: Override default `~/.pocketscope/` config directory
- `POCKETSCOPE_LOGGING_LEVEL`: Set log level (DEBUG, INFO, WARNING, ERROR)
- `PYTHONPATH`: Ensure `src/` is in path for development

---

## 10. Resources & Further Reading

### Documentation Map
- Start with [`docs/overview.md`](../docs/overview.md) for architecture summary
- See [`docs/event-bus.md`](../docs/event-bus.md) for messaging patterns
- Consult [`docs/rendering.md`](../docs/rendering.md) for display details
- Read [`AGENTS.md`](../AGENTS.md) for AI-assistant coding guidelines

### External References
- **ADS-B Protocol**: [Mode S overview](https://mode-s.org/)
- **dump1090**: [FlightAware dump1090-fa](https://github.com/flightaware/dump1090)
- **Raspberry Pi**: [GPIO pinout](https://pinout.xyz/)
- **Pydantic**: [v2 docs](https://docs.pydantic.dev/latest/)
- **Pygame**: [pygame-ce docs](https://pyga.me/)

### Contributing
- Fork and create feature branches
- Keep PRs small and focused
- Include tests and documentation
- Run pre-commit checks before pushing
- Reference related issues and docs in PR descriptions

---

**Last Updated**: 2026-06-25 | **Maintainer**: PocketScope Team | **License**: MIT`