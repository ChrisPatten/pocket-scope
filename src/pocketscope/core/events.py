"""Async in-process event bus with bounded per-topic queues and backpressure.

Usage example:

    bus = EventBus(default_maxsize=256)
    sub = bus.subscribe("adsb.raw")

    async def producer():
        for i in range(10):
            await bus.publish("adsb.raw", pack({"i": i}))
        await bus.close()

    async def consumer():
        async for env in sub:
            msg = unpack(env.payload)
            # process...

    # run with asyncio.gather(producer(), consumer())

Notes
-----
- Each subscriber has its own bounded asyncio.Queue per topic.
- Backpressure policy is drop-oldest on publish if a subscriber queue is full.
- Shutdown via close() signals all subscriptions to finish by sending a sentinel.
- Serialization helpers (pack/unpack) use msgpack.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any, AsyncIterator, Dict, List

import msgpack

__all__ = [
    "EventBus",
    "Subscription",
    "Envelope",
    "BusMetrics",
    "pack",
    "unpack",
]


@dataclass(slots=True)
class Envelope:
    topic: str
    ts: float
    payload: bytes


@dataclass(slots=True)
class TopicStats:
    queue_len: int
    drops: int
    publishes: int
    deliveries: int


@dataclass(slots=True)
class BusMetrics:
    topics: Dict[str, TopicStats]


_Sentinel = object()


class _TopicState:
    __slots__ = ("maxsize", "subscribers", "drops", "publishes", "deliveries")

    def __init__(self, maxsize: int) -> None:
        self.maxsize: int = max(1, int(maxsize))
        self.subscribers: List[asyncio.Queue[Envelope | object]] = []
        # metrics
        self.drops: int = 0
        self.publishes: int = 0
        self.deliveries: int = 0


class EventBus:
    """Async event bus with per-topic bounded queues and drop-oldest backpressure.

    Parameters
    ----------
    default_maxsize:
        Default queue size for new topics/subscriptions (min 1).
    """

    def __init__(self, *, default_maxsize: int = 1024) -> None:
        self._default_maxsize = max(1, int(default_maxsize))
        self._topics: Dict[str, _TopicState] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def subscribe(self, topic: str) -> "Subscription":
        """Create a subscription to a topic.

        Multiple subscribers per topic are supported; each gets its own queue.
        """
        if self._closed:
            raise RuntimeError("EventBus is closed")

        queue: asyncio.Queue[Envelope | object]
        queue = asyncio.Queue()

        async def _register() -> None:
            async with self._lock:
                state = self._topics.get(topic)
                if state is None:
                    state = _TopicState(self._default_maxsize)
                    self._topics[topic] = state
                # Ensure queue uses current topic capacity by recreating with maxsize
                nonlocal queue
                queue = asyncio.Queue(maxsize=state.maxsize)
                state.subscribers.append(queue)

        # Register synchronously by running the coroutine.
        # If we're in an async context, we can't use the normal sync-over-async patterns
        # that would cause deadlocks. Instead, we'll make this work synchronously.
        try:
            asyncio.get_running_loop()
            # We're in an async context - we can't use typical sync-over-async patterns
            # Instead, create the subscription directly without async operations

            # This is a simplified synchronous version of _register()
            state = self._topics.get(topic)
            if state is None:
                state = _TopicState(self._default_maxsize)
                self._topics[topic] = state
            queue = asyncio.Queue(maxsize=state.maxsize)
            state.subscribers.append(queue)

        except RuntimeError:
            # No running loop; create a new loop just to register.
            asyncio.run(_register())

        return Subscription(self, topic, queue)

    async def publish(self, topic: str, payload: bytes) -> None:
        """Publish a message to a topic.

        Applies drop-oldest per subscriber queue if full.
        """
        if self._closed:
            raise RuntimeError("EventBus is closed")

        env = Envelope(topic=topic, ts=monotonic(), payload=payload)
        async with self._lock:
            state = self._topics.get(topic)
            if state is None:
                state = _TopicState(self._default_maxsize)
                self._topics[topic] = state
            state.publishes += 1
            if not state.subscribers:
                return
            # Snapshot list to tolerate modifications during iteration.
            subs = list(state.subscribers)
            for q in subs:
                # Make space if needed (drop-oldest)
                if q.full():
                    try:
                        _ = q.get_nowait()
                        state.drops += 1
                    except asyncio.QueueEmpty:
                        # Rare race; ignore.
                        pass
                try:
                    q.put_nowait(env)
                    state.deliveries += 1
                except asyncio.QueueFull:
                    # If still full due to concurrent consumer/prod,
                    # drop one more and retry once.
                    try:
                        _ = q.get_nowait()
                        state.drops += 1
                        q.put_nowait(env)
                        state.deliveries += 1
                    except Exception:
                        # Give up; this should be exceedingly rare.
                        pass

    async def close(self) -> None:
        """Gracefully close the bus and signal subscribers to finish."""
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            for state in self._topics.values():
                for q in list(state.subscribers):
                    # Use a more careful approach to avoid dropping user messages
                    # when inserting the sentinel
                    sentinel_added = False

                    # Try to add sentinel without dropping messages
                    if not q.full():
                        try:
                            q.put_nowait(_Sentinel)
                            sentinel_added = True
                        except asyncio.QueueFull:
                            pass

                    # If queue is full, we need to ensure the sentinel gets in
                    # We'll temporarily bypass the limit by manipulating the
                    # internal deque
                    if not sentinel_added:
                        try:
                            # Access the internal deque to add sentinel without
                            # size check. This is safer than modifying _maxsize
                            q._queue.append(_Sentinel)  # type: ignore
                        except Exception:
                            # Fallback: drop one message as last resort
                            try:
                                _ = q.get_nowait()
                                q.put_nowait(_Sentinel)
                            except Exception:
                                pass

    def metrics(self) -> BusMetrics:
        """Return per-topic metrics snapshot."""
        out: Dict[str, TopicStats] = {}
        # No await here; a brief inconsistency is acceptable for metrics.
        for name, state in self._topics.items():
            # queue_len as max of subscriber queue sizes to reflect worst backlog
            max_qlen = 0
            for q in state.subscribers:
                qlen = q.qsize()
                if qlen > max_qlen:
                    max_qlen = qlen
            out[name] = TopicStats(
                queue_len=max_qlen,
                drops=state.drops,
                publishes=state.publishes,
                deliveries=state.deliveries,
            )
        return BusMetrics(topics=out)

    def ensure_topic(self, topic: str, maxsize: int | None = None) -> None:
        """Ensure a topic exists and optionally override per-subscriber queue size.

        Changing the maxsize only affects new subscriptions for that topic.
        Thread/Task-safe to call from sync contexts.
        """
        if self._closed:
            raise RuntimeError("EventBus is closed")

        async def _ensure() -> None:
            async with self._lock:
                state = self._topics.get(topic)
                if state is None:
                    state = _TopicState(self._default_maxsize)
                    self._topics[topic] = state
                if maxsize is not None:
                    state.maxsize = max(1, int(maxsize))

        try:
            loop = asyncio.get_running_loop()
            fut = asyncio.run_coroutine_threadsafe(_ensure(), loop)
            fut.result()
        except RuntimeError:
            asyncio.run(_ensure())

    def list_topics(self) -> Dict[str, int]:
        """Return mapping of topic -> active subscriber count."""
        return {name: len(state.subscribers) for name, state in self._topics.items()}

    async def _remove_subscription(
        self, topic: str, queue: asyncio.Queue[Envelope | object]
    ) -> None:
        async with self._lock:
            state = self._topics.get(topic)
            if not state:
                return
            try:
                state.subscribers.remove(queue)
            except ValueError:
                return


class Subscription:
    """A subscription that yields Envelopes as an async iterator."""

    def __init__(
        self,
        bus: EventBus,
        topic: str,
        queue: asyncio.Queue[Envelope | object],
    ) -> None:
        self._bus = bus
        self._topic = topic
        self._queue: asyncio.Queue[Envelope | object] = queue
        self._closed = False

    def __aiter__(self) -> AsyncIterator[Envelope]:
        return self

    async def __anext__(self) -> Envelope:
        if self._closed:
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is _Sentinel or item is None:
            raise StopAsyncIteration
        assert isinstance(item, Envelope)
        return item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Signal iterator to stop if it's awaiting
        try:
            if not self._queue.full():
                self._queue.put_nowait(_Sentinel)
            else:
                # Queue is full - add sentinel without dropping user messages
                try:
                    # Access the internal deque to add sentinel without size check
                    self._queue._queue.append(_Sentinel)  # type: ignore
                except Exception:
                    # Fallback: drop one message as last resort
                    try:
                        _ = self._queue.get_nowait()
                        self._queue.put_nowait(_Sentinel)
                    except Exception:
                        pass
        except asyncio.QueueFull:
            # Last resort: ignore
            pass
        await self._bus._remove_subscription(self._topic, self._queue)


# Serialization helpers -----------------------------------------------------


def pack(obj: Any) -> bytes:
    """Serialize an object to bytes using msgpack."""
    return msgpack.packb(obj, use_bin_type=True)


def unpack(b: bytes) -> Any:
    """Deserialize bytes into an object using msgpack."""
    return msgpack.unpackb(b, raw=False, strict_map_key=False)
