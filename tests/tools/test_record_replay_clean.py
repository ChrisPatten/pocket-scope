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

            # Publish some events
            await bus.publish("test.topic", b"hello")
            ts.advance(1.0)
            await bus.publish("test.topic", b"world")

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
            assert event1["t_mono"] >= 10.0  # Should be >= start time
            assert base64.b64decode(event1["payload_b64"]) == b"hello"

            # Parse second event
            event2 = json.loads(lines[1])
            assert event2["topic"] == "test.topic"
            assert event2["t_mono"] >= event1["t_mono"]  # Should be after first event
            assert base64.b64decode(event2["payload_b64"]) == b"world"

        finally:
            # Cleanup
            Path(output_path).unlink(missing_ok=True)
            if bus is not None:
                await bus.close()


class TestJsonlReplayer:
    """Test JsonlReplayer functionality - just a simple test for now."""

    @pytest.mark.asyncio
    async def test_replayer_basic(self, sample_trace_file: Path) -> None:
        """Test basic replayer functionality."""
        bus = None
        sub = None

        try:
            bus = EventBus()
            ts = RealTimeSource()
            sub = bus.subscribe("test.topic1")

            received_events = []

            async def collect_events() -> None:
                async for env in sub:
                    received_events.append(env.payload)
                    if len(received_events) >= 2:  # Stop after getting some events
                        break

            # Start collector
            collector_task = asyncio.create_task(collect_events())

            # Create and run replayer
            replayer = JsonlReplayer(
                bus, ts, str(sample_trace_file), speed=10.0
            )  # Fast speed

            # Use wait_for to ensure test doesn't hang
            try:
                await asyncio.wait_for(replayer.run(), timeout=2.0)
            except asyncio.TimeoutError:
                pass  # Expected for real-time playback

            # Give a moment for events to be processed
            await asyncio.sleep(0.1)

            # Stop collector
            collector_task.cancel()
            try:
                await collector_task
            except asyncio.CancelledError:
                pass

            # Should have received at least some events
            assert len(received_events) >= 1
            assert received_events[0] == b"one"

        finally:
            if sub is not None:
                await sub.close()
            if bus is not None:
                await bus.close()
            sample_trace_file.unlink(missing_ok=True)
