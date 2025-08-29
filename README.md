# PocketScope

PocketScope is a handheld Pi-powered ATC-style scope for decoding and displaying ADS-B traffic. Built with Python, it features a modular, event-driven architecture designed for real-time sensor data processing, deterministic testing, and rapid prototyping.

## Features

### Core Architecture
- **Event-Driven System**: Async EventBus with bounded queues and backpressure handling
- **Time Abstraction**: Deterministic testing support with SimTimeSource and RealTimeSource
- **Modular Design**: Clean separation between ingestion, processing, and visualization

### Data Sources
- **ADS-B**: Aircraft transponder data decoding (SBS, Beast, JSON formats)
- **GPS**: Position and navigation data (NMEA serial)
- **IMU**: Inertial measurement unit integration (9-axis sensors)

### Infrastructure
- **Record/Replay System**: JSONL-based event recording and deterministic replay
- **Platform Abstraction**: Display, input, and I/O abstraction layers
- **Layered Rendering**: Composable visualization pipeline
- **Comprehensive Testing**: Full test suite with async event testing

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

### Project Structure

```
src/pocketscope/
├── __main__.py             # CLI entry point
├── config/                 # Configuration files
│   └── default.toml        # Default configuration
├── core/                   # Core event and time systems
│   ├── events.py           # EventBus implementation
│   ├── time.py             # Time abstraction (Real/Sim)
│   ├── models.py           # Pydantic data models
│   └── domain/             # Domain logic (planned)
├── ingest/                 # Data ingestion modules
│   ├── adsb/               # ADS-B transponder data
│   ├── gps/                # GPS/GNSS data
│   └── imu/                # Inertial measurement data
├── platform/               # Hardware abstraction
│   ├── display/            # Display drivers
│   ├── input/              # Input handling
│   └── io/                 # I/O interfaces
├── render/                 # Visualization pipeline
│   ├── canvas.py           # Drawing primitives
│   ├── view_ppi.py         # Plan Position Indicator view
│   └── layers/             # Composable render layers
├── tools/                  # Development and debugging tools
│   └── record_replay.py    # Event recording/replay
├── tests/                  # Comprehensive test suite
│   ├── core/               # Core system tests
│   ├── data/               # Test data files
│   ├── golden_frames/      # Visual regression tests
│   ├── integration/        # Integration tests
│   ├── tools/              # Tool tests
│   └── unit/               # Unit tests
└── ui/                     # User interface components
    ├── controllers.py      # UI controllers
    └── softkeys.py         # Soft key handling
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
- **Testing**: pytest with asyncio support
- **Pre-commit Hooks**: Automated quality checks on every commit

Additional tools:
- **pytest-asyncio**: Enhanced async test support
- **pre-commit**: Git hook management

### Running Tests

```bash
# Run all tests
pytest

# Run specific test modules
pytest src/pocketscope/tests/core/test_events.py
pytest src/pocketscope/tests/tools/test_record_replay.py

# Run with coverage
pytest --cov=src/pocketscope

# Run specific test patterns
pytest -k "test_record" -v

# Run tests by category
pytest src/pocketscope/tests/unit/        # Unit tests
pytest src/pocketscope/tests/integration/ # Integration tests
pytest src/pocketscope/tests/core/        # Core system tests
```

### Test Structure

- **Unit Tests** (`tests/unit/`): Isolated component testing
- **Integration Tests** (`tests/integration/`): Multi-component interactions
- **Core Tests** (`tests/core/`): Event system and time abstraction
- **Tool Tests** (`tests/tools/`): Record/replay and utilities  
- **Golden Frame Tests** (`tests/golden_frames/`): Visual regression testing (planned)
- **Test Data** (`tests/data/`): Sample data files for testing

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

## File Formats

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

**Current State**: Core infrastructure complete
- ✅ Event bus with backpressure handling
- ✅ Time abstraction for deterministic testing
- ✅ Record/replay system with JSONL format
- ✅ Comprehensive test suite
- ✅ Development tooling and quality checks

**Next Steps**:
- ADS-B decoder implementation
- GPS/IMU integration
- Display rendering pipeline
- Hardware platform drivers
- User interface components
