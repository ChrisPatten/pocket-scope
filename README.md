# PocketScope

<!-- Badges -->
[![Version](https://img.shields.io/github/v/tag/ChrisPatten/pocket-scope?label=version)](https://github.com/ChrisPatten/pocket-scope/tags)
[![Release](https://img.shields.io/github/v/release/ChrisPatten/pocket-scope)](https://github.com/ChrisPatten/pocket-scope/releases)
<!-- Uncomment once published to PyPI: -->
<!-- [![PyPI](https://img.shields.io/pypi/v/pocketscope)](https://pypi.org/project/pocketscope/) -->

**Current Version:** 0.1.2

PocketScope is a handheld, Pi-powered ATC-style scope for decoding and displaying ADS-B traffic. The application is written in Python and designed for real-time sensor data processing, deterministic testing, and rapid prototyping.

Recent rendering enhancements include simplified neutral aircraft glyph fill (consistent
baseline colour for all aircraft) with lightweight climb / descent ▲ ▼ arrow indicators
appended to simple labels (shown only when vertical rate exceeds ±200 fpm). Arrow colour
follows the `ac.climb.fill` / `ac.desc.fill` palette keys while the glyph itself now
always uses `ac.level.fill` for improved visual stability.

- Event-driven architecture with deterministic simulation time
- Modular ingestion, processing, and rendering pipelines
- Multiple display backends (desktop, SPI TFT, web)
- Persistent settings with live theme reload and screenshot automation

Comprehensive architecture and feature documentation now lives in [`docs/overview.md`](docs/overview.md) along with the rest of the [documentation set](#documentation).

```bash
python -m pocketscope [options]
```

Common useful options:

- --playback sample_data/demo_adsb.jsonl  Use bundled demo trace instead of live dump1090
- --file /path/to/aircraft.json          Poll local dump1090-style JSON (bypass HTTP)
- --fps 15                                Target a different frame rate (default 30)
- --run-seconds 10                        Run for N seconds then exit (capture perf logs)
- --web-ui                                Expose simple web view (headless browser mode)
- --tft                                   Use SPI TFT + touch (embedded)
 - --adsb-src {JSON,SBS,BEAST,PLAYBACK}    Select ADS-B input source (JSON polls dump1090,
	 SBS connects to dump1090 SBS TCP:30003)

Example (collect 5 seconds of performance breakdown logs at 12 FPS):

```bash
POCKETSCOPE_LOGGING_LEVEL=INFO python -m pocketscope --playback sample_data/demo_adsb.jsonl --fps 12 --run-seconds 5 | grep 'ui.perf'
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
- [Logging & telemetry](docs/logging-and-telemetry.md)
- [Systemd setup guide](docs/systemd-setup.md)
- [ADS-B feeder setup (pm2)](docs/feeder-setup.md)
- [Release notes](docs/releases/)
- [Pull request guidelines](docs/pr/)

## License

PocketScope is distributed under the MIT License. See [LICENSE](LICENSE) for details.

