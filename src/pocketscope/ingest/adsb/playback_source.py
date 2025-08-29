"""File-driven ADS-B playback source with deterministic timing.

This module provides a FilePlaybackSource that reads ADS-B messages from
JSONL trace files and replays them at simulated rates, publishing to an
EventBus.

Usage example:

    from pocketscope.core.events import EventBus
    from pocketscope.core.time import SimTimeSource
    from pocketscope.ingest.adsb.playback_source import FilePlaybackSource
    
    bus = EventBus()
    ts = SimTimeSource()
    src = FilePlaybackSource("trace.jsonl", ts=ts, bus=bus, speed=2.0)
    
    # Start playback in background
    task = asyncio.create_task(src.run())
    
    # Advance simulation time to trigger events
    ts.advance(1.0)
    
    # Stop playback
    await src.stop()
    await task

Input trace format (JSONL, one object per line):
{
  "t_mono": 0.00,
  "msg": {
    "icao24": "abc123",
    "callsign": "TEST1", 
    "lat": 40.0,
    "lon": -74.0,
    "baro_alt": 32000,
    "ground_speed": 450,
    "track_deg": 270,
    "src": "PLAYBACK"
  }
}
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.events import EventBus, pack
from ...core.models import AdsbMessage
from ...core.time import TimeSource

__all__ = [
    "FilePlaybackSource",
]

logger = logging.getLogger(__name__)


class FilePlaybackSource:
    """
    Replays ADS-B messages from a JSONL trace at simulated rates.
    Publishes to topic: "adsb.msg"
    """

    def __init__(
        self,
        path: str,
        *,
        ts: TimeSource,
        bus: EventBus,
        speed: float = 1.0,
        loop: bool = False,
        topic: str = "adsb.msg",
    ) -> None:
        """Initialize ADS-B file playback source.

        Args:
            path: Path to JSONL trace file
            ts: TimeSource for timing control
            bus: EventBus to publish messages to
            speed: Playback speed multiplier (2.0 = twice as fast)
            loop: Whether to loop the file continuously
            topic: Topic to publish messages to
        """
        self._path = Path(path)
        self._ts = ts
        self._bus = bus
        self._speed = max(0.1, float(speed))  # Prevent invalid speeds
        self._loop = loop
        self._topic = topic
        self._running = False
        self._stop_event = asyncio.Event()

        # Track event scheduling
        self._next_due: float | None = None
        self._events_cache: list[tuple[float, dict[str, Any]]] = []
        self._original_events: list[tuple[float, dict[str, Any]]] = []
        self._events_loaded = False
        self._pending_event_times: list[
            float
        ] = []  # Track pending event times for next_due

    async def run(self) -> None:
        """Start replaying ADS-B messages.

        Reads the JSONL file and publishes AdsbMessage instances to the EventBus
        with appropriate timing delays based on the TimeSource type.
        """
        if self._running:
            raise RuntimeError("FilePlaybackSource is already running")

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
        """Stop replaying messages."""
        self._stop_event.set()

    def next_due_monotonic(self) -> float | None:
        """Return the monotonic time of the next scheduled message.

        This is useful when using SimTimeSource to advance time precisely
        to the next event without overshooting.

        Returns:
            Next message time or None if no messages pending
        """
        if not self._events_loaded:
            self._load_events()

        # For SimTimeSource, check for pending event times
        if hasattr(self._ts, "_monotonic_time") and self._pending_event_times:
            current_time = self._ts.monotonic()
            future_times = [t for t in self._pending_event_times if t > current_time]
            return min(future_times) if future_times else None

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
                        logger.warning(
                            f"Skipping invalid line {line_num} in {self._path}: {e}"
                        )
                        continue

                if events:
                    # Sort by monotonic time to ensure proper ordering
                    events.sort(key=lambda e: e[0])  # Sort by t_mono

                    # Store original events (without offset)
                    self._original_events = events.copy()

                    # Apply speed multiplier to compress time intervals
                    first_event_time = events[0][0]
                    compressed_events = []
                    for t_mono, msg_data in events:
                        # Calculate relative time and apply speed multiplier
                        relative_time = t_mono - first_event_time
                        compressed_relative_time = relative_time / self._speed
                        compressed_time = first_event_time + compressed_relative_time
                        compressed_events.append((compressed_time, msg_data))

                    # Apply time offset for initial replay
                    current_time = self._ts.monotonic()
                    # Default: start events relative to current time
                    # This ensures the first event is scheduled for "now"
                    offset = current_time - compressed_events[0][0]

                    # Apply offset to all events
                    events = [
                        (t + offset, msg_data) for t, msg_data in compressed_events
                    ]

                    self._events_cache = events
                    # Set the first event as next due
                    if events:
                        self._next_due = events[0][0]

        except FileNotFoundError:
            logger.error(f"ADS-B trace file not found: {self._path}")
        except Exception as e:
            logger.error(f"Error reading ADS-B trace file {self._path}: {e}")

        self._events_loaded = True

    def _parse_record(self, record: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Parse a JSON record into (t_mono, msg_data) tuple."""
        t_mono = float(record["t_mono"])
        msg_data = dict(record["msg"])
        return (t_mono, msg_data)

    async def _replay_file_once(self) -> None:
        """Replay the entire file once."""
        # Load raw events from file if not already loaded
        if not self._events_loaded:
            self._load_events()

        if not self._events_cache:
            logger.warning(f"No valid ADS-B events found in {self._path}")
            return

        # For looping, we need to recalculate time offsets for each iteration
        # Use the original event times and apply fresh offset each time
        current_time = self._ts.monotonic()
        original_events = self._original_events

        if not original_events:
            return

        # Calculate time offset for this iteration
        # Apply speed compression to original events
        first_event_time = original_events[0][0]
        compressed_events = []
        for t_mono, msg_data in original_events:
            # Calculate relative time from first event and apply speed multiplier
            relative_time = t_mono - first_event_time
            compressed_relative_time = relative_time / self._speed
            compressed_time = first_event_time + compressed_relative_time
            compressed_events.append((compressed_time, msg_data))

        # Default: start events relative to current time
        offset = current_time - compressed_events[0][0]

        # Apply offset to create events for this iteration
        iteration_events = [(t + offset, msg_data) for t, msg_data in compressed_events]

        await self._replay_events(iteration_events)

    async def _replay_events(self, events: list[tuple[float, dict[str, Any]]]) -> None:
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

            for t_mono, msg_data in events:
                if t_mono <= current_time:
                    immediate_events.append((t_mono, msg_data))
                else:
                    future_events.append((t_mono, msg_data))

            # Update pending event times for next_due tracking
            self._pending_event_times = [t for t, _ in future_events]

            # Publish immediate events right away
            for t_mono, msg_data in immediate_events:
                if self._stop_event.is_set():
                    return
                try:
                    await self._publish_adsb_message(msg_data)
                except Exception as e:
                    logger.warning(f"Error publishing ADS-B message: {e}")

            # For future events, create sleep tasks
            if future_events:
                # Sort by time to track next due
                future_events.sort(key=lambda x: x[0])
                self._next_due = future_events[0][0]

                async def schedule_event(
                    t_mono: float, msg_data: dict[str, Any]
                ) -> tuple[float, dict[str, Any]]:
                    """Schedule a single event to be published at the specified time."""
                    try:
                        # Sleep until the event time
                        sleep_duration = t_mono - self._ts.monotonic()
                        if sleep_duration > 0:
                            await self._ts.sleep(sleep_duration)

                        # Check if we should stop
                        if self._stop_event.is_set():
                            return (t_mono, msg_data)

                        # Remove this event time from pending list
                        if t_mono in self._pending_event_times:
                            self._pending_event_times.remove(t_mono)

                        # Publish the event
                        await self._publish_adsb_message(msg_data)
                        return (t_mono, msg_data)
                    except Exception as e:
                        logger.warning(f"Error scheduling ADS-B message: {e}")
                        return (t_mono, msg_data)

                # Create tasks for all future events with event info attached
                event_task_map = {}  # task -> event_info
                for t_mono, msg_data in future_events:
                    if self._stop_event.is_set():
                        break
                    task = asyncio.create_task(schedule_event(t_mono, msg_data))
                    event_task_map[task] = (t_mono, msg_data)

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

            for i, (t_mono, msg_data) in enumerate(events):
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

                # Publish the message
                try:
                    await self._publish_adsb_message(msg_data)
                except Exception as e:
                    logger.warning(f"Error publishing ADS-B message: {e}")

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

    async def _publish_adsb_message(self, msg_data: dict[str, Any]) -> None:
        """Create AdsbMessage and publish to EventBus."""
        # Set timestamp to current time from TimeSource
        # Convert monotonic time to wall time approximation
        wall_time = self._ts.wall_time()

        # Create timestamp from wall time using timezone-aware datetime
        ts = datetime.fromtimestamp(wall_time, tz=timezone.utc)

        # Create AdsbMessage with current timestamp
        msg_data_with_ts = {"ts": ts, **msg_data}

        try:
            adsb_msg = AdsbMessage(**msg_data_with_ts)
            # Convert datetime to ISO string for serialization
            adsb_dict = adsb_msg.model_dump()
            adsb_dict["ts"] = adsb_msg.ts.isoformat()
            payload = pack(adsb_dict)
            await self._bus.publish(self._topic, payload)
        except Exception as e:
            logger.warning(f"Error creating/publishing AdsbMessage: {e}")
            raise
