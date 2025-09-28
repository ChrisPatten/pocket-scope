# Event Bus

PocketScope components communicate through an asynchronous event bus located at `src/pocketscope/core/events.py`. The bus is responsible for decoupling producers and consumers, enforcing backpressure, and providing deterministic shutdown semantics so long-running tasks can exit cleanly.

## Concepts

- **Topics** – Named channels (for example, `"adsb.raw"` or `"tracks.updates"`) that group related events.
- **Envelopes** – Each published message is wrapped with metadata (`topic`, timestamp, sender id, and payload bytes) for consistent logging and replay.
- **Bounded Queues** – Every topic has a configurable queue depth. When the queue is full the bus drops the oldest message, favouring fresh data over stale samples.
- **Async Iteration** – Subscribers consume events with `async for`, which keeps processing loops concise and cancellation-friendly.
- **Sentinel Close** – When the bus shuts down it injects sentinel envelopes so subscribers can exit without polling flags.

## Typical Usage

```python
from pocketscope.core.events import EventBus, pack, unpack

bus = EventBus(default_maxsize=256)
subscriber = bus.subscribe("adsb.raw")

async def producer() -> None:
    await bus.publish("adsb.raw", pack({"icao": "ABCD12", "alt_ft": 35000}))
    await bus.close()

async def consumer() -> None:
    async for envelope in subscriber:
        message = unpack(envelope.payload)
        print(f"{envelope.topic}: {message}")
```

## Extending the Bus

- **Custom Serialisation** – `pack` and `unpack` wrap `msgpack`. Swap them for another codec if you need human-readable payloads or encryption.
- **Metrics & Logging** – Each `publish` call surfaces queue depth and timing data. Attach observers to the bus to collect telemetry or drop alerts.
- **Bridging** – Because topics are independent, you can bridge selected topics to WebSockets, MQTT, or other transports without modifying producers.

The event bus is central to the architecture; almost every other subsystem accepts a `EventBus` instance during construction so it can publish or subscribe to the relevant topics.
