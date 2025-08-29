"""Tests for ADS-B FilePlaybackSource functionality."""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from pocketscope.core.events import EventBus, unpack
from pocketscope.core.models import AdsbMessage
from pocketscope.core.time import RealTimeSource, SimTimeSource
from pocketscope.ingest.adsb.playback_source import FilePlaybackSource


@pytest.fixture
def adsb_trace_file() -> Path:
    """Create a temporary JSONL file with ADS-B trace data."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        # Three ADS-B events with different timestamps
        events = [
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
                    "src": "PLAYBACK",
                },
            },
            {
                "t_mono": 0.40,
                "msg": {
                    "icao24": "def456",
                    "callsign": "TEST2",
                    "lat": 40.1,
                    "lon": -74.1,
                    "baro_alt": 25000,
                    "ground_speed": 420,
                    "track_deg": 90,
                    "src": "PLAYBACK",
                },
            },
            {
                "t_mono": 1.00,
                "msg": {
                    "icao24": "abc123",
                    "lat": 40.02,
                    "lon": -74.02,
                    "baro_alt": 32100,
                    "ground_speed": 452,
                    "track_deg": 271,
                    "src": "PLAYBACK",
                },
            },
        ]

        for event in events:
            f.write(json.dumps(event) + "\n")

    return Path(f.name)


class TestFilePlaybackSource:
    """Test FilePlaybackSource functionality."""

    @pytest.mark.asyncio
    async def test_deterministic_playback_sim_time(self, adsb_trace_file: Path) -> None:
        """Test deterministic playback with SimTimeSource (golden sequence)."""
        bus = None
        sub = None
        collector_task = None
        playback_task = None

        try:
            bus = EventBus()
            # Start simulation at time 10.0 (not 0.0, to be more realistic)
            ts = SimTimeSource(start=10.0)

            # Subscribe to ADS-B messages
            sub = bus.subscribe("adsb.msg")

            received_messages: list[tuple[float, AdsbMessage]] = []

            async def collect_messages() -> None:
                """Collect ADS-B messages from subscription."""
                try:
                    async for env in sub:
                        # Deserialize AdsbMessage
                        msg_dict = unpack(env.payload)
                        # Convert timestamp back to datetime
                        msg_dict["ts"] = datetime.fromisoformat(
                            msg_dict["ts"].replace("Z", "+00:00")
                        )
                        adsb_msg = AdsbMessage(**msg_dict)
                        received_messages.append((ts.monotonic(), adsb_msg))
                except asyncio.CancelledError:
                    pass  # Expected when cancelled

            # Start message collector
            collector_task = asyncio.create_task(collect_messages())

            # Create and start playback source
            playback_src = FilePlaybackSource(
                str(adsb_trace_file), ts=ts, bus=bus, speed=1.0
            )
            playback_task = asyncio.create_task(playback_src.run())

            await asyncio.sleep(0)  # Let tasks start

            # Initially no messages should be delivered
            assert len(received_messages) == 0
            assert ts.monotonic() == 10.0

            # Give the playback source a moment to process any immediately due events
            await asyncio.sleep(0.001)

            # Events should be mapped to start at current time (10.0)
            # Original events: 0.0, 0.4, 1.0 -> Playback events: 10.0, 10.4, 11.0

            # The first event should be immediately available since sim time = 10.0
            # and the first event is scheduled at 10.0
            assert len(received_messages) == 1
            assert received_messages[0][0] == 10.0
            assert received_messages[0][1].icao24 == "abc123"
            assert received_messages[0][1].callsign == "TEST1"
            assert received_messages[0][1].lat == 40.0
            assert received_messages[0][1].lon == -74.0

            # Next event should be at 10.4
            next_due = playback_src.next_due_monotonic()
            assert next_due == 10.4
            ts.set_time(10.4)
            await asyncio.sleep(0)
            await asyncio.sleep(0)  # Give events more time to propagate

            assert len(received_messages) == 2
            assert received_messages[1][0] == 10.4
            assert received_messages[1][1].icao24 == "def456"
            assert received_messages[1][1].callsign == "TEST2"
            assert received_messages[1][1].lat == 40.1
            assert received_messages[1][1].lon == -74.1

            # Give the playback source time to update next_due
            await asyncio.sleep(0)

            # Advance to third event time (11.0)
            next_due = playback_src.next_due_monotonic()
            assert next_due == 11.0
            ts.set_time(11.0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)  # Give events more time to propagate

            assert len(received_messages) == 3
            assert received_messages[2][0] == 11.0
            assert received_messages[2][1].icao24 == "abc123"
            assert received_messages[2][1].lat == 40.02
            assert received_messages[2][1].lon == -74.02

            # Give the playback source time to finish and clear next_due
            await asyncio.sleep(0)

            # No more events
            assert playback_src.next_due_monotonic() is None

            # Wait for playback to complete
            await playback_task

        finally:
            # Cancel collector task first
            if collector_task is not None:
                collector_task.cancel()
                try:
                    await collector_task
                except asyncio.CancelledError:
                    pass

            # Close subscription
            if sub is not None:
                await sub.close()

            # Stop playback if still running
            if playback_task is not None and not playback_task.done():
                playback_task.cancel()
                try:
                    await playback_task
                except asyncio.CancelledError:
                    pass

            # Close bus
            if bus is not None:
                await bus.close()

            # Clean up test file
            adsb_trace_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_speed_multiplier(self, adsb_trace_file: Path) -> None:
        """Test speed multiplier functionality."""
        bus = None
        sub = None
        collector_task = None
        playback_task = None

        try:
            bus = EventBus()
            ts = SimTimeSource(start=0.0)
            sub = bus.subscribe("adsb.msg")

            received_times: list[float] = []

            async def collect_times() -> None:
                async for env in sub:
                    received_times.append(ts.monotonic())

            collector_task = asyncio.create_task(collect_times())

            # Use 2x speed - events at 0.0, 0.4, 1.0 should be at 0.0, 0.2, 0.5
            playback_src = FilePlaybackSource(
                str(adsb_trace_file), ts=ts, bus=bus, speed=2.0
            )
            playback_task = asyncio.create_task(playback_src.run())
            await asyncio.sleep(0)

            # Let immediate events be processed first
            await asyncio.sleep(0)

            # First event at 0.0 (immediate)
            assert len(received_times) == 1
            assert received_times[0] == 0.0

            # Second event should be at 0.2 (0.4 / 2.0)
            next_due = playback_src.next_due_monotonic()
            assert next_due == 0.2
            ts.set_time(0.2)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert len(received_times) == 2
            assert received_times[1] == 0.2

            # Third event should be at 0.5 (1.0 / 2.0)
            next_due = playback_src.next_due_monotonic()
            assert next_due == 0.5
            ts.set_time(0.5)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert len(received_times) == 3
            assert received_times[2] == 0.5

            await playback_task

        finally:
            if collector_task is not None:
                collector_task.cancel()
                await asyncio.gather(collector_task, return_exceptions=True)
            if sub is not None:
                await sub.close()
            if playback_task is not None and not playback_task.done():
                playback_task.cancel()
                await asyncio.gather(playback_task, return_exceptions=True)
            if bus is not None:
                await bus.close()
            adsb_trace_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_real_time_playback_smoke(self) -> None:
        """Test real-time playback smoke test."""
        # Create a small trace file with events close together
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            events = [
                {
                    "t_mono": 0.0,
                    "msg": {
                        "icao24": "111111",
                        "callsign": "FAST1",
                        "lat": 41.0,
                        "lon": -75.0,
                        "baro_alt": 30000,
                        "ground_speed": 400,
                        "track_deg": 180,
                        "src": "PLAYBACK",
                    },
                },
                {
                    "t_mono": 0.02,  # 20ms later
                    "msg": {
                        "icao24": "222222",
                        "callsign": "FAST2",
                        "lat": 41.01,
                        "lon": -75.01,
                        "baro_alt": 31000,
                        "ground_speed": 410,
                        "track_deg": 181,
                        "src": "PLAYBACK",
                    },
                },
            ]

            for event in events:
                f.write(json.dumps(event) + "\n")

            trace_path = Path(f.name)

        try:
            bus = EventBus()
            ts = RealTimeSource()
            sub = bus.subscribe("adsb.msg")

            received_events: list[str] = []

            async def collect_events() -> None:
                async for env in sub:
                    msg_dict = unpack(env.payload)
                    received_events.append(msg_dict["icao24"])

            # Start collector and playback source
            collector_task = asyncio.create_task(collect_events())
            playback_src = FilePlaybackSource(
                str(trace_path), ts=ts, bus=bus, speed=2.0
            )  # 2x speed

            # Use wait_for to ensure test doesn't hang
            await asyncio.wait_for(playback_src.run(), timeout=1.0)

            # Give a moment for final events to be processed
            await asyncio.sleep(0.01)

            # Should have received both events in order
            assert len(received_events) == 2
            assert received_events[0] == "111111"
            assert received_events[1] == "222222"

            await sub.close()
            await bus.close()
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)

        finally:
            trace_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_graceful_stop(self) -> None:
        """Test graceful stop functionality."""
        # Create a trace file with looping enabled
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            event = {
                "t_mono": 0.0,
                "msg": {
                    "icao24": "999999",
                    "callsign": "LOOP1",
                    "lat": 42.0,
                    "lon": -76.0,
                    "baro_alt": 28000,
                    "ground_speed": 380,
                    "track_deg": 90,
                    "src": "PLAYBACK",
                },
            }
            f.write(json.dumps(event) + "\n")
            trace_path = Path(f.name)

        bus = None

        try:
            bus = EventBus()
            ts = SimTimeSource(start=0.0)
            sub = bus.subscribe("adsb.msg")

            received_count = 0

            async def count_events() -> None:
                nonlocal received_count
                async for env in sub:
                    received_count += 1
                    # Stop playback after a few loops
                    if received_count >= 3:
                        await playback_src.stop()
                        break

            collector_task = asyncio.create_task(count_events())

            # Start playback source in loop mode
            playback_src = FilePlaybackSource(
                str(trace_path), ts=ts, bus=bus, loop=True
            )
            playback_task = asyncio.create_task(playback_src.run())
            await asyncio.sleep(0)

            # Advance time multiple times to trigger loops
            for i in range(5):  # More iterations to ensure we trigger enough events
                next_due = playback_src.next_due_monotonic()
                if next_due is not None:
                    ts.set_time(next_due)
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)  # Give events time to propagate
                    ts.advance(0.001)  # Small increment to allow loop restart
                    await asyncio.sleep(0)

                # Check if we've collected enough events
                if received_count >= 3:
                    break

            # Wait for collector to finish
            await collector_task

            # Should have received multiple instances of the same event
            assert received_count >= 3

            # Playback source should already be stopped by the collector
            if not playback_task.done():
                await playback_src.stop()
            await playback_task
            await sub.close()

        finally:
            if bus is not None:
                await bus.close()
            trace_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_invalid_file(self) -> None:
        """Test playback source behavior with invalid/missing file."""
        bus = None

        try:
            bus = EventBus()
            ts = SimTimeSource()

            # Test with non-existent file
            playback_src = FilePlaybackSource("/nonexistent/file.jsonl", ts=ts, bus=bus)

            # Should not crash, just log an error
            await playback_src.run()

        finally:
            if bus is not None:
                await bus.close()

    @pytest.mark.asyncio
    async def test_malformed_jsonl(self) -> None:
        """Test playback source behavior with malformed JSONL."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Write mix of valid and invalid lines
            f.write(
                '{"t_mono": 0.0, "msg": {"icao24": "aaa111", "lat": 40.0, '
                '"lon": -74.0, "src": "PLAYBACK"}}\n'
            )
            f.write("invalid json line\n")
            f.write(
                '{"t_mono": 1.0, "msg": {"icao24": "bbb222", "lat": 40.1, '
                '"lon": -74.1, "src": "PLAYBACK"}}\n'
            )
            f.write("\n")  # Empty line
            f.write('{"incomplete": "missing fields"}\n')

            trace_path = Path(f.name)

        bus = None
        sub = None

        try:
            bus = EventBus()
            ts = SimTimeSource(start=0.0)
            sub = bus.subscribe("adsb.msg")

            received_events: list[str] = []

            async def collect_events() -> None:
                async for env in sub:
                    msg_dict = unpack(env.payload)
                    received_events.append(msg_dict["icao24"])

            collector_task = asyncio.create_task(collect_events())

            playback_src = FilePlaybackSource(str(trace_path), ts=ts, bus=bus)
            playback_task = asyncio.create_task(playback_src.run())
            await asyncio.sleep(0)

            # Advance through valid events
            ts.set_time(0.0)
            await asyncio.sleep(0)
            ts.set_time(1.0)
            await asyncio.sleep(0)

            await playback_task

            # Should have received only the valid events
            assert len(received_events) == 2
            assert "aaa111" in received_events
            assert "bbb222" in received_events

            await sub.close()
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)

        finally:
            if sub is not None:
                await sub.close()
            if bus is not None:
                await bus.close()
            trace_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_custom_topic(self, adsb_trace_file: Path) -> None:
        """Test custom topic parameter."""
        bus = None
        sub = None

        try:
            bus = EventBus()
            ts = SimTimeSource(start=0.0)

            # Subscribe to custom topic
            custom_topic = "custom.adsb.topic"
            sub = bus.subscribe(custom_topic)

            received_count = 0

            async def count_events() -> None:
                nonlocal received_count
                async for env in sub:
                    received_count += 1

            collector_task = asyncio.create_task(count_events())

            # Create playback source with custom topic
            playback_src = FilePlaybackSource(
                str(adsb_trace_file), ts=ts, bus=bus, topic=custom_topic
            )
            playback_task = asyncio.create_task(playback_src.run())
            await asyncio.sleep(0)

            # Process all events by advancing to each next_due time
            for i in range(3):
                next_due = playback_src.next_due_monotonic()
                if next_due is not None and next_due > ts.monotonic():
                    ts.set_time(next_due)
                    await asyncio.sleep(0)

            await playback_task

            # Should have received all events on custom topic
            assert received_count == 3

            await sub.close()
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)

        finally:
            if sub is not None:
                await sub.close()
            if bus is not None:
                await bus.close()
            adsb_trace_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_already_running_error(self, adsb_trace_file: Path) -> None:
        """Test that starting playback source twice raises error."""
        bus = None

        try:
            bus = EventBus()
            ts = SimTimeSource()
            playback_src = FilePlaybackSource(str(adsb_trace_file), ts=ts, bus=bus)

            playback_task = asyncio.create_task(playback_src.run())
            await asyncio.sleep(0)

            # Try to start again - this should fail immediately
            with pytest.raises(RuntimeError, match="already running"):
                await playback_src.run()

            # Stop the first run
            await playback_src.stop()

            # Wait a bit for the task to complete after stop
            try:
                await asyncio.wait_for(playback_task, timeout=0.1)
            except asyncio.TimeoutError:
                # If it doesn't complete, cancel it
                playback_task.cancel()
                try:
                    await playback_task
                except asyncio.CancelledError:
                    pass

        finally:
            if bus is not None:
                await bus.close()
            adsb_trace_file.unlink(missing_ok=True)
