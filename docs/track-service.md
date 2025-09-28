# Track Management Service

Aircraft tracking logic lives in `src/pocketscope/core/tracks.py`. It consumes normalised ADS-B messages and maintains the world state rendered by the UI.

## Responsibilities

- **Track Lifecycle** – Create, update, or expire aircraft entities based on incoming messages and timeouts.
- **History Buffers** – Maintain ring buffers of recent positions for each aircraft, enabling trail rendering without unbounded memory growth.
- **Quality Metrics** – Track message age, signal quality, and data completeness to drive UI highlighting.
- **Pinning & Filters** – Honour user preferences for pinning aircraft or filtering altitude ranges.

## Update Flow

1. ADS-B ingestors publish `AdsbMessage` events on the bus.
2. The track service subscribes to those topics and converts them into internal `TrackState` objects.
3. Each update mutates the existing track (or creates one), updates the history buffer, and marks fields that changed since the last frame.
4. Periodic maintenance passes remove stale tracks and prune history buffers using the injected time source.

## Integrating with Rendering

Renderer layers subscribe to track updates to draw glyphs, data blocks, and trails. Because the track service exposes read-only views of its state, renderers can safely consume snapshots without introducing race conditions.

When extending the UI with new overlays (for example, conflict detection or approach sequencing), hook into the track service rather than duplicating tracking logic.

## Testing Strategies

- Use `SimTimeSource` to advance the clock and assert that tracks expire as expected.
- Feed recorded JSONL messages through the record/replay utilities to reproduce edge cases.
- Validate history buffer behaviour by publishing repeated updates and inspecting the emitted trail points.

Keeping the tracking logic isolated to a single service ensures deterministic behaviour and simplifies the renderer pipeline.
