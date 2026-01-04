# Data Ingestion

PocketScope ingests live sensor feeds and recorded datasets into the event bus. Ingestion modules live under `src/pocketscope/ingest/` and normalise raw data into the models defined in `src/pocketscope/core/models.py`.

## ADS-B Sources

### Live dump1090 Polling

`ingest/adsb/json_source.py` polls a dump1090-compatible endpoint (typically `http://host/data/aircraft.json`). Key behaviours:

### Local dump1090 JSON file

`ingest/adsb/file_source.py` polls a local dump1090-style `aircraft.json` written by another service.

- Enable with `--file /path/to/aircraft.json` (takes precedence over `--url`, lower than `--playback`).
- Polls the file at 1 Hz by default, republishing updates without HTTP overhead.
- Shares payload validation and event publication logic with the live JSON source.
- Logs and retries with backoff if the file is missing or unreadable.

### Live dump1090 SBS (TCP 30003)

Some dump1090 setups also expose an SBS-1 style text feed on TCP port
30003. This feed emits newline-delimited ASCII messages (SBS-1) that
contain individual aircraft state updates. PocketScope may optionally
connect to such a feed as an `AdsbSource` implementation that parses
SBS lines and publishes the same `AdsbMessage` models as the JSON
polling source.

Suggested configuration / usage:

- CLI: add an option to select the `SBS` source and provide host:port
	(for example `--adsb-src SBS --adsb-sbs-host 192.168.1.2 --adsb-sbs-port 30003`).
- Environment:
	- `DUMP1090_SBS_HOST` — host serving the SBS feed (default: `localhost`).
	- `DUMP1090_SBS_PORT` — TCP port (default: `30003`).
	- `DUMP1090_SBS_RECONNECT_S` — reconnect backoff base in seconds.

Notes and behaviour:

- The SBS TCP connection should be read line-by-line and tolerant of
	partial/incomplete lines. Implementations must not block the event
	loop for long reads; prefer a small reader task or thread pool for
	parsing.  
- When both JSON polling and SBS TCP are available users should pick
	one to avoid duplicate messages; the ingestion layer does not dedupe
	across sources by default.
- Keep the connection local by default (operate offline) unless an
	explicit remote host is configured.

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
