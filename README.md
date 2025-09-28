# PocketScope

<!-- Badges -->
[![Version](https://img.shields.io/github/v/tag/ChrisPatten/pocket-scope?label=version)](https://github.com/ChrisPatten/pocket-scope/tags)
[![Release](https://img.shields.io/github/v/release/ChrisPatten/pocket-scope)](https://github.com/ChrisPatten/pocket-scope/releases)
<!-- Uncomment once published to PyPI: -->
<!-- [![PyPI](https://img.shields.io/pypi/v/pocketscope)](https://pypi.org/project/pocketscope/) -->

**Current Version:** 0.1.2

PocketScope is a handheld, Pi-powered ATC-style scope for decoding and displaying ADS-B traffic. The application is written in Python and designed for real-time sensor data processing, deterministic testing, and rapid prototyping.

- Event-driven architecture with deterministic simulation time
- Modular ingestion, processing, and rendering pipelines
- Multiple display backends (desktop, SPI TFT, web)
- Persistent settings with live theme reload and screenshot automation

Comprehensive architecture and feature documentation now lives in [`docs/overview.md`](docs/overview.md) along with the rest of the [documentation set](#documentation).

## Getting Started

### Prerequisites
- Python 3.11+
- Git
- Recommended: a virtual environment for local development

### Installation

```bash
git clone https://github.com/ChrisPatten/pocket-scope.git
cd pocket-scope
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

### Running the Application

PocketScope exposes a CLI entry point once installed in editable mode:

```bash
pocketscope --help                      # Discover available options
pocketscope --version                   # Show version information
pocketscope \
    --url http://127.0.0.1:8080/data/aircraft.json \
    --center 42.0,-71.0 \
    --range 60                          # Live ADS-B ingest
pocketscope --playback sample_data/demo_adsb.jsonl --loop
```

If you prefer not to install the console script, run the module directly:

```bash
python -m pocketscope [options]
```

### Configuration

The application stores user-facing settings at `~/.pocketscope/settings.json` (override with `POCKETSCOPE_HOME`). Edits are validated and reloaded live by the running UI. Settings include units, default range, track length preset, demo mode, altitude filter bounds, north-up lock, and theme overrides.

Sample data for airports, sectors, and ADS-B playback lives in `sample_data/`. Copy or replace these files with your own datasets as needed.

## Development Workflow

PocketScope uses standard Python tooling for quality and automation:

```bash
pytest                                   # Run the full test suite
pytest -k "record" -v                    # Filtered test run
pytest --cov=src/pocketscope             # Coverage report
ruff check .                             # Linting
black .                                  # Formatting
```

CI-equivalent checks are configured through pre-commit hooks and `pyproject.toml`.

## Contributing

1. Fork and clone the repository.
2. Create a feature branch: `git checkout -b feature/my-improvement`.
3. Install dependencies with `pip install -e ".[dev]"` and enable pre-commit hooks.
4. Make your changes, run tests, and open a pull request.

Bug reports and feature discussions are welcome via GitHub issues. Please reference relevant documentation and tests when filing or reviewing changes.

## Documentation

- [Architecture overview](docs/overview.md)
- [Event bus](docs/event-bus.md)
- [Time & simulation](docs/time-and-simulation.md)
- [Data ingestion](docs/data-ingestion.md)
- [Track management service](docs/track-service.md)
- [Rendering & display](docs/rendering.md)
- [Settings & soft keys](docs/settings-and-softkeys.md)
- [Recording & replay](docs/recording-and-replay.md)
- [ADS-B data flow](docs/adsb-data-flow.md)
- [Spatial utilities and GeoJSON ingestion](docs/spatial.md)
- [Theme system reference](docs/theming.md)
- [Screenshot automation](docs/screenshots.md)
- [Systemd setup guide](docs/systemd-setup.md)
- [Release notes](docs/releases/)
- [Pull request guidelines](docs/pr/)

## License

PocketScope is distributed under the MIT License. See [LICENSE](LICENSE) for details.
