# ADS-B Data Flow Documentation

This document describes the complete data flow from triggering an outbound HTTP request to receiving ADS-B messages on the event bus in PocketScope.

## Overview

The PocketScope application processes ADS-B (Automatic Dependent Surveillance-Broadcast) data through a multi-stage pipeline:

1. **Request Trigger** - Application startup or periodic polling
2. **HTTP Request** - Outbound HTTP request to dump1090 server
3. **Response Processing** - JSON parsing and data validation
4. **Message Creation** - Converting to standardized AdsbMessage objects
5. **Event Publishing** - Publishing to EventBus for distribution
6. **Track Processing** - Converting messages to aircraft tracks
7. **UI Updates** - Rendering tracks on display

## Detailed Data Flow

### 1. Application Startup and Request Triggering

**Entry Point**: `src/pocketscope/examples/live_view.py`

The application begins when the user runs:
```bash
python -m pocketscope.examples.live_view --url https://adsb.chrispatten.dev/data/aircraft.json --center 42.00748,-71.20899 --sectors ./sample_data/us_states.json --tft
```

**Flow**:
1. **Argument Parsing** (`parse_args()`)
   - Processes command-line arguments including URL, center coordinates, display mode
   - Validates input parameters

2. **Component Initialization** (`main_async()`)
   ```python
   # Core components
   ts = RealTimeSource()           # Time source for real-time operation
   bus = EventBus()                # Central message bus for component communication
   tracks = TrackService(bus, ts)  # Track management service
   
   # ADS-B source selection
   if args.playback:
       src = FilePlaybackSource(...)  # For recorded data playback
   else:
       src = Dump1090JsonSource(args.url, bus=bus, poll_hz=1.0)  # Live data
   ```

3. **Task Orchestration**
   - TrackService started: `await tracks.run()`
   - Source and UI tasks created and run concurrently
   - Error handling ensures graceful shutdown

### 2. HTTP Request Initiation

**Component**: `Dump1090JsonSource` in `src/pocketscope/ingest/adsb/json_source.py`

**Trigger Mechanism**:
- **Periodic Polling**: Timer-based requests at specified intervals (default 1Hz)
- **Exponential Backoff**: Failed requests trigger progressive delays
- **Conditional Requests**: ETag/If-Modified-Since headers avoid redundant downloads

**Request Process**:
```python
async def run(self) -> None:
    # Session setup with timeout and SSL configuration
    if self._ext_session is not None:
        self._session = self._ext_session
    else:
        timeout = aiohttp.ClientTimeout(total=self._timeout_s, connect=self._connect_timeout_s)
        connector = aiohttp.TCPConnector(ssl=self._verify_tls)
        self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    
    # Main polling loop
    while not self._stop_event.is_set():
        try:
            await self._poll_once()  # Single HTTP request/response cycle
            # Reset backoff on success
        except Exception as e:
            # Exponential backoff on errors
            await asyncio.sleep(backoff)
            backoff = min(max_backoff, backoff * 2.0)
```

### 3. Outbound HTTP Request Details

**Request Configuration**:
- **URL**: User-specified dump1090 endpoint (e.g., `https://adsb.chrispatten.dev/data/aircraft.json`)
- **Method**: GET
- **Headers**: 
  - `If-None-Match`: ETag from previous response (304 optimization)
  - `If-Modified-Since`: Last-Modified timestamp (304 optimization)
- **Timeout**: Default 3.0 seconds (configurable via `DUMP1090_TIMEOUT_S`)
- **TLS Verification**: Enabled by default (configurable via `DUMP1090_VERIFY_TLS`)

**Network Diagnostics**:
```python
# DNS resolution logging (first request only)
if not self._dns_logged and self._url.startswith("http"):
    infos = await asyncio.get_running_loop().getaddrinfo(host, port)
    addrs = sorted({ai[4][0] for ai in infos})
    logger.debug("dump1090 DNS %s -> %s", host, addrs)
```

**Error Scenarios**:
- **TimeoutError**: Request exceeds configured timeout
- **Connection Errors**: Network unreachable, DNS failure, refused connection
- **HTTP Errors**: 4xx/5xx status codes from server
- **JSON Parse Errors**: Malformed response content

### 4. HTTP Response Processing

**Response Handling** (`_poll_once()` method):

```python
async with self._session.get(self._url, headers=headers) as resp:
    if resp.status == 304:  # Not Modified
        return False
    resp.raise_for_status()  # Raise on 4xx/5xx
    
    # Cache headers for next request
    etag = resp.headers.get("ETag")
    if etag:
        self._cache.etag = etag
    
    # Read and parse JSON
    text = await resp.text()
    data = json.loads(text)  # May raise JSONDecodeError
    
    self._handle_payload(data)  # Process aircraft data
    return True
```

**Performance Monitoring**:
- Request timing logged if > 80% of timeout
- Response size tracking
- Consecutive error counting with periodic alerts

### 5. JSON Data Processing and Message Creation

**Payload Structure** (dump1090 aircraft.json format):
```json
{
  "now": 1672531200.0,
  "messages": 12345,
  "aircraft": [
    {
      "hex": "abc123",
      "flight": "UAL123  ",
      "lat": 40.7128,
      "lon": -74.0060,
      "alt_baro": 32000,
      "alt_geom": 32100,
      "gs": 450.5,
      "track": 270.0,
      "baro_rate": 0,
      "squawk": "1234",
      "seen": 0.5,
      "seen_pos": 1.2,
      "nic": 8,
      "nac_p": 9
    }
  ]
}
```

**Data Processing** (`_handle_payload()` method):

```python
def _handle_payload(self, data: dict[str, Any]) -> None:
    # Extract timestamp
    now_s = _coerce_float(data.get("now")) or time.time()
    ts = datetime.fromtimestamp(float(now_s), tz=timezone.utc)
    
    # Process each aircraft
    ac_list = data.get("aircraft", [])
    for ac in ac_list:
        # Validation and normalization
        icao = ac.get("hex")
        if not _is_valid_icao24(icao):  # Must be 6-char hex
            continue
        icao = str(icao).strip().lower()
        
        # Stale data filtering
        seen = _coerce_float(ac.get("seen")) or 0.0
        seen_pos = _coerce_float(ac.get("seen_pos")) or 0.0
        if seen > 60.0 or seen_pos > 60.0:  # Skip stale data
            continue
        
        # Field extraction and type coercion
        callsign = ac.get("flight").strip() if ac.get("flight") else None
        lat = _coerce_float(ac.get("lat"))
        lon = _coerce_float(ac.get("lon"))
        baro_alt = _coerce_float(ac.get("alt_baro"))
        # ... (additional fields)
        
        # Create normalized message
        msg = AdsbMessage(
            ts=ts,
            icao24=icao,
            callsign=callsign,
            lat=lat,
            lon=lon,
            baro_alt=baro_alt,
            # ... (all other fields)
            src="JSON"
        )
```

**Data Validation and Normalization**:
- **ICAO24**: Must be exactly 6 hexadecimal characters, converted to lowercase
- **Callsign**: Trimmed of whitespace, null if empty
- **Coordinates**: Validated as valid floats within reasonable ranges
- **Numeric Fields**: Type coercion with null handling for missing/invalid values
- **Stale Filtering**: Messages with `seen` > 60 seconds are discarded

### 6. Event Bus Publishing

**Message Serialization and Publishing**:
```python
# Convert to dictionary for serialization
msg_dict = msg.model_dump()
msg_dict["ts"] = msg.ts.isoformat()  # Convert datetime to ISO string

# Publish to event bus
asyncio.create_task(self._bus.publish(self._topic, pack(msg_dict)))
```

**EventBus Architecture** (`src/pocketscope/core/events.py`):

**Key Features**:
- **Async Message Queues**: Each subscriber gets its own bounded queue per topic
- **Backpressure Handling**: Drop-oldest policy when subscriber queues are full
- **Serialization**: msgpack for efficient binary serialization
- **Topic-Based Routing**: Messages published to specific topics (e.g., "adsb.msg")

**Publishing Process**:
```python
async def publish(self, topic: str, payload: bytes) -> None:
    async with self._lock:
        state = self._topics.get(topic)
        if not state:
            return  # No subscribers
        
        # Create envelope with timestamp
        env = Envelope(topic=topic, ts=monotonic(), payload=payload)
        
        # Deliver to all subscribers
        for queue in state.subscribers:
            try:
                queue.put_nowait(env)
                state.deliveries += 1
            except asyncio.QueueFull:
                # Drop oldest message (backpressure)
                try:
                    queue.get_nowait()
                    queue.put_nowait(env)
                    state.drops += 1
                except asyncio.QueueEmpty:
                    pass
```

### 7. Track Processing

**Component**: `TrackService` in `src/pocketscope/core/tracks.py`

**Subscription and Message Processing**:
```python
async def run(self) -> None:
    self._subscription = self._bus.subscribe(self._topic_in)  # "adsb.msg"
    self._task = asyncio.create_task(self._process_messages())
    
async def _process_messages(self) -> None:
    async for envelope in self._subscription:
        # Deserialize message
        msg_data = unpack(envelope.payload)
        msg_data["ts"] = datetime.fromisoformat(msg_data["ts"].replace("Z", "+00:00"))
        msg = AdsbMessage.model_validate(msg_data)
        
        # Process the message
        await self._process_adsb_message(msg)
```

**Track State Management**:
```python
async def _process_adsb_message(self, msg: AdsbMessage) -> None:
    icao24 = msg.icao24
    
    # Get or create track
    track = self._tracks.get(icao24)
    if track is None:
        track = AircraftTrack(
            icao24=icao24,
            last_ts=msg.ts,
            callsign=msg.callsign,
        )
        self._tracks[icao24] = track
    
    # Update track state
    track.last_ts = msg.ts
    if msg.callsign:
        track.callsign = msg.callsign
    
    # Update state fields (preserve existing values)
    if msg.ground_speed is not None:
        track.state["ground_speed"] = msg.ground_speed
    # ... (other fields)
    
    # Add position trail point (1Hz sampling)
    if msg.lat is not None and msg.lon is not None:
        last_pos_ts = self._last_position_ts.get(icao24, -1.0)
        if msg.ts.timestamp() - last_pos_ts >= 0.9:  # 1Hz rule
            alt_ft = msg.baro_alt or msg.geo_alt
            point = (msg.ts, msg.lat, msg.lon, alt_ft)
            track.history.append(point)
            self._last_position_ts[icao24] = msg.ts.timestamp()
            self._trim_trail(track, msg.ts.timestamp())
```

**Track Management Features**:
- **Trail History**: Ring buffer of position points with configurable retention
- **State Preservation**: Incremental updates preserve existing field values
- **Expiry Management**: Automatic cleanup of stale tracks
- **Pinning**: Extended trail retention for selected aircraft
- **1Hz Sampling**: Position updates limited to ~1 second intervals

### 8. Error Handling and Recovery

**Network Error Recovery**:
- **Exponential Backoff**: 0.2s → 0.4s → 0.8s → 1.6s → 2.0s (capped)
- **Connection Recovery**: Automatic retry with backoff reset on success
- **DNS Diagnostics**: IPv4 forcing for problematic networks
- **Timeout Tuning**: Configurable via environment variables

**Data Error Handling**:
- **JSON Parse Errors**: Logged and skipped, polling continues
- **Invalid Aircraft Data**: Individual aircraft skipped, others processed
- **Message Processing Errors**: Tracked but don't stop the pipeline

**Graceful Degradation**:
- **Partial Data**: Missing fields handled gracefully with null values
- **Stale Data**: Filtered out based on `seen`/`seen_pos` timestamps
- **Out-of-Order Messages**: Detected and handled appropriately

## Configuration Options

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DUMP1090_TIMEOUT_S` | 3.0 | HTTP request timeout |
| `DUMP1090_CONNECT_TIMEOUT_S` | - | TCP connection timeout |
| `DUMP1090_VERIFY_TLS` | true | TLS certificate verification |
| `DUMP1090_FORCE_IPV4` | false | Force IPv4 connections |
| `DUMP1090_BASE_URL` | - | Base URL for relative paths |

### Runtime Parameters

- **Poll Rate**: `poll_hz` parameter (default 1.0 Hz)
- **Event Bus**: Topic names and queue sizes
- **Track Service**: Trail lengths, expiry times, sampling rates

## Troubleshooting Common Issues

### Timeout Errors (as seen in your Raspberry Pi issue)

**Symptoms**:
```
dump1090 poll error (url=https://adsb.chrispatten.dev/data/aircraft.json, verify_tls=True)
TimeoutError
```

**Possible Causes**:
1. **Network Latency**: High latency to remote server
2. **DNS Resolution**: Slow DNS lookup times
3. **TLS Handshake**: Slow certificate validation
4. **Server Load**: Remote server responding slowly

**Solutions**:
```bash
# Increase timeout
export DUMP1090_TIMEOUT_S=10.0

# Disable TLS verification (if using HTTPS)
export DUMP1090_VERIFY_TLS=false

# Force IPv4 (if IPv6 causing issues)
export DUMP1090_FORCE_IPV4=true

# Use local dump1090 instance instead
python -m pocketscope.examples.live_view --url http://127.0.0.1:8080/data/aircraft.json
```

### Connection Issues

**Network Diagnostics**:
- Check connectivity: `ping adsb.chrispatten.dev`
- Test HTTP directly: `curl -v https://adsb.chrispatten.dev/data/aircraft.json`
- Monitor DNS: Application logs DNS resolution results

**Alternative Data Sources**:
- Local dump1090: `http://127.0.0.1:8080/data/aircraft.json`
- Playback mode: `--playback sample_data/demo_adsb.jsonl`

## Performance Considerations

### Memory Usage
- EventBus queues: Default 1024 messages per subscriber per topic
- Track history: 60-180 seconds of position data per aircraft
- JSON parsing: Temporary memory for full aircraft.json response

### CPU Usage
- JSON parsing: Proportional to aircraft count and update rate
- Coordinate transformations: Per-aircraft per-update for display
- Event bus overhead: Minimal for typical message volumes

### Network Usage
- Baseline: ~1KB per request (empty aircraft list)
- Scaling: ~100-200 bytes per aircraft in response
- Optimization: ETag/If-Modified-Since headers reduce redundant transfers

## Related Components

- **Display Backends**: Pygame, ILI9341 TFT, Web UI
- **Input Handling**: Touch, keyboard, mouse events
- **Rendering Pipeline**: PPI view, data blocks, trails
- **Data Sources**: Alternative to FilePlaybackSource
- **Configuration Management**: Settings persistence and hot-reload
