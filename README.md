# PocketScope

<!-- Badges -->
[![Version](https://img.shields.io/github/v/tag/ChrisPatten/pocket-scope?label=version)](https://github.com/ChrisPatten/pocket-scope/tags)
[![Release](https://img.shields.io/github/v/release/ChrisPatten/pocket-scope)](https://github.com/ChrisPatten/pocket-scope/releases)
<!-- Uncomment once published to PyPI: -->
<!-- [![PyPI](https://img.shields.io/pypi/v/pocketscope)](https://pypi.org/project/pocketscope/) -->

**Current Version:** 0.1.1 *(pi-display branch adds embedded SPI TFT + touch + web UI backends)*


PocketScope is a handheld Pi-powered ATC-style scope for decoding and displaying ADS-B traffic. Built with Python, it features a modular, event-driven architecture designed for real-time sensor data processing, deterministic testing, and rapid prototyping.

## Features

### Core Architecture
- **Event-Driven System**: Async EventBus with bounded queues and backpressure handling
- **Time Abstraction**: Deterministic testing support with SimTimeSource and RealTimeSource
- **Modular Design**: Clean separation between ingestion, processing, and visualization
- **Rendering/UI**: Canvas API + multiple backends (Pygame, SPI TFT ILI9341, Web) with ATC-style data blocks, sector + airports overlays, deterministic golden-frame tests
- **Persistent UI Settings**: Debounced JSON settings (units, default range, track length preset, demo mode, altitude filter incl. custom bounds, north-up lock) with soft key bar + settings screen

### Data Sources
- **ADS-B**: File-based playback with deterministic timing and live polling of dump1090 `aircraft.json`
- **Aircraft Tracking**: Real-time track maintenance with ring-buffer trails, state aggregation, and expiry management
- **GPS**: Position and navigation data (NMEA serial)
- **IMU**: Inertial measurement unit integration (9-axis sensors)
- **Airports**: Static airport reference data from JSON files with identifier, lat/lon positioning
- **Sector Data**: ARTCC sector polygon overlays (JSON/GeoJSON format)

### Infrastructure
- **Record/Replay System**: JSONL-based event recording and deterministic replay
- **Platform Abstraction**: Display, input, and I/O abstraction layers (now includes SPI TFT + touch & WebSocket/web page backends)
- **Layered Rendering**: Composable visualization pipeline built on a minimal Canvas API
- **Comprehensive Testing**: Full test suite (core, UI, rendering, platform drivers)

### Navigation & Geodesy
- **WGS‑84 Helpers**: Great‑circle distance (NM), initial bearing, destination point
- **Frames**: Geodetic⇄ECEF conversion and ECEF→ENU local tangent plane
- **Mapping**: ENU→screen north‑up mapping and range/bearing convenience APIs

# PocketScope

<!-- Badges -->
[![Version](https://img.shields.io/github/v/tag/ChrisPatten/pocket-scope?label=version)](https://github.com/ChrisPatten/pocket-scope/tags)
[![Release](https://img.shields.io/github/v/release/ChrisPatten/pocket-scope)](https://github.com/ChrisPatten/pocket-scope/releases)
<!-- Uncomment once published to PyPI: -->
<!-- [![PyPI](https://img.shields.io/pypi/v/pocketscope)](https://pypi.org/project/pocketscope/) -->

**Current Version:** 0.1.1 *(pi-display branch adds embedded SPI TFT + touch + web UI backends)*

PocketScope is a handheld Pi-powered ATC-style scope for decoding and displaying ADS-B traffic. Built with Python, it features a modular, event-driven architecture designed for real-time sensor data processing, deterministic testing, and rapid prototyping.

## Features

### Core Architecture
- **Event-Driven System**: Async EventBus with bounded queues and backpressure handling
- **Time Abstraction**: Deterministic testing support with SimTimeSource and RealTimeSource
- **Modular Design**: Clean separation between ingestion, processing, and visualization
- **Rendering/UI**: Canvas API + multiple backends (Pygame, SPI TFT ILI9341, Web) with ATC-style data blocks, sector + airports overlays, deterministic golden-frame tests
- **Persistent UI Settings**: Debounced JSON settings (units, default range, track length preset, demo mode, altitude filter incl. custom bounds, north-up lock) with soft key bar + settings screen

### Data Sources
- **ADS-B**: File-based playback with deterministic timing and live polling of dump1090 `aircraft.json`
- **Aircraft Tracking**: Real-time track maintenance with ring-buffer trails, state aggregation, and expiry management
- **GPS**: Position and navigation data (NMEA serial)
- **IMU**: Inertial measurement unit integration (9-axis sensors)
- **Airports**: Static airport reference data from JSON files with identifier, lat/lon positioning
- **Sector Data**: ARTCC sector polygon overlays (JSON/GeoJSON format)

### Infrastructure
- **Record/Replay System**: JSONL-based event recording and deterministic replay
- **Platform Abstraction**: Display, input, and I/O abstraction layers (now includes SPI TFT + touch & WebSocket/web page backends)
- **Layered Rendering**: Composable visualization pipeline built on a minimal Canvas API
- **Comprehensive Testing**: Full test suite (core, UI, rendering, platform drivers)

### Navigation & Geodesy
- **WGS‑84 Helpers**: Great‑circle distance (NM), initial bearing, destination point
- **Frames**: Geodetic⇄ECEF conversion and ECEF→ENU local tangent plane
- **Mapping**: ENU→screen north‑up mapping and range/bearing convenience APIs

## Architecture Overview

### Core Components

#### EventBus (`src/pocketscope/core/events.py`)
High-performance async event bus with:
- Per-topic bounded queues with configurable sizes
- Drop-oldest backpressure policy
- Multiple subscribers per topic
- Clean shutdown with sentinel-based termination
- Built-in metrics and monitoring

```python
from pocketscope.core.events import EventBus, pack, unpack

bus = EventBus(default_maxsize=256)
sub = bus.subscribe("adsb.raw")

async def producer():
    for i in range(10):
        await bus.publish("adsb.raw", pack({"aircraft_id": i, "altitude": 35000}))
    await bus.close()

async def consumer():
    async for envelope in sub:
        data = unpack(envelope.payload)
        print(f"Received: {data} at {envelope.ts}")
```

#### Time Abstraction (`src/pocketscope/core/time.py`)
Unified time interface supporting both real-time and deterministic simulation:

**RealTimeSource**: Uses system clocks for production
```python
from pocketscope.core.time import RealTimeSource

ts = RealTimeSource()
start = ts.monotonic()
await ts.sleep(1.0)
elapsed = ts.monotonic() - start  # ~1.0 seconds
```

**SimTimeSource**: Deterministic time for testing
```python
from pocketscope.core.time import SimTimeSource

ts = SimTimeSource(start=0.0)
task = asyncio.create_task(ts.sleep(5.0))
ts.advance(5.0)  # Instantly advance time
await task  # Completes immediately
```

#### Data Models (`src/pocketscope/core/models.py`)
Comprehensive Pydantic v2 data models for all sensor inputs and domain objects:

- **AdsbMessage**: Normalized ADS-B transponder data with validation
- **GpsFix**: GPS position, time, and navigation data
- **ImuSample**: 9-axis inertial measurement readings
- **AircraftTrack**: Aggregated aircraft state with position history
- **HistoryPoint**: Time-stamped position data for track visualization

```python
from pocketscope.core.models import AdsbMessage, AircraftTrack

# ADS-B message parsing with validation
message = AdsbMessage(
    ts=datetime.utcnow(),
    icao24="a12345",
    callsign="UAL123",
    lat=37.7749,
    lon=-122.4194,
    alt_ft=35000
)

# Aircraft tracking with history
track = AircraftTrack(
    icao24="a12345",
    callsign="UAL123",
    last_ts=datetime.utcnow()
)
track.add_point((datetime.utcnow(), 37.7749, -122.4194, 35000))
```

#### Record/Replay System (`src/pocketscope/tools/record_replay.py`)
JSONL-based event recording and replay with timing preservation:

**Recording Events**:
```python
from pocketscope.tools.record_replay import JsonlRecorder

recorder = JsonlRecorder(bus, ts, "flight_data.jsonl", ["adsb.raw", "gps.position"])
record_task = asyncio.create_task(recorder.run())

# ... publish events to bus ...

await recorder.stop()
```

**Replaying Events**:
```python
from pocketscope.tools.record_replay import JsonlReplayer

# Real-time replay at 2x speed
replayer = JsonlReplayer(bus, ts, "flight_data.jsonl", speed=2.0)
await replayer.run()

# Deterministic replay for testing
sim_ts = SimTimeSource()
replayer = JsonlReplayer(bus, sim_ts, "flight_data.jsonl")
replay_task = asyncio.create_task(replayer.run())

# Manually control time advancement
next_time = replayer.next_due_monotonic()
sim_ts.set_time(next_time)
```

#### ADS-B File Playback (`src/pocketscope/ingest/adsb/playback_source.py`)
Deterministic ADS-B message replay from JSONL trace files with precise timing control:

**Key Features**:
- Deterministic timing with SimTimeSource integration
- Real-time playback with RealTimeSource
- Speed multiplier support (e.g., 2x for faster replay)
- Loop mode for continuous testing
- Graceful start/stop control
- Next event timing queries for precise simulation

**File Format** (JSONL, one object per line):
```json
{
  "t_mono": 0.00,
  "msg": {
    "icao24": "abc123",
    "callsign": "TEST1", 
    "lat": 40.0,
    "lon": -74.0,
    "baro_alt": 32000,
    "ground_speed": 450,
    "track_deg": 270,
    "src": "PLAYBACK"
  }
}
```

**Basic Usage**:
```python
from pocketscope.core.events import EventBus
from pocketscope.core.time import SimTimeSource
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource

bus = EventBus()
ts = SimTimeSource()
source = FilePlaybackSource("trace.jsonl", ts=ts, bus=bus, speed=2.0)

# Start playback in background
task = asyncio.create_task(source.run())

# For deterministic testing - advance to next event
next_time = source.next_due_monotonic()
if next_time:
    ts.set_time(next_time)

# Stop playback
await source.stop()
await task
```

**Deterministic Testing Pattern**:
```python
@pytest.mark.asyncio
async def test_adsb_processing():
    bus = EventBus()
    ts = SimTimeSource(start=0.0)
    source = FilePlaybackSource("test_data.jsonl", ts=ts, bus=bus)
    
    # Subscribe to ADS-B messages
    sub = bus.subscribe("adsb.msg")
    received = []
    
    async def collector():
        async for env in sub:
            msg_dict = unpack(env.payload)
            received.append((ts.monotonic(), msg_dict["icao24"]))
    
    collector_task = asyncio.create_task(collector())
    playback_task = asyncio.create_task(source.run())
    
    # Deterministic event processing
    while True:
        next_due = source.next_due_monotonic()
        if next_due is None:
            break
        ts.set_time(next_due)
        await asyncio.sleep(0)  # Process events
    
    await playback_task
    await sub.close()
    collector_task.cancel()
    
    # Verify deterministic timing
    assert received[0] == (0.0, "abc123")
    assert received[1] == (0.4, "def456")
```

**Real-time Playback**:
```python
from pocketscope.core.time import RealTimeSource

ts = RealTimeSource()
source = FilePlaybackSource(
    "flight_trace.jsonl", 
    ts=ts, 
    bus=bus, 
    speed=1.0,  # Real-time speed
    loop=True   # Continuous playback
)

await source.run()  # Runs until stopped
```

#### Live ADS-B via dump1090 JSON (`src/pocketscope/ingest/adsb/json_source.py`)
Polls a dump1090 server's `aircraft.json` over HTTP and publishes normalized `AdsbMessage` events to `adsb.msg`.

Key features:
- Efficient polling with `aiohttp` and timeouts
- Conditional requests (ETag/If-Modified-Since) to avoid re-downloading unchanged data
- Exponential backoff on errors with automatic recovery
- Stale filtering using `seen/seen_pos` thresholds
- Strict field normalization (lowercased 6-hex ICAO, trimmed callsigns, numeric conversions)

Basic usage:
```python
import asyncio
from pocketscope.core.events import EventBus, unpack
from pocketscope.ingest.adsb.json_source import Dump1090JsonSource

async def main():
    bus = EventBus()
    sub = bus.subscribe("adsb.msg")

    # Point to your dump1090 instance (usually http://<host>:8080/data/aircraft.json)
    src = Dump1090JsonSource(
        url="http://127.0.0.1:8080/data/aircraft.json",
        bus=bus,
        poll_hz=5.0,
    )

    task = asyncio.create_task(src.run())
    try:
        for _ in range(5):
            env = await sub.__anext__()
            print(unpack(env.payload))
    finally:
        await src.stop()
        task.cancel()
        await bus.close()

if __name__ == "__main__":
    asyncio.run(main())
```

#### Track Service (`src/pocketscope/core/tracks.py`)
Domain service for maintaining aircraft tracks from incoming ADS-B messages with ring-buffer trails, quality tracking, and expiry management:

**Key Features**:
- Ring-buffer trail management with configurable time windows
- 1Hz position sampling to prevent data flooding
- Track state aggregation (callsign, altitude, speed, etc.)
- Pinning functionality for extended trail retention
- Automatic expiry of stale tracks
- Deterministic behavior with SimTimeSource

**Basic Usage**:
```python
from pocketscope.core.events import EventBus, pack
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.core.models import AdsbMessage

# Setup
bus = EventBus()
ts = SimTimeSource(start=0.0)
service = TrackService(
    bus, 
    ts,
    trail_len_default_s=60.0,    # Default 60s trail
    trail_len_pinned_s=180.0,    # Pinned tracks: 180s trail
    expiry_s=12.0                # Expire after 12s without updates
)

# Start the service
await service.run()

# Publish ADS-B messages - service automatically maintains tracks
msg = AdsbMessage(
    ts=datetime.utcnow(),
    icao24="abc123", 
    lat=40.0,
    lon=-74.0,
    callsign="UAL123",
    baro_alt=35000
)
await bus.publish("adsb.msg", pack(msg.model_dump()))

# Query tracks
track = service.get("abc123")
if track:
    print(f"Track {track.callsign}: {len(track.history)} trail points")
    print(f"Last position: {track.history[-1][1:]}")  # lat, lon, alt

# Pin important tracks for longer retention
service.pin("abc123", True)

# List all active tracks
active_tracks = service.list_active()
print(f"Tracking {len(active_tracks)} aircraft")

# Stop the service
await service.stop()
```

#### Geodesy (`src/pocketscope/core/geo.py`)
Numerically stable WGS‑84 utilities for navigation and rendering:
- Spherical helpers: `haversine_nm`, `initial_bearing_deg`, `dest_point`
- Frames: `geodetic_to_ecef`, `ecef_to_enu`
- Convenience: `range_bearing_from`, `enu_to_screen`

```python
from pocketscope.core.geo import (
    haversine_nm,
    initial_bearing_deg,
    dest_point,
    range_bearing_from,
    geodetic_to_ecef,
    ecef_to_enu,
)

# Distance and bearing
nm = haversine_nm(37.6188, -122.375, 34.0522, -118.2437)  # SFO→LAX in NM
bearing = initial_bearing_deg(37.6188, -122.375, 34.0522, -118.2437)

# Forward destination
lat2, lon2 = dest_point(37.6188, -122.375, bearing_deg=135.0, range_nm=10.0)

# Local ENU frame at an origin
xe, ye, ze = geodetic_to_ecef(37.6188, -122.375, alt_m=0.0)
e, n, u = ecef_to_enu(xe, ye, ze, lat0=37.6188, lon0=-122.375, alt0_m=0.0)

# Range/Bearing convenience
rng_nm, brg_deg = range_bearing_from(37.6188, -122.375, lat2, lon2)
```

**Trail Management**:
- **Ring Buffer**: Automatically trims old position points based on time windows
- **1Hz Sampling**: Limits position updates to minimum 0.9 second intervals
- **Time-Based Trimming**: Keeps recent points within configured time window
- **Pinned Tracks**: Extended retention for tracks of interest

**State Tracking**:
- **Position History**: Time-stamped trail of (timestamp, lat, lon, altitude)
- **Dynamic State**: Speed, heading, vertical rate, transponder codes
- **Metadata**: Callsign, ICAO24, last update time, data quality indicators

**Deterministic Testing**:
```python
@pytest.mark.asyncio
async def test_track_behavior():
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts, trail_len_default_s=10.0)
    
    await service.run()
    
    # Create predictable message sequence
    for t in [0.0, 1.0, 2.0, 3.0]:
        msg = AdsbMessage(
            ts=datetime.fromtimestamp(t, tz=timezone.utc),
            icao24="test123",
            lat=40.0 + t * 0.01,
            lon=-74.0
        )
        ts.set_time(t)
        await bus.publish("adsb.msg", pack(msg.model_dump()))
        await asyncio.sleep(0)  # Process events
    
    # Verify trail points
    track = service.get("test123")
    assert len(track.history) == 4
    
    await service.stop()
```

### Rendering and UI

#### Canvas/Display/Input Abstractions
- `Canvas` and `DisplayBackend` protocols provide a minimal, framework-agnostic rendering contract in `src/pocketscope/render/canvas.py`.
- Backends:
    - **Pygame** (desktop + headless via `SDL_VIDEODRIVER=dummy`)
    - **ILI9341 SPI TFT** (`platform/display/ili9341_backend.py`): RGB565 conversion, chunked SPI writes (2KB), brightness + draw-op heuristics to skip flicker/blank frames, watchdog + exponential backoff recovery, optional 180° flip, replays last good frame after recovery.
    - **WebDisplayBackend** (minimal browser view via WebSocket) – optional CLI `--web-ui`.
  - Input:
    - Pygame mouse → tap events.
    - **XPT2046 Touch** (`platform/input/xpt2046_touch.py`): median-of-3 sampling, linear calibration, high-frequency polling (`--touch-hz`, default 180 Hz), synthesized down / drag / tap forwarded into UI (softkeys + settings screen).
  - Airports & sector overlays: range-aware culling, screen clamping, sample data auto-detect.
  - Label layout: collision set now seeded with aircraft glyph footprint to prevent labels covering aircraft markers.

#### Persistent Settings & Soft Keys

User-facing configuration is now persisted across runs:

- `Settings` model (`settings/schema.py`) stored at `~/.pocketscope/settings.json` (override path with `POCKETSCOPE_HOME`).
- Debounced atomic writes via `SettingsStore` to minimize unnecessary disk I/O while adjusting controls rapidly.
- `ConfigWatcher` publishes `cfg.changed` when the file mtime changes allowing hot reload (e.g. manual edit in an editor) without restarting.
- Soft key bar (`ui/softkeys.py`) renders large tap/click targets (Zoom-/+, Units, Tracks, Demo, Settings) with auto font scaling.
- Settings screen overlay (`ui/settings_screen.py`) toggled by Settings soft key or `s` key; edits are staged and flushed explicitly via Save (Back dismisses without forcing immediate write).
- Persisted fields: units; default range step (2/5/10/20/40/80 NM); track length preset (short=15s, medium=45s, long=120s); demo mode; altitude filter band (All, 0–5k, 5–10k, 10–20k, >20k) **plus custom altitude bounds**; north-up lock.
- Altitude filter excludes aircraft outside selected band (and hides unknown altitude when a band is active).
- North-up lock enforces rotation 0°; unlock enables arrow-key rotation.
- Demo mode loops a bundled JSONL trace (`sample_data/demo_adsb.jsonl`), temporarily re-centers on first record and displays a DEMO badge.

Extensive tests in `tests/ui/` assert persistence, hot reload, filter correctness, rotation lock behavior, settings screen mouse interactions, and trail length trimming.

#### Display & Input Backends
- **Pygame** display: `platform/display/pygame_backend.py` (windowed + headless; PNG snapshots for tests)
- **ILI9341 SPI TFT**: `platform/display/ili9341_backend.py` (RGB565 encode, chunked writes, blink mitigation, watchdog/status ping recovery, last-frame resend, optional flip)
- **Web**: `platform/display/web_backend.py` (optional minimal browser rendering)
- Input:
    - Pygame mouse mapping (`platform/input/pygame_input.py`)
    - XPT2046 touch (`platform/input/xpt2046_touch.py`) high-rate polling & synthesized events

#### North-up PPI View
- `src/pocketscope/render/view_ppi.py` implements a north-up Plan Position Indicator with:
    - Range rings and cardinal ticks (N/E/S/W)
    - Ownship symbol
    - Ring-buffer trails, aircraft glyphs, and labels
    - Optional airports overlay (5x5 px squares + monospaced ident labels) with range-based culling
    - Optional sector polygon overlays with configurable colors and transparency
    - ATC-style three-line data blocks with leader lines (default)
    - Altitude filter band + north-up lock (rotation enforced when locked; filtering applied during snapshot build)

##### ATC-style Data Blocks
The PPI view supports rich ATC-style data blocks backed by `render/labels.py`:

- Formatting (`DataBlockFormatter`):
    - Three fixed lines in the standard mode (default):
        1) CALLSIGN or ICAO24 (uppercased)
        2) Altitude in hundreds of feet, zero-padded to 3 digits, using geometric altitude if present else barometric; suffix “+” if climb rate > +500 fpm, “-” if < -500 fpm
        3) Bearing from ownship (0..359, 3 digits) and speed in tens of knots (2 digits)
- Layout (`DataBlockLayout`):
    - Places blocks near aircraft with leader lines to the nearest block edge
    - Collision avoidance by nudging outward in a small spiral while clamping on screen
- Typography controls: font size and inter-line gap are configurable
- Simple label mode: fall back to a minimal one-line label near the glyph

Defaults:
- The live viewer shows full data blocks by default; pass `--simple` to enable minimal labels.
- Leader lines and collision-aware placement are enabled automatically.
- Airports overlay can be enabled in the live viewer via `--airports PATH`; if omitted, a `sample_data/airports.json` file is auto-detected when present.
    - ENU mapping from geodetic sources
- Range-based culling at current PPI range
- Soft key bar + settings screen available in live viewer for quick range, units, track length, demo toggle, altitude filter, and north-up lock adjustments.

#### Rendering Tests
- A lightweight, headless input smoke test validates the pygame backend event flow.
- Test: `src/pocketscope/tests/render/test_golden_ppi.py`
- UI smoke test (range zoom, overlay, frame loop): `src/pocketscope/tests/ui/test_ui_smoke.py`

### Project Structure

```
src/pocketscope/
├── __main__.py             # CLI entry point
├── config.py               # Runtime config module (merges persisted + CLI overrides)
├── core/                   # Core event and time systems
│   ├── events.py           # EventBus implementation
│   ├── geo.py              # WGS‑84 geodesy helpers (distance, bearings, ECEF/ENU)
│   ├── time.py             # Time abstraction (Real/Sim)
│   ├── models.py           # Pydantic data models
│   ├── tracks.py           # Track Service for aircraft state management
│   └── domain/             # Domain logic (planned)
├── ingest/                 # Data ingestion modules
│   ├── adsb/               # ADS-B transponder data
│   │   ├── playback_source.py  # File-based ADS-B replay
│   │   └── json_source.py      # Live dump1090 aircraft.json polling
│   ├── gps/                # GPS/GNSS data
│   └── imu/                # Inertial measurement data
├── platform/               # Hardware abstraction
│   ├── display/            # Display drivers
│   │   ├── pygame_backend.py   # Pygame display backend (headless-friendly)
│   │   ├── ili9341_backend.py  # ILI9341 SPI TFT driver (pi-display)
│   │   └── spi_lock.py         # SPI locking helper for safe concurrent access
│   ├── input/              # Input handling
│   │   ├── pygame_input.py     # Pygame input backend (mouse→tap)
│   │   └── xpt2046_touch.py    # XPT2046 touch driver (pi-display)
│   └── io/                 # I/O interfaces
├── render/                 # Visualization pipeline
│   ├── canvas.py           # Canvas/DisplayBackend protocols and drawing primitives
│   ├── view_ppi.py         # Plan Position Indicator view (north-up)
│   ├── airports_layer.py   # Airports overlay layer (markers + ident labels)
│   ├── sectors_layer.py    # Sector polygon overlay layer
│   └── layers/             # Composable render layers
├── data/                   # Reference and lightweight spatial data helpers
│   ├── airports.py         # Airports loader + nearest-neighbor selection
│   └── sectors.py          # ARTCC sector polygons loader (JSON/GeoJSON)
├── tools/                  # Development and debugging tools
│   ├── record_replay.py    # Event recording/replay
│   └── test_dump1090_fetch.py # Fetch test helper for dump1090/http
├── examples/               # Small runnable examples
│   └── live_view.py        # Minimal on-screen PPI viewer for live ADS-B
├── tests/                  # Comprehensive test suite
│   ├── core/               # Core system tests
│   ├── data/               # Test data files
│   │   └── adsb_trace_ppi.jsonl # Golden PPI test trace (auto-created if absent)
│   ├── ingest/             # Live ingestion tests
│   │   └── test_dump1090_json_source.py
│   ├── platform/           # Platform/hardware tests (ILI9341, touch, integration)
│   │   ├── test_ili9341_backend.py
│   │   ├── test_ili9341_chunking.py
│   │   ├── test_integration_smoke.py
│   │   └── test_xpt2046_touch.py
│   ├── render/             # Rendering tests
│   │   └── test_golden_ppi.py   # Pygame backend input smoke test
│   ├── ui/                 # UI tests (settings, softkeys, altitude filter)
│   └── unit/               # Unit tests
└── ui/                     # User interface components
    ├── controllers.py      # UI controllers (keyboard/mouse, frame loop)
    ├── status_overlay.py   # On-screen status HUD (FPS, range, tracks, bus, UTC)
    ├── softkeys.py         # Soft key handling
    └── settings/           # Persistent settings schema & values
```

## Development Setup

### Prerequisites
- Python 3.11+
- Git
- Virtual environment (recommended)

### Dependencies
Core runtime dependencies:
- **pydantic>=2.0**: Data validation and serialization
- **numpy>=1.24**: Numerical operations and geometry
- **msgpack>=1.0**: Efficient binary serialization
- **aiohttp>=3.9**: HTTP client for live dump1090 polling
- **pygame-ce>=2.5.5** on Python ≥3.13, else **pygame>=2.3.0**: Display/input backend (module import remains `pygame`)
  
Optional data overlay:
- Airports overlay uses a plain JSON file of airports (identifier, lat, lon); see `sample_data/airports.json` for a ready-to-use subset around MA/NH/RI/CT.

### Installation

1. **Clone the repository**:
```bash
git clone https://github.com/ChrisPatten/pocket-scope.git
cd pocket-scope
```

2. **Create virtual environment**:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. **Install dependencies**:
```bash
pip install -e ".[dev]"
```

4. **Install pre-commit hooks**:
```bash
pre-commit install
```

### Command Line Interface

PocketScope provides a CLI interface for various operations:

```bash
# Run the main application
pocketscope

# Show version information
pocketscope --version

# Get help on available commands
pocketscope --help
```

### Development Tools

The project includes comprehensive development tooling configured in `pyproject.toml`:

- **Code Formatting**: Black (88 char line length), isort (Black profile)
- **Linting**: Ruff with auto-fix capabilities
- **Type Checking**: MyPy with strict mode enabled
- **Testing**: pytest with asyncio and Hypothesis (property-based tests)
- **Pre-commit Hooks**: Automated quality checks on every commit

Additional tools:
- **pytest-asyncio**: Enhanced async test support
- **pre-commit**: Git hook management
- Optional graphics: `pygame`/`pygame-ce` is used by rendering tests and the Pygame backend

### Running Tests

```bash
# Run all tests
pytest

# Run specific test modules
pytest src/pocketscope/tests/core/test_events.py
pytest src/pocketscope/tests/core/test_geo_unit.py
pytest src/pocketscope/tests/core/test_geo_property.py
pytest src/pocketscope/tests/tools/test_record_replay.py
pytest tests/render/test_airports_unit.py
pytest tests/render/test_airports_golden.py

# Run with coverage
pytest --cov=src/pocketscope

# Run specific test patterns
pytest -k "test_record" -v

# Run tests by category
pytest src/pocketscope/tests/unit/        # Unit tests
pytest src/pocketscope/tests/integration/ # Integration tests
pytest src/pocketscope/tests/core/        # Core system tests
pytest src/pocketscope/tests/render/      # Rendering (golden frame) tests
```

New platform and hardware tests are included under `tests/platform/` for the ILI9341 backend and XPT2046 touch driver; see:

```bash
pytest tests/platform/test_ili9341_backend.py
pytest tests/platform/test_ili9341_chunking.py
pytest tests/platform/test_xpt2046_touch.py
```

New tests for the dump1090 JSON source live ingestion remain under `tests/ingest/`:

```bash
# Run the dump1090 JSON source tests
pytest tests/ingest/test_dump1090_json_source.py -q
```

### Test Structure

- **Unit Tests** (`tests/unit/`): Isolated component testing
- **Integration Tests** (`tests/integration/`): Multi-component interactions
- **Core Tests** (`tests/core/`): Event system and time abstraction
- **Tool Tests** (`tests/tools/`): Record/replay and utilities  
- **Rendering/Golden Frame Tests** (`tests/render/`, `tests/golden_frames/`): Deterministic visual regression tests (headless)
    - Airports overlay golden: `tests/render/test_airports_golden.py`
    - Golden render smoke updated to ensure soft key / settings overlay do not regress drawing
 - **UI / Settings Tests** (`tests/ui/`): Soft keys, settings persistence, altitude filter, north-up lock, settings screen mouse, track length trimming.

### Code Quality

```bash
# Format code
black src/
isort src/

# Lint and auto-fix
ruff check src/ --fix

# Type checking
mypy src/

# Run all quality checks
pre-commit run --all-files
```

## Configuration

PocketScope uses TOML configuration files for settings:

- **`pyproject.toml`**: Main project configuration, dependencies, and tool settings
- **`src/pocketscope/config/default.toml`**: Runtime application configuration
- **`.pre-commit-config.yaml`**: Git hook configuration for code quality

Key configuration sections:
- Build system and dependencies
- Code quality tool settings (Black, Ruff, MyPy)
- Test configuration (pytest)
- Package metadata and scripts

## Usage Examples

### ADS-B File Playback
```python
import asyncio
from pocketscope.core.events import EventBus, unpack
from pocketscope.core.time import SimTimeSource
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource

async def main():
    bus = EventBus()
    ts = SimTimeSource(start=0.0)
    
    # Subscribe to ADS-B messages
    adsb_sub = bus.subscribe("adsb.msg")
    
    async def process_aircraft():
        async for envelope in adsb_sub:
            aircraft = unpack(envelope.payload)
            print(f"Aircraft {aircraft['icao24']} at ({aircraft['lat']}, {aircraft['lon']})")
    
    # Start ADS-B playback from file
    source = FilePlaybackSource("aircraft_trace.jsonl", ts=ts, bus=bus, speed=2.0)
    processor = asyncio.create_task(process_aircraft())
    playback = asyncio.create_task(source.run())
    
    # Advance simulation time to process events
    while True:
        next_due = source.next_due_monotonic()
        if next_due is None:
            break
        ts.set_time(next_due)
        await asyncio.sleep(0)  # Process events
    
    await playback
    await bus.close()
    await processor

if __name__ == "__main__":
    asyncio.run(main())
```

### Basic Event Processing
```python
import asyncio
from pocketscope.core.events import EventBus, pack, unpack
from pocketscope.core.time import RealTimeSource

async def main():
    bus = EventBus()
    ts = RealTimeSource()
    
    # Subscribe to aircraft data
    adsb_sub = bus.subscribe("adsb.decoded")
    
    async def process_aircraft():
        async for envelope in adsb_sub:
            aircraft = unpack(envelope.payload)
            print(f"Aircraft {aircraft['icao']} at {aircraft['position']}")
    
    # Start processing
    processor = asyncio.create_task(process_aircraft())
    
    # Simulate publishing aircraft data
    await bus.publish("adsb.decoded", pack({
        "icao": "A12345",
        "callsign": "UAL123",
        "position": {"lat": 37.7749, "lon": -122.4194},
        "altitude": 35000
    }))
    
    await asyncio.sleep(0.1)  # Let processor handle event
    await bus.close()
    await processor

if __name__ == "__main__":
    asyncio.run(main())
```

### Testing with Deterministic Time
```python
import asyncio
import pytest
from pocketscope.core.events import EventBus, pack
from pocketscope.core.time import SimTimeSource

@pytest.mark.asyncio
async def test_timed_processing():
    bus = EventBus()
    ts = SimTimeSource(start=0.0)
    
    received_times = []
    sub = bus.subscribe("test.topic")
    
    async def collector():
        async for env in sub:
            received_times.append(ts.monotonic())
    
    task = asyncio.create_task(collector())
    
    # Publish events at specific times
    ts.set_time(1.0)
    await bus.publish("test.topic", pack("event1"))
    
    ts.set_time(2.5)
    await bus.publish("test.topic", pack("event2"))
    
    await asyncio.sleep(0)  # Process events
    await bus.close()
    await task
    
    assert received_times == [1.0, 2.5]
```

### Live Viewer (Desktop / Web / Embedded TFT)
Unified viewer supports a desktop window (Pygame), SPI TFT (`--tft`), or Web UI (`--web-ui`).

Module: `src/pocketscope/examples/live_view.py`

Usage (desktop window):

```bash
# Basic: connect to local dump1090 and show a 60 NM PPI centered at 42.0,-71.0
python -m pocketscope.examples.live_view \
    --url http://127.0.0.1:8080/data/aircraft.json \
    --center 42.0,-71.0 \
    --range 60

# Show minimal one-line labels instead of full data blocks
python -m pocketscope.examples.live_view --simple

# Tweak data block typography (font size and line gap)
python -m pocketscope.examples.live_view --font-px 12 --block-line-gap-px -5

# Enable airports overlay using a bundled sample list (auto-detected if present)
python -m pocketscope.examples.live_view --airports sample_data/airports.json

# Enable sector polygon overlays (auto-detected if present)
python -m pocketscope.examples.live_view --sectors sample_data/artcc.json

# Local JSONL playback (overrides --url) and loops
python -m pocketscope.examples.live_view --playback tests/data/adsb_trace_airports.jsonl

# Run with persistent settings + soft keys + settings screen
python -m pocketscope.examples.live_view --url http://127.0.0.1:8080/data/aircraft.json --range 20 --center 42.0,-71.0
        # Interact via soft keys (click/tap or touch) or press 's' for Settings. Settings stored in ~/.pocketscope/settings.json

# Serve minimal web UI (opens WebSocket server)
python -m pocketscope.examples.live_view --web-ui --url http://127.0.0.1:8080/data/aircraft.json

# Embedded SPI TFT (ILI9341) + touch (XPT2046)
python -m pocketscope.examples.live_view --tft --url http://127.0.0.1:8080/data/aircraft.json --range 20 --center 42.0,-71.0 --touch-hz 180

# Local playback looping on TFT
python -m pocketscope.examples.live_view --tft --playback tests/data/adsb_trace_airports.jsonl --range 20

New flags:
    --tft        Use SPI TFT (ILI9341) + touch backend
    --touch-hz   Touch poll frequency (default 180.0)
    --web-ui     Serve Web UI instead of opening a Pygame window
```

Notes:
- Requires a GUI-capable environment; if the window doesn't appear, check `SDL_VIDEODRIVER` and system display settings.
- Rendering remains deterministic in headless tests; the viewer explicitly requests a visible window.

### Airports Overlay

- Layer: `src/pocketscope/render/airports_layer.py`
- Data helper: `src/pocketscope/data/airports.py` provides:
    - `load_airports_json(path) -> list[Airport]` to load `{identifier, lat, lon}` arrays
    - `nearest_airports(lat, lon, airports, max_nm=50.0, k=3)` for simple nearest selection using haversine distance
- Live viewer flags:
        - `--airports PATH` to specify an airports file (defaults to `sample_data/airports.json` when present)
- Rendering rules:
    - 5x5 px square markers (dim gray), ident labels in white, clamped on-screen
    - Range-based culling at current PPI range

### Sector Polygons Overlay

- Layer: `src/pocketscope/render/sectors_layer.py`
- Data helper: `src/pocketscope/data/sectors.py` provides:
    - `load_sectors_json(path) -> list[Sector]` to load sector polygon data from JSON or GeoJSON
    - Support for both simple JSON format (`{name, points: [{lat, lon}]}`) and GeoJSON FeatureCollection
    - Automatic format detection and robust parsing with error handling
- Live viewer flags:
    - `--sectors PATH` to specify a sectors file (defaults to `sample_data/artcc.json` when present)
- Rendering rules:
    - Translucent polygon outlines with configurable color and transparency
    - Uses ENU local tangent plane projection for accurate rendering
    - Efficient clipping and range-based rendering optimization

### UI and Controls

- Module: `src/pocketscope/ui/controllers.py` provides an interactive `UiController` with a status overlay (`ui/status_overlay.py`).
- Key bindings (when using the live viewer with pygame window):
    - `[` or `-`: zoom out; `]` or `=`: zoom in; `o`: toggle overlay (airports/sectors)
    - `s`: toggle Settings screen; `u`: cycle units; `t`: cycle track length; `d`: toggle demo mode
    - Arrow Left / Right: rotate when north-up lock disabled; `q` / `ESC`: quit; mouse wheel: zoom in/out.
    - Target FPS and range ladder are configurable via `UiConfig`.

#### Settings Screen Fields

| Field | Description | Persistence |
|-------|-------------|-------------|
| Units | Cycle nm/ft/kt → mi/ft/mph → km/m/kmh | Debounced save |
| Range Default | Default zoom ladder step (2–80 NM) | Debounced save |
| Track Length | Trail retention preset (15s / 45s / 120s; pinned = next tier) | Debounced save |
| Altitude Filter | Visibility band (All, 0–5k, 5–10k, 10–20k, >20k) | Debounced save |
| Demo Mode | Loop JSONL trace + DEMO badge + temporary recenter | Debounced save |
| North-up Lock | Enforce rotation 0°; unlock enables arrow rotation | Debounced save |

Custom altitude filter bounds (user-defined min/max) are supported and tested (`tests/ui/test_altitude_filter_custom_bounds.py`).

## File Formats

### ADS-B Trace Format (JSONL)
ADS-B trace files use JSONL format (one JSON object per line) with the following schema:
```json
{
  "t_mono": 12.345,
    "msg": {
        "icao24": "abc123",
        "callsign": "UAL123",
        "lat": 40.7128,
        "lon": -74.0060,
        "baro_alt": 35000,
        "ground_speed": 450,
        "track_deg": 270,
        "src": "PLAYBACK"
    }
}
```

- `t_mono`: Monotonic timestamp in seconds (relative to trace start)
- `msg`: ADS-B message object with aircraft data
  - `icao24`: 24-bit ICAO aircraft identifier (required)
  - `callsign`: Aircraft callsign (optional)
  - `lat`/`lon`: Latitude/longitude in decimal degrees (optional)
  - `baro_alt`: Barometric altitude in feet (optional)
  - `ground_speed`: Ground speed in knots (optional)
  - `track_deg`: Track angle in degrees (optional)
  - `src`: Data source identifier (optional)

### JSONL Event Format
Events are recorded in JSONL format with the following schema:
```json
{
  "topic": "adsb.raw",
  "t_mono": 12.345,
  "t_wall": 1693333333.123,
  "payload_b64": "eyJpY2FvIjoiQTEyMzQ1In0="
}
```

- `topic`: Event topic string
- `t_mono`: Monotonic timestamp (seconds)
- `t_wall`: Wall clock timestamp (Unix epoch)
- `payload_b64`: Base64-encoded message payload

## Contributing

### Contributing Guidelines

For detailed architectural principles and coding standards, see `.github/copilot_instructions.md` which contains:
- Clean Architecture + Ports & Adapters principles
- Performance requirements (≥5 FPS with 20 aircraft)
- Module boundaries and interfaces
- Security and privacy considerations

### Development Workflow

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes with comprehensive tests
4. Run quality checks: `pre-commit run --all-files`
5. Ensure all tests pass: `pytest`
6. Commit your changes: `git commit -m 'Add amazing feature'`
7. Push to branch: `git push origin feature/amazing-feature`
8. Open a Pull Request

### Code Style

- Follow PEP 8 (enforced by Black)
- Use type hints (checked by MyPy)
- Write comprehensive tests
- Document public APIs with docstrings
- Keep functions focused and testable

## License

MIT License - see LICENSE file for details.

## Project Status

**Current State**: Core infrastructure and ADS-B ingestion complete
- ✅ Event bus with backpressure handling
- ✅ Time abstraction for deterministic testing
- ✅ Record/replay system with JSONL format
- ✅ ADS-B file playback with deterministic timing
- ✅ Live ADS-B source polling dump1090 `aircraft.json` with backoff and conditional requests
- ✅ WGS‑84 geodesy helpers with deterministic unit and property tests
- ✅ Rendering/UI foundation: Canvas API, Pygame + SPI TFT + Web display/input backends
- ✅ PPI view with rings, ownship, trails, and labels
- ✅ ATC-style data blocks with leader lines and collision avoidance
- ✅ Airports overlay with range-based culling and auto-detection
- ✅ Sector polygon overlays with JSON/GeoJSON support and transparency
- ✅ Interactive UI controls (zoom, overlay toggle, keyboard/mouse input)
 - ✅ Soft key bar with autoscaling labels
 - ✅ Persistent JSON settings (units, range, track length, demo mode, altitude filter, north-up lock)
 - ✅ Settings screen overlay (staged edits + Back/Save softkeys)
 - ✅ Altitude filter banding & snapshot filtering
 - ✅ North-up lock & view rotation (arrow keys when unlocked)
 - ✅ Demo mode with looping JSONL playback + center override & badge
- ✅ Deterministic golden-frame rendering test (headless, pinned SHA-256)
- ✅ Comprehensive test suite
- ✅ Development tooling and quality checks
- ✅ Live viewer (desktop/web/embedded) to visualize real traffic
- ✅ Embedded hardware drivers (ILI9341 display + XPT2046 touch) with tests
- ✅ Label layout prevents overlap with aircraft glyphs
- ✅ Runtime config module (`config.py`) merges persisted settings + CLI overrides

**Next Steps**:
- Additional live ADS-B formats (SBS, Beast)
- GPS/IMU integration
- Further web UI expansion (controls, overlays)
- Enhanced theme customization & night mode

## Notes on Environment and Determinism

- For Python 3.13+, install `pygame-ce` (imported as `pygame`); for older versions, install `pygame`. SPI TFT + touch drivers are optional and safely no-op on systems without `spidev`/`RPi.GPIO`.
- Headless tests use `SDL_VIDEODRIVER=dummy` set before importing the Pygame backend.
- Type checking: MyPy is in strict mode; missing `pygame.*` stubs are ignored via a project override to keep CI green.
