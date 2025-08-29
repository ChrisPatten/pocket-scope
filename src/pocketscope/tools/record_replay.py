"""JSONL event recorder and replayer for EventBus integration.

This module provides tools to record EventBus events to JSONL files and
replay them with deterministic timing control.

Record format (JSON per line):
{
  "topic": "adsb.raw",
  "t_mono": 12.345,
  "t_wall": 1693333333.123,
  "payload_b64": "<base64 of bytes>"
}

Usage examples:

Recording events:
    bus = EventBus()
    ts = RealTimeSource()
    recorder = JsonlRecorder(bus, ts, "events.jsonl", ["adsb.raw", "gps.nmea"])
    
    # Start recording in background
    record_task = asyncio.create_task(recorder.run())
    
    # Publish some events...
    await bus.publish("adsb.raw", b"some data")
    
    # Stop recording
    await recorder.stop()

Replaying with real time:
    bus = EventBus()
    ts = RealTimeSource()
    replayer = JsonlReplayer(bus, ts, "events.jsonl", speed=2.0)  # 2x speed
    
    # Replay events in real-time
    await replayer.run()

Replaying with simulated time (deterministic):
    bus = EventBus()
    ts = SimTimeSource(start=0.0)
    replayer = JsonlReplayer(bus, ts, "events.jsonl")
    
    # Start replay in background
    replay_task = asyncio.create_task(replayer.run())
    
    # Manually advance time to trigger events
    next_time = replayer.next_due_monotonic()
    if next_time is not None:
        ts.set_time(next_time)
    
    # Continue advancing time as needed...
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

# Import at runtime only
from ..core.events import EventBus
from ..core.time import TimeSource

__all__ = [
    "JsonlRecorder",
    "JsonlReplayer",
]

logger = logging.getLogger(__name__)


class JsonlRecorder:
    """Records EventBus events to a JSONL file.

    Subscribes to specified topics and writes each event as a JSON line
    with monotonic timestamp and base64-encoded payload.
    """

    def __init__(
        self, bus: EventBus, ts: TimeSource, path: str, topics: list[str]
    ) -> None:
        """Initialize recorder.

        Args:
            bus: EventBus to subscribe to
            ts: TimeSource for timestamps
            path: Output file path
            topics: List of topics to record
        """
        self._bus = bus
        self._ts = ts
        self._path = Path(path)
        self._topics = list(topics)
        self._subscriptions: list[Any] = []  # Subscription objects
        self._file_handle: Any = None
        self._running = False
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Start recording events.

        This method runs until stop() is called, recording all events
        from subscribed topics to the JSONL file.
        """
        if self._running:
            raise RuntimeError("Recorder is already running")

        self._running = True

        try:
            # Open file for writing
            self._file_handle = open(self._path, "w", encoding="utf-8")

            # Subscribe to all topics
            for topic in self._topics:
                subscription = self._bus.subscribe(topic)
                self._subscriptions.append(subscription)

            # Start consumer tasks for each subscription
            tasks = []
            for i, subscription in enumerate(self._subscriptions):
                task = asyncio.create_task(
                    self._consume_subscription(subscription),
                    name=f"recorder-{self._topics[i]}",
                )
                tasks.append(task)

            # Wait for stop signal
            await self._stop_event.wait()

            # Cancel all tasks
            for task in tasks:
                task.cancel()

            # Wait for tasks to complete
            await asyncio.gather(*tasks, return_exceptions=True)

        finally:
            self._running = False
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None

    async def stop(self) -> None:
        """Stop recording and close file.

        Gracefully shuts down all subscriptions and closes the output file.
        """
        if not self._running:
            return

        # Signal stop
        self._stop_event.set()

        # Close subscriptions
        for subscription in self._subscriptions:
            await subscription.close()

        self._subscriptions.clear()

    async def _consume_subscription(self, subscription: Any) -> None:
        """Consume events from a subscription and write to file."""
        try:
            async for envelope in subscription:
                await self._write_event(envelope)
        except asyncio.CancelledError:
            # Expected when stopping
            pass
        except Exception as e:
            logger.warning(f"Error in recorder subscription: {e}")

    async def _write_event(self, envelope: Any) -> None:
        """Write a single event to the JSONL file."""
        try:
            # Create JSON record - use TimeSource for consistent timing
            record = {
                "topic": envelope.topic,
                "t_mono": self._ts.monotonic(),  # Use TimeSource for consistent timing
                "t_wall": self._ts.wall_time(),
                "payload_b64": base64.b64encode(envelope.payload).decode("ascii"),
            }

            # Write to file (run in thread to avoid blocking)
            line = json.dumps(record) + "\n"
            await asyncio.to_thread(self._file_handle.write, line)
            await asyncio.to_thread(self._file_handle.flush)

        except Exception as e:
            logger.warning(f"Error writing event to file: {e}")


class JsonlReplayer:
    """Replays events from a JSONL file into an EventBus.

    Reads JSONL records and publishes them to the EventBus with timing
    based on the configured TimeSource. Supports speed control and looping.
    """

    def __init__(
        self,
        bus: EventBus,
        ts: TimeSource,
        path: str,
        *,
        speed: float = 1.0,
        start_at: float | None = None,
        loop: bool = False,
    ) -> None:
        """Initialize replayer.

        Args:
            bus: EventBus to publish events to
            ts: TimeSource for timing control
            path: Input JSONL file path
            speed: Playback speed multiplier (e.g., 2.0 = 2x faster)
            start_at: Optional offset for starting t_mono
            loop: Whether to loop the file continuously
        """
        self._bus = bus
        self._ts = ts
        self._path = Path(path)
        self._speed = max(0.1, float(speed))  # Prevent invalid speeds
        self._start_at = start_at
        self._loop = loop
        self._running = False
        self._stop_event = asyncio.Event()

        # Track next event timing for SimTimeSource integration
        self._next_due: float | None = None
        self._events_cache: list[tuple[float, str, bytes]] = []
        self._original_events: list[
            tuple[float, str, bytes]
        ] = []  # Store original events without offset
        self._events_loaded = False

    async def run(self) -> None:
        """Start replaying events.

        Reads the JSONL file and publishes events to the EventBus with
        appropriate timing delays based on the TimeSource type.
        """
        if self._running:
            raise RuntimeError("Replayer is already running")

        self._running = True

        try:
            while True:
                await self._replay_file_once()

                if not self._loop:
                    break

                # Check if we should stop before looping
                if self._stop_event.is_set():
                    break

                # Small delay before restarting loop
                await self._ts.sleep(0.001)

        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop replaying events."""
        self._stop_event.set()

    def next_due_monotonic(self) -> float | None:
        """Return the monotonic time of the next scheduled event.

        This is useful when using SimTimeSource to advance time precisely
        to the next event without overshooting.

        Returns:
            Next event time or None if no events pending
        """
        if not self._events_loaded:
            self._load_events()
        return self._next_due

    def _load_events(self) -> None:
        """Load and cache events from the JSONL file."""
        if self._events_loaded:
            return

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                events = []

                # Parse all valid events
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        record = json.loads(line)
                        events.append(self._parse_record(record))
                    except Exception as e:
                        logger.debug(f"Skipping invalid line {line_num}: {e}")
                        continue

                if events:
                    # Sort by monotonic time to ensure proper ordering
                    events.sort(key=lambda e: e[0])  # Sort by t_mono

                    # Store original events (without offset)
                    self._original_events = events.copy()

                    # Apply time offset for initial replay
                    if self._start_at is not None:
                        # User-specified start time
                        offset = self._start_at - events[0][0]
                    else:
                        # Default: start events relative to current time
                        # This ensures the first event is scheduled for "now"
                        current_time = self._ts.monotonic()
                        offset = current_time - events[0][0]

                    # Apply offset to all events
                    events = [
                        (t + offset, topic, payload) for t, topic, payload in events
                    ]

                    self._events_cache = events
                    # Set the first event as next due
                    if events:
                        self._next_due = events[0][0]

        except FileNotFoundError:
            logger.error(f"JSONL file not found: {self._path}")
        except Exception as e:
            logger.error(f"Error reading JSONL file {self._path}: {e}")

        self._events_loaded = True

    async def _replay_file_once(self) -> None:
        """Replay the entire file once."""
        # Load raw events from file if not already loaded
        if not self._events_loaded:
            self._load_events()

        if not self._events_cache:
            logger.warning(f"No valid events found in {self._path}")
            return

        # For looping, we need to recalculate time offsets for each iteration
        # Use the original event times and apply fresh offset each time
        current_time = self._ts.monotonic()
        original_events = self._original_events

        if not original_events:
            return

        # Calculate time offset for this iteration
        if self._start_at is not None:
            # User-specified start time
            offset = self._start_at - original_events[0][0]
        else:
            # Default: start events relative to current time
            offset = current_time - original_events[0][0]

        # Apply offset to create events for this iteration
        iteration_events = [
            (t + offset, topic, payload) for t, topic, payload in original_events
        ]

        await self._replay_events(iteration_events)

    def _parse_record(self, record: dict[str, Any]) -> tuple[float, str, bytes]:
        """Parse a JSON record into (t_mono, topic, payload) tuple."""
        topic = str(record["topic"])
        t_mono = float(record["t_mono"])
        payload_b64 = str(record["payload_b64"])
        payload = base64.b64decode(payload_b64)

        return (t_mono, topic, payload)

    async def _replay_events(self, events: list[tuple[float, str, bytes]]) -> None:
        """Replay a list of events with proper timing."""
        if not events:
            return

        # Determine if we're using SimTimeSource or RealTimeSource
        is_sim_time = hasattr(self._ts, "_monotonic_time")

        if is_sim_time:
            # For SimTimeSource: create sleep tasks for all future events
            current_time = self._ts.monotonic()

            # Separate immediate events from future events
            immediate_events = []
            future_events = []

            for t_mono, topic, payload in events:
                if t_mono <= current_time:
                    immediate_events.append((t_mono, topic, payload))
                else:
                    future_events.append((t_mono, topic, payload))

            # Publish immediate events right away
            for t_mono, topic, payload in immediate_events:
                if self._stop_event.is_set():
                    return
                try:
                    await self._bus.publish(topic, payload)
                except Exception as e:
                    logger.warning(f"Error publishing event {topic}: {e}")

            # For future events, create sleep tasks
            if future_events:
                # Sort by time to track next due
                future_events.sort(key=lambda x: x[0])
                self._next_due = future_events[0][0]

                async def schedule_event(
                    t_mono: float, topic: str, payload: bytes
                ) -> tuple[float, str, bytes]:
                    """Schedule a single event to be published at the specified time."""
                    try:
                        # Sleep until the event time
                        sleep_duration = t_mono - self._ts.monotonic()
                        if sleep_duration > 0:
                            await self._ts.sleep(sleep_duration)

                        # Check if we should stop
                        if self._stop_event.is_set():
                            return (t_mono, topic, payload)

                        # Publish the event
                        await self._bus.publish(topic, payload)
                        return (t_mono, topic, payload)
                    except Exception as e:
                        logger.warning(f"Error scheduling event {topic}: {e}")
                        return (t_mono, topic, payload)

                # Create tasks for all future events with event info attached
                event_task_map = {}  # task -> event_info
                for t_mono, topic, payload in future_events:
                    if self._stop_event.is_set():
                        break
                    task = asyncio.create_task(schedule_event(t_mono, topic, payload))
                    event_task_map[task] = (t_mono, topic, payload)

                # Monitor tasks and update next_due as they complete
                while event_task_map and not self._stop_event.is_set():
                    # Wait for any task to complete
                    done, pending = await asyncio.wait(
                        event_task_map.keys(), return_when=asyncio.FIRST_COMPLETED
                    )

                    # Remove completed tasks from tracking
                    for task in done:
                        del event_task_map[task]

                    # Update next_due to the earliest remaining event
                    if event_task_map:
                        remaining_times = [
                            event_info[0] for event_info in event_task_map.values()
                        ]
                        self._next_due = min(remaining_times)
                    else:
                        self._next_due = None

                # Cancel any remaining tasks if stopping
                for task in event_task_map.keys():
                    if not task.done():
                        task.cancel()

        else:
            # For RealTimeSource: use sleep delays sequentially
            prev_time = events[0][0]

            for i, (t_mono, topic, payload) in enumerate(events):
                # Check for stop signal
                if self._stop_event.is_set():
                    break

                # Update next due time to the current event
                self._next_due = t_mono

                # Calculate delay from previous event
                delay = (t_mono - prev_time) / self._speed
                prev_time = t_mono

                # Sleep for the computed delay
                if delay > 0:
                    await self._ts.sleep(delay)

                # Publish the event
                try:
                    await self._bus.publish(topic, payload)
                except Exception as e:
                    logger.warning(f"Error publishing event {topic}: {e}")

                # Set next due to the next event (if any)
                if i + 1 < len(events):
                    self._next_due = events[i + 1][0]
                else:
                    self._next_due = None

        # Clear next due time when done (unless we're looping)
        if not self._loop:
            self._next_due = None
        else:
            # For loop mode, schedule next iteration to start soon
            if self._original_events:
                # Next iteration should start after a small delay from current time
                self._next_due = self._ts.monotonic() + 0.001
