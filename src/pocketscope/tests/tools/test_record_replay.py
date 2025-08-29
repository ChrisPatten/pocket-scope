"""Tests for JSONL record and replay functionality."""

import asyncio
import base64
import json
import tempfile
from pathlib import Path

import pytest

from pocketscope.core.events import EventBus
from pocketscope.core.time import RealTimeSource, SimTimeSource
from pocketscope.tools.record_replay import JsonlRecorder, JsonlReplayer


@pytest.fixture
def sample_trace_file() -> Path:
    """Create a temporary JSONL file with sample data."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        # Three events with base64-encoded payloads
        events = [
            {
                "topic": "test.topic1",
                "t_mono": 0.0,
                "t_wall": 1693333333.0,
                "payload_b64": base64.b64encode(b"one").decode("ascii"),
            },
            {
                "topic": "test.topic2",
                "t_mono": 0.5,
                "t_wall": 1693333333.5,
                "payload_b64": base64.b64encode(b"two").decode("ascii"),
            },
            {
                "topic": "test.topic1",
                "t_mono": 1.0,
                "t_wall": 1693333334.0,
                "payload_b64": base64.b64encode(b"three").decode("ascii"),
            },
        ]

        for event in events:
            f.write(json.dumps(event) + "\n")

    return Path(f.name)


class TestJsonlRecorder:
    """Test JsonlRecorder functionality."""

    @pytest.mark.asyncio
    async def test_record_basic_functionality(self) -> None:
        """Test basic recording functionality."""
        bus = None

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            output_path = f.name

        try:
            bus = EventBus()
            ts = SimTimeSource(start=10.0)
            recorder = JsonlRecorder(bus, ts, output_path, ["test.topic"])

            # Start recording in background
            record_task = asyncio.create_task(recorder.run())
            await asyncio.sleep(0)  # Let recorder start

            # Publish some events with explicit timing control
            await bus.publish("test.topic", b"hello")
            await asyncio.sleep(0)  # Let recorder process first event

            ts.advance(1.0)
            await bus.publish("test.topic", b"world")
            await asyncio.sleep(0)  # Let recorder process second event

            # Give recorder time to process
            await asyncio.sleep(0.01)

            # Stop recording
            await recorder.stop()
            await record_task

            # Verify file contents
            with open(output_path, "r") as f:
                lines = f.readlines()

            assert len(lines) == 2

            # Parse first event
            event1 = json.loads(lines[0])
            assert event1["topic"] == "test.topic"
            assert event1["t_mono"] == 10.0
            assert base64.b64decode(event1["payload_b64"]) == b"hello"

            # Parse second event
            event2 = json.loads(lines[1])
            assert event2["topic"] == "test.topic"
            assert event2["t_mono"] == 11.0
            assert base64.b64decode(event2["payload_b64"]) == b"world"

        finally:
            # Cleanup
            Path(output_path).unlink(missing_ok=True)
            if bus is not None:
                await bus.close()

    @pytest.mark.asyncio
    async def test_record_multiple_topics(self) -> None:
        """Test recording multiple topics."""
        bus = None

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            output_path = f.name

        try:
            bus = EventBus()
            ts = SimTimeSource(start=0.0)
            recorder = JsonlRecorder(bus, ts, output_path, ["topic1", "topic2"])

            # Start recording
            record_task = asyncio.create_task(recorder.run())
            await asyncio.sleep(0)

            # Publish to different topics
            await bus.publish("topic1", b"msg1")
            await bus.publish("topic2", b"msg2")
            await bus.publish("topic1", b"msg3")

            await asyncio.sleep(0.01)
            await recorder.stop()
            await record_task

            # Verify file contents
            with open(output_path, "r") as f:
                events = [json.loads(line) for line in f]

            assert len(events) == 3
            assert events[0]["topic"] == "topic1"
            assert events[1]["topic"] == "topic2"
            assert events[2]["topic"] == "topic1"

        finally:
            Path(output_path).unlink(missing_ok=True)
            if bus is not None:
                await bus.close()

    @pytest.mark.asyncio
    async def test_recorder_already_running_error(self) -> None:
        """Test that starting recorder twice raises error."""
        bus = None

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            output_path = f.name

        try:
            bus = EventBus()
            ts = SimTimeSource()
            recorder = JsonlRecorder(bus, ts, output_path, ["test"])

            record_task = asyncio.create_task(recorder.run())
            await asyncio.sleep(0)

            # Try to start again
            with pytest.raises(RuntimeError, match="already running"):
                await recorder.run()

            await recorder.stop()
            await record_task

        finally:
            Path(output_path).unlink(missing_ok=True)
            if bus is not None:
                await bus.close()


class TestJsonlReplayer:
    """Test JsonlReplayer functionality."""

    @pytest.mark.asyncio
    async def test_deterministic_playback_sim_time(
        self, sample_trace_file: Path
    ) -> None:
        """Test deterministic playback with SimTimeSource."""
        bus = None
        sub1 = None
        sub2 = None
        collector_task = None
        replay_task = None

        try:
            bus = EventBus()
            # Start simulation at time 10.0 (not 0.0, to be more realistic)
            ts = SimTimeSource(start=10.0)

            # Subscribe to test topics
            sub1 = bus.subscribe("test.topic1")
            sub2 = bus.subscribe("test.topic2")

            received_events: list[tuple[str, bytes]] = []

            async def collect_events() -> None:
                """Collect events from both subscriptions."""
                try:

                    async def collect_topic1() -> None:
                        async for env in sub1:
                            received_events.append((env.topic, env.payload))

                    async def collect_topic2() -> None:
                        async for env in sub2:
                            received_events.append((env.topic, env.payload))

                    await asyncio.gather(
                        collect_topic1(), collect_topic2(), return_exceptions=True
                    )
                except asyncio.CancelledError:
                    pass  # Expected when cancelled

            # Start event collector
            collector_task = asyncio.create_task(collect_events())

            # Create and start replayer
            replayer = JsonlReplayer(bus, ts, str(sample_trace_file), speed=1.0)
            replay_task = asyncio.create_task(replayer.run())

            await asyncio.sleep(0)  # Let tasks start

            # Initially no events should be delivered
            assert len(received_events) == 0
            assert ts.monotonic() == 10.0

            # Give the replayer a moment to process any immediately due events
            await asyncio.sleep(0.001)

            # Events should be mapped to start at current time (10.0)
            # Original events: 0.0, 0.5, 1.0 -> Replay events: 10.0, 10.5, 11.0

            # The first event should be immediately available since sim time = 10.0
            # and the first event is scheduled at 10.0
            assert len(received_events) == 1
            assert received_events[0] == ("test.topic1", b"one")

            # Next event should be at 10.5
            next_due = replayer.next_due_monotonic()
            assert next_due == 10.5
            ts.set_time(10.5)
            await asyncio.sleep(0)
            await asyncio.sleep(0)  # Give events more time to propagate

            assert len(received_events) == 2
            assert received_events[1] == ("test.topic2", b"two")

            # Give the replayer time to update next_due
            await asyncio.sleep(0)

            # Advance to third event time (11.0)
            next_due = replayer.next_due_monotonic()
            assert next_due == 11.0
            ts.set_time(11.0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)  # Give events more time to propagate

            assert len(received_events) == 3
            assert received_events[2] == ("test.topic1", b"three")

            # Give the replayer time to finish and clear next_due
            await asyncio.sleep(0)

            # No more events
            assert replayer.next_due_monotonic() is None

            # Wait for replay to complete
            await replay_task

        finally:
            # Cancel collector task first
            if collector_task is not None:
                collector_task.cancel()
                try:
                    await collector_task
                except asyncio.CancelledError:
                    pass

            # Close subscriptions
            if sub1 is not None:
                await sub1.close()
            if sub2 is not None:
                await sub2.close()

            # Stop replay if still running
            if replay_task is not None and not replay_task.done():
                replay_task.cancel()
                try:
                    await replay_task
                except asyncio.CancelledError:
                    pass

            # Close bus
            if bus is not None:
                await bus.close()

            # Clean up test file
            sample_trace_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_real_time_playback_smoke(self) -> None:
        """Test real-time playback smoke test."""
        # Create a small trace file with events close together
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            events = [
                {
                    "topic": "test.fast",
                    "t_mono": 0.0,
                    "t_wall": 1693333333.0,
                    "payload_b64": base64.b64encode(b"first").decode("ascii"),
                },
                {
                    "topic": "test.fast",
                    "t_mono": 0.02,  # 20ms later
                    "t_wall": 1693333333.02,
                    "payload_b64": base64.b64encode(b"second").decode("ascii"),
                },
            ]

            for event in events:
                f.write(json.dumps(event) + "\n")

            trace_path = Path(f.name)

        try:
            bus = EventBus()
            ts = RealTimeSource()
            sub = bus.subscribe("test.fast")

            received_events: list[bytes] = []

            async def collect_events() -> None:
                async for env in sub:
                    received_events.append(env.payload)

            # Start collector and replayer
            collector_task = asyncio.create_task(collect_events())
            replayer = JsonlReplayer(bus, ts, str(trace_path), speed=2.0)  # 2x speed

            # Use wait_for to ensure test doesn't hang
            await asyncio.wait_for(replayer.run(), timeout=1.0)

            # Give a moment for final events to be processed
            await asyncio.sleep(0.01)

            # Should have received both events in order
            assert len(received_events) == 2
            assert received_events[0] == b"first"
            assert received_events[1] == b"second"

            await sub.close()
            await bus.close()
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)

        finally:
            trace_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_recorder_round_trip(self) -> None:
        """Test recording events and then replaying them."""
        bus = None

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            trace_path = Path(f.name)

        try:
            bus = EventBus()
            ts = SimTimeSource(start=100.0)
            # Record phase
            recorder = JsonlRecorder(bus, ts, str(trace_path), ["test.roundtrip"])
            record_task = asyncio.create_task(recorder.run())
            await asyncio.sleep(0)

            # Publish events at known times
            ts.set_time(100.0)
            await bus.publish("test.roundtrip", b"event1")
            await asyncio.sleep(0)  # Let recorder process

            ts.set_time(100.5)
            await bus.publish("test.roundtrip", b"event2")
            await asyncio.sleep(0)  # Let recorder process

            await asyncio.sleep(0.01)
            await recorder.stop()
            await record_task

            # Replay phase with fresh bus and sim time
            replay_bus = EventBus()
            replay_ts = SimTimeSource(start=0.0)
            sub = replay_bus.subscribe("test.roundtrip")

            received_events: list[tuple[float, bytes]] = []

            async def collect_events() -> None:
                async for env in sub:
                    received_events.append((replay_ts.monotonic(), env.payload))

            collector_task = asyncio.create_task(collect_events())

            replayer = JsonlReplayer(replay_bus, replay_ts, str(trace_path))
            replay_task = asyncio.create_task(replayer.run())
            await asyncio.sleep(0)

            # Let immediate events be processed first
            await asyncio.sleep(0)

            # Advance through replay
            next_due = replayer.next_due_monotonic()
            if next_due is not None:
                replay_ts.set_time(next_due)
                await asyncio.sleep(0)
                await asyncio.sleep(0)  # Give events more time to propagate

            next_due = replayer.next_due_monotonic()
            if next_due is not None:
                replay_ts.set_time(next_due)
                await asyncio.sleep(0)
                await asyncio.sleep(0)  # Give events more time to propagate

            await replay_task

            # Verify we got the events back in the right order
            assert len(received_events) == 2
            assert received_events[0][1] == b"event1"
            assert received_events[1][1] == b"event2"

            # Time differences should match original (100.0 -> 100.5 = 0.5 delta)
            time_delta = received_events[1][0] - received_events[0][0]
            assert abs(time_delta - 0.5) < 0.001  # Small tolerance for floating point

            await sub.close()
            await replay_bus.close()
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)

        finally:
            if bus is not None:
                await bus.close()
            trace_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_replayer_speed_control(self, sample_trace_file: Path) -> None:
        """Test speed control in replayer."""
        bus = None

        try:
            bus = EventBus()
            ts = RealTimeSource()

            # Test with 10x speed (events at 0.0, 0.5, 1.0 should play in ~0.15s total)
            replayer = JsonlReplayer(bus, ts, str(sample_trace_file), speed=10.0)

            start_time = ts.monotonic()
            await replayer.run()
            elapsed = ts.monotonic() - start_time

            # Should complete much faster than 1 second (original duration)
            assert elapsed < 0.5  # Should be ~0.1s but allow margin for overhead

        finally:
            if bus is not None:
                await bus.close()
            sample_trace_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_replayer_start_at_offset(self, sample_trace_file: Path) -> None:
        """Test start_at parameter for time offset."""
        bus = None
        sub = None

        try:
            bus = EventBus()
            ts = SimTimeSource(start=0.0)
            sub = bus.subscribe("test.topic1")

            received_times: list[float] = []

            async def collect_events() -> None:
                async for env in sub:
                    received_times.append(ts.monotonic())

            collector_task = asyncio.create_task(collect_events())

            # Start replay with offset of 50.0
            replayer = JsonlReplayer(bus, ts, str(sample_trace_file), start_at=50.0)
            replay_task = asyncio.create_task(replayer.run())
            await asyncio.sleep(0)

            # Let immediate events be processed first
            await asyncio.sleep(0)

            # Events should now be at 50.0, 50.5, 51.0 instead of 0.0, 0.5, 1.0
            next_due = replayer.next_due_monotonic()
            assert next_due == 50.0  # First event offset to 50.0

            ts.set_time(50.0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)  # Give replayer time to update next_due
            await asyncio.sleep(0)  # Give replayer more time to update next_due

            next_due = replayer.next_due_monotonic()
            assert (
                next_due == 50.5
            )  # Second event offset to 50.5 (was 0.5, now 0.5 + 50.0)

            ts.set_time(50.5)
            await asyncio.sleep(0)
            await asyncio.sleep(0)  # Give replayer time to update next_due
            await asyncio.sleep(0)  # Give replayer more time to update next_due

            next_due = replayer.next_due_monotonic()
            assert (
                next_due == 51.0
            )  # Third event offset to 51.0 (was 1.0, now 1.0 + 50.0)

            ts.set_time(51.0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)  # Give replayer time to update next_due
            await asyncio.sleep(0)  # Give replayer more time to update next_due

            await replay_task

            # Should have received events at offset times
            assert len(received_times) == 2  # Only topic1 events
            assert received_times[0] == 50.0
            assert received_times[1] == 51.0

            await sub.close()
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)

        finally:
            if sub is not None:
                await sub.close()
            if bus is not None:
                await bus.close()
            sample_trace_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_replayer_loop_mode(self) -> None:
        """Test loop mode functionality."""
        bus = None
        sub = None

        # Create a small trace file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            event = {
                "topic": "test.loop",
                "t_mono": 0.0,
                "t_wall": 1693333333.0,
                "payload_b64": base64.b64encode(b"loop_event").decode("ascii"),
            }
            f.write(json.dumps(event) + "\n")
            trace_path = Path(f.name)

        try:
            bus = EventBus()
            ts = SimTimeSource(start=0.0)
            sub = bus.subscribe("test.loop")

            received_count = 0

            async def count_events() -> None:
                nonlocal received_count
                if sub is not None:
                    async for env in sub:
                        received_count += 1
                        # Stop replayer after a few loops
                        if received_count >= 3:
                            await replayer.stop()
                            break

            collector_task = asyncio.create_task(count_events())

            # Start replayer in loop mode
            replayer = JsonlReplayer(bus, ts, str(trace_path), loop=True)
            replay_task = asyncio.create_task(replayer.run())
            await asyncio.sleep(0)

            # Advance time multiple times to trigger loops
            for i in range(5):  # More iterations to ensure we trigger enough events
                next_due = replayer.next_due_monotonic()
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

            # Replayer should already be stopped by the collector
            if not replay_task.done():
                await replayer.stop()
            await replay_task
            await sub.close()

        finally:
            if bus is not None:
                await bus.close()
            trace_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_replayer_invalid_file(self) -> None:
        """Test replayer behavior with invalid/missing file."""
        bus = None

        try:
            bus = EventBus()
            ts = SimTimeSource()

            # Test with non-existent file
            replayer = JsonlReplayer(bus, ts, "/nonexistent/file.jsonl")

            # Should not crash, just log an error
            await replayer.run()

        finally:
            if bus is not None:
                await bus.close()

    @pytest.mark.asyncio
    async def test_replayer_malformed_jsonl(self) -> None:
        """Test replayer behavior with malformed JSONL."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Write mix of valid and invalid lines
            f.write('{"topic": "test", "t_mono": 0.0, "payload_b64": "dGVzdA=="}\n')
            f.write("invalid json line\n")
            f.write('{"topic": "test2", "t_mono": 1.0, "payload_b64": "dGVzdDI="}\n')
            f.write("\n")  # Empty line
            f.write('{"incomplete": "missing fields"}\n')

            trace_path = Path(f.name)

        bus = None
        sub = None
        sub2 = None

        try:
            bus = EventBus()
            ts = SimTimeSource(start=0.0)

            sub = bus.subscribe("test")
            sub2 = bus.subscribe("test2")

            received_events: list[str] = []

            async def collect_events() -> None:
                async def collect_test() -> None:
                    async for env in sub:
                        received_events.append(env.topic)

                async def collect_test2() -> None:
                    async for env in sub2:
                        received_events.append(env.topic)

                await asyncio.gather(
                    collect_test(), collect_test2(), return_exceptions=True
                )

            collector_task = asyncio.create_task(collect_events())

            replayer = JsonlReplayer(bus, ts, str(trace_path))
            replay_task = asyncio.create_task(replayer.run())
            await asyncio.sleep(0)

            # Advance through valid events
            ts.set_time(0.0)
            await asyncio.sleep(0)
            ts.set_time(1.0)
            await asyncio.sleep(0)

            await replay_task

            # Should have received only the valid events
            assert len(received_events) == 2
            assert "test" in received_events
            assert "test2" in received_events

            await sub.close()
            await sub2.close()
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)

        finally:
            if sub is not None:
                await sub.close()
            if sub2 is not None:
                await sub2.close()
            if bus is not None:
                await bus.close()
            trace_path.unlink(missing_ok=True)
