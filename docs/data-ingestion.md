# Data Ingestion

PocketScope ingests live sensor feeds and recorded datasets into the event bus. Ingestion modules live under `src/pocketscope/ingest/` and normalise raw data into the models defined in `src/pocketscope/core/models.py`.

## ADS-B Sources

### Live dump1090 Polling

`ingest/adsb/json_source.py` polls a dump1090-compatible endpoint (typically `http://host/data/aircraft.json`). Key behaviours:

- Conditional requests with `If-Modified-Since` and `ETag` headers to avoid unnecessary transfers.
- Exponential backoff when the endpoint is unavailable.
- Translation of dump1090 fields into `AdsbMessage` models, including unit conversion and identifier casing fixes.
- Publication on the `adsb.raw` topic for downstream processing.

### JSONL Playback

`ingest/adsb/playback_source.py` replays JSONL traces recorded via the built-in recorder. Features include:

- Deterministic scheduling based on the monotonic timestamps captured during recording.
- Configurable playback speed (`--rate`) and looped playback (`--loop`).
- Seamless integration with the simulated clock for unit tests.

## Position & Orientation Sensors

- **GPS** – Serial NMEA ingestion publishes `gps.position` events with geodetic coordinates.
- **IMU** – IMU handlers produce attitude and motion data for future overlays.

These modules follow the same pattern: parse raw bytes, normalise into Pydantic models, and publish onto well-known topics. Services that care about positioning can subscribe to `gps.position` or `imu.sample` without knowing the source hardware.

## Reference Data

The `sample_data/` directory ships with GeoJSON airports, ARTCC sector outlines, and ADS-B demo traces. Replace them with your own datasets to customise the overlays. The CLI accepts `--airports`, `--sectors`, and `--playback` arguments to point at alternative files.

## Adding a New Ingestor

1. Define a model (or reuse an existing one) for the normalised payload.
2. Implement an async producer that reads the sensor/feed, handles reconnection, and publishes to the event bus.
3. Register CLI options or configuration settings so users can enable the new source.
4. Write integration tests using `SimTimeSource` or JSONL playback to exercise boundary conditions.

Keeping each ingestor isolated to a single module makes it easy to support additional radios, network feeds, or simulator bridges in the future.
