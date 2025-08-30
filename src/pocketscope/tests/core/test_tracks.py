"""Tests for TrackService."""

import asyncio
from datetime import datetime, timezone

import pytest

from pocketscope.core.events import EventBus, pack
from pocketscope.core.models import AdsbMessage, AircraftTrack
from pocketscope.core.time import SimTimeSource
from pocketscope.core.tracks import TrackService


def create_adsb_message(
    icao24: str,
    ts: float,
    *,
    lat: float | None = None,
    lon: float | None = None,
    alt_ft: float | None = None,
    callsign: str | None = None,
    ground_speed: float | None = None,
    track_deg: float | None = None,
    vertical_rate: float | None = None,
    nic: int | None = None,
    nacp: int | None = None,
) -> AdsbMessage:
    """Factory to create AdsbMessage with defaults."""
    # Convert the raw timestamp to a proper datetime
    # This matches what the real playback source does
    return AdsbMessage(
        ts=datetime.fromtimestamp(ts, tz=timezone.utc),
        icao24=icao24,
        lat=lat,
        lon=lon,
        baro_alt=alt_ft,
        geo_alt=None,
        callsign=callsign,
        ground_speed=ground_speed,
        track_deg=track_deg,
        vertical_rate=vertical_rate,
        nic=nic,
        nacp=nacp,
        src="PLAYBACK",
    )


def extract_trail_timestamps(track: AircraftTrack | None) -> list[float]:
    """Extract timestamps from track history for assertions."""
    if not track or not track.history:
        return []
    return [point[0].timestamp() for point in track.history]


@pytest.mark.asyncio
async def test_create_and_update() -> None:
    """Test creating and updating tracks with basic messages."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts)

    await service.run()

    try:
        # Publish three messages with increasing lat/lon
        messages = [
            create_adsb_message("abc123", 0.0, lat=40.0, lon=-74.0),
            create_adsb_message("abc123", 0.5, lat=40.01, lon=-74.01),
            create_adsb_message("abc123", 1.0, lat=40.02, lon=-74.02),
        ]

        for msg in messages:
            # Convert datetime to ISO string for serialization
            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            await bus.publish("adsb.msg", pack(msg_dict))

        # Advance time to let messages process
        ts.advance(0.1)
        await asyncio.sleep(0.01)  # Let event loop process

        # Check track exists
        track = service.get("abc123")
        assert track is not None
        assert track.icao24 == "abc123"
        assert track.last_ts.timestamp() == 1.0

        # Check trail - should have 2 points due to 1Hz sampling (0.0 and ~1.0)
        trail_ts = extract_trail_timestamps(track)
        assert len(trail_ts) == 2
        assert trail_ts[0] == 0.0
        assert trail_ts[1] == 1.0

    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_ring_buffer_trimming_by_time_window() -> None:
    """Test ring-buffer trimming by time window."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts, trail_len_default_s=1.0)

    await service.run()

    try:
        # Publish points at t=0.0, 0.5, 1.0, 2.1
        messages = [
            create_adsb_message("abc123", 0.0, lat=40.0, lon=-74.0),
            create_adsb_message(
                "abc123", 0.5, lat=40.01, lon=-74.01
            ),  # Too close to 0.0, won't be added
            create_adsb_message("abc123", 1.0, lat=40.02, lon=-74.02),
            create_adsb_message("abc123", 2.1, lat=40.03, lon=-74.03),
        ]

        for i, msg in enumerate(messages):
            ts.set_time(msg.ts.timestamp())
            # Convert datetime to ISO string for serialization
            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            await bus.publish("adsb.msg", pack(msg_dict))
            await asyncio.sleep(0.01)

        # After t=2.1, trail should only contain points with ts >= 1.1
        track = service.get("abc123")
        assert track is not None

        trail_ts = extract_trail_timestamps(track)
        # Should have points at 1.0 and 2.1 (within 1.0s window from 2.1)
        expected_ts = [t for t in [0.0, 1.0, 2.1] if t >= 2.1 - 1.0]
        assert trail_ts == expected_ts

    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_pinned_track_longer_window() -> None:
    """Test pinned tracks use longer trail window."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts, trail_len_default_s=1.0, trail_len_pinned_s=3.0)

    await service.run()

    try:
        # Pin the track
        service.pin("abc123", True)

        # Add points at t=0.0, 1.0, 2.0, 3.0, 4.0
        timestamps = [0.0, 1.0, 2.0, 3.0, 4.0]
        for t in timestamps:
            msg = create_adsb_message("abc123", t, lat=40.0 + t * 0.01, lon=-74.0)
            ts.set_time(t)
            # Convert datetime to ISO string for serialization
            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            await bus.publish("adsb.msg", pack(msg_dict))
            await asyncio.sleep(0.01)

        # At t=4.0, trail should retain last 3 seconds (>= 1.0)
        track = service.get("abc123")
        assert track is not None

        trail_ts = extract_trail_timestamps(track)
        # Should have points at 1.0, 2.0, 3.0, 4.0 (within 3.0s window from 4.0)
        expected_ts = [t for t in timestamps if t >= 4.0 - 3.0]
        assert trail_ts == expected_ts

    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_expiry() -> None:
    """Test track expiry."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts, expiry_s=2.0)

    # Subscribe to track updates to monitor expiry
    updates_sub = bus.subscribe("tracks.updated")
    received_updates = []

    async def collect_updates() -> None:
        async for envelope in updates_sub:
            from pocketscope.core.events import unpack

            update = unpack(envelope.payload)
            received_updates.append(update)

    update_task = asyncio.create_task(collect_updates())

    await service.run()

    try:
        # Create track at t=0.0
        msg = create_adsb_message("abc123", 0.0, lat=40.0, lon=-74.0)
        # Convert datetime to ISO string for serialization
        msg_dict = msg.model_dump()
        msg_dict["ts"] = msg.ts.isoformat()
        await bus.publish("adsb.msg", pack(msg_dict))
        await asyncio.sleep(0.01)

        # Verify track exists
        track = service.get("abc123")
        assert track is not None
        assert "abc123" in [t.icao24 for t in service.list_active()]

        # Advance to t=2.1 without new messages (past expiry)
        ts.advance(2.1)
        await asyncio.sleep(0.1)  # Let expiry loop run

        # Track should be expired
        assert service.get("abc123") is None
        assert "abc123" not in [t.icao24 for t in service.list_active()]

        # Should have received expiry notification
        await asyncio.sleep(0.1)  # Let update be published
        assert len(received_updates) > 0

        # Find the expiry update
        expiry_update = None
        for update in received_updates:
            if "abc123" in update.get("expired", []):
                expiry_update = update
                break

        assert expiry_update is not None
        assert "abc123" in expiry_update["expired"]

    finally:
        await service.stop()
        await updates_sub.close()
        update_task.cancel()
        try:
            await update_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_out_of_order_message_ignored() -> None:
    """Test out-of-order messages are ignored."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts)

    await service.run()

    try:
        # Create track with last_ts=5.0
        msg1 = create_adsb_message(
            "abc123", 5.0, lat=40.0, lon=-74.0, ground_speed=100.0
        )
        ts.set_time(5.0)
        # Convert datetime to ISO string for serialization
        msg1_dict = msg1.model_dump()
        msg1_dict["ts"] = msg1.ts.isoformat()
        await bus.publish("adsb.msg", pack(msg1_dict))
        await asyncio.sleep(0.01)

        track = service.get("abc123")
        assert track is not None
        assert track.last_ts.timestamp() == 5.0
        assert track.state.get("ground_speed") == 100.0
        initial_trail_len = len(track.history)

        # Publish out-of-order message with ts=4.0
        msg2 = create_adsb_message(
            "abc123", 4.0, lat=40.01, lon=-74.01, ground_speed=200.0
        )
        # Convert datetime to ISO string for serialization
        msg2_dict = msg2.model_dump()
        msg2_dict["ts"] = msg2.ts.isoformat()
        await bus.publish("adsb.msg", pack(msg2_dict))
        await asyncio.sleep(0.01)

        # Verify last_ts and state remain unchanged
        track = service.get("abc123")
        assert track is not None
        assert track.last_ts.timestamp() == 5.0  # Should not be updated
        assert track.state.get("ground_speed") == 100.0  # Should not be updated
        assert len(track.history) == initial_trail_len  # Trail not extended

    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_multiple_aircraft() -> None:
    """Test multiple aircraft tracks update independently."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts)

    await service.run()

    try:
        # Publish interleaved messages for two aircraft
        messages = [
            create_adsb_message("abc123", 0.0, lat=40.0, lon=-74.0, callsign="AAL123"),
            create_adsb_message("def456", 0.5, lat=41.0, lon=-75.0, callsign="UAL456"),
            create_adsb_message(
                "abc123", 1.0, lat=40.01, lon=-74.01, ground_speed=150.0
            ),
            create_adsb_message(
                "def456", 1.5, lat=41.01, lon=-75.01, ground_speed=200.0
            ),
        ]

        for msg in messages:
            ts.set_time(msg.ts.timestamp())
            # Convert datetime to ISO string for serialization
            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            await bus.publish("adsb.msg", pack(msg_dict))
            await asyncio.sleep(0.01)

        # Check both tracks exist and are independent
        track1 = service.get("abc123")
        track2 = service.get("def456")

        assert track1 is not None
        assert track2 is not None

        # Check track 1
        assert track1.icao24 == "abc123"
        assert track1.callsign == "AAL123"
        assert track1.last_ts.timestamp() == 1.0
        assert track1.state.get("ground_speed") == 150.0

        # Check track 2
        assert track2.icao24 == "def456"
        assert track2.callsign == "UAL456"
        assert track2.last_ts.timestamp() == 1.5
        assert track2.state.get("ground_speed") == 200.0

        # Verify no cross-contamination
        assert track1.callsign != track2.callsign
        assert track1.state.get("ground_speed") != track2.state.get("ground_speed")

        # Check both appear in active list
        active_tracks = service.list_active()
        active_icaos = {track.icao24 for track in active_tracks}
        assert "abc123" in active_icaos
        assert "def456" in active_icaos

    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_1hz_sampling_rule() -> None:
    """Test that trail points are sampled at most 1Hz."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    # Disable expiry for this test to focus on trail functionality
    service = TrackService(bus, ts, expiry_s=1000000.0)

    await service.run()

    try:
        # Publish messages closer than 0.9s apart
        messages = [
            create_adsb_message("abc123", 0.0, lat=40.0, lon=-74.0),
            create_adsb_message("abc123", 0.3, lat=40.01, lon=-74.01),  # Too close
            create_adsb_message("abc123", 0.6, lat=40.02, lon=-74.02),  # Too close
            create_adsb_message("abc123", 1.0, lat=40.03, lon=-74.03),  # >= 0.9s gap
            create_adsb_message("abc123", 1.2, lat=40.04, lon=-74.04),  # Too close
            create_adsb_message("abc123", 2.0, lat=40.05, lon=-74.05),  # >= 0.9s gap
        ]

        for msg in messages:
            ts.set_time(msg.ts.timestamp())
            # Convert datetime to ISO string for serialization
            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            await bus.publish("adsb.msg", pack(msg_dict))
            await asyncio.sleep(0.01)

        track = service.get("abc123")
        assert track is not None

        # Should only have points at 0.0, 1.0, 2.0 due to 1Hz sampling
        trail_ts = extract_trail_timestamps(track)
        assert trail_ts == [0.0, 1.0, 2.0]

    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_coordinates_required_for_trail() -> None:
    """Test that trail points are only added when lat/lon are present."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts)

    await service.run()

    try:
        # Publish messages with and without coordinates
        messages = [
            create_adsb_message("abc123", 0.0, lat=40.0, lon=-74.0),  # Has coords
            create_adsb_message("abc123", 1.0, ground_speed=150.0),  # No coords
            create_adsb_message("abc123", 2.0, lat=40.01, lon=-74.01),  # Has coords
        ]

        for msg in messages:
            ts.set_time(msg.ts.timestamp())
            # Convert datetime to ISO string for serialization
            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            await bus.publish("adsb.msg", pack(msg_dict))
            await asyncio.sleep(0.01)

        track = service.get("abc123")
        assert track is not None

        # Should only have points at 0.0 and 2.0 (messages with coordinates)
        trail_ts = extract_trail_timestamps(track)
        assert trail_ts == [0.0, 2.0]

        # But state should still be updated from message without coords
        assert track.state.get("ground_speed") == 150.0

    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_state_field_updates() -> None:
    """Test that various state fields are properly updated."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts)

    await service.run()

    try:
        # Publish message with various state fields
        msg = create_adsb_message(
            "abc123",
            0.0,
            lat=40.0,
            lon=-74.0,
            alt_ft=5000.0,
            callsign="TEST123",
            ground_speed=150.0,
            track_deg=45.0,
            vertical_rate=500.0,
            nic=8,
            nacp=9,
        )

        # Convert datetime to ISO string for serialization
        msg_dict = msg.model_dump()
        msg_dict["ts"] = msg.ts.isoformat()
        await bus.publish("adsb.msg", pack(msg_dict))
        await asyncio.sleep(0.01)

        track = service.get("abc123")
        assert track is not None
        assert track.callsign == "TEST123"
        assert track.state["ground_speed"] == 150.0
        assert track.state["track_deg"] == 45.0
        assert track.state["vertical_rate"] == 500.0
        assert track.state["baro_alt"] == 5000.0
        assert track.state["nic"] == 8
        assert track.state["nacp"] == 9

    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_pin_unpin_functionality() -> None:
    """Test pin/unpin functionality."""
    ts = SimTimeSource(start=0.0)
    bus = EventBus()
    service = TrackService(bus, ts, trail_len_default_s=1.0, trail_len_pinned_s=3.0)

    await service.run()

    try:
        # Create track with some initial history
        timestamps = [0.0, 1.0, 2.0]
        for t in timestamps:
            msg = create_adsb_message("abc123", t, lat=40.0 + t * 0.01, lon=-74.0)
            ts.set_time(t)
            # Convert datetime to ISO string for serialization
            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            await bus.publish("adsb.msg", pack(msg_dict))
            await asyncio.sleep(0.01)

        # At t=2.0 with default trail length (1.0s), only recent points remain
        track = service.get("abc123")
        assert track is not None
        trail_ts = extract_trail_timestamps(track)
        # Should have points at 1.0, 2.0 (within 1.0s from 2.0)
        assert trail_ts == [1.0, 2.0]

        # Pin the track - this should prevent aggressive future trimming
        service.pin("abc123", True)

        # Add more points to test the longer window
        for t in [3.0, 4.0, 5.0]:
            msg = create_adsb_message("abc123", t, lat=40.0 + t * 0.01, lon=-74.0)
            ts.set_time(t)
            # Convert datetime to ISO string for serialization
            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            await bus.publish("adsb.msg", pack(msg_dict))
            await asyncio.sleep(0.01)

        # Now should have more points retained (3.0s window)
        track = service.get("abc123")
        assert track is not None
        trail_ts = extract_trail_timestamps(track)
        # At t=5.0 with 3.0s window, points >= 2.0 should remain
        expected_ts = [2.0, 3.0, 4.0, 5.0]
        assert trail_ts == expected_ts

        # Unpin and verify shorter trail takes effect
        service.pin("abc123", False)

        # Add another point to trigger re-evaluation
        msg = create_adsb_message("abc123", 6.0, lat=40.06, lon=-74.0)
        ts.set_time(6.0)
        # Convert datetime to ISO string for serialization
        msg_dict = msg.model_dump()
        msg_dict["ts"] = msg.ts.isoformat()
        await bus.publish("adsb.msg", pack(msg_dict))
        await asyncio.sleep(0.01)

        # Should be back to shorter trail
        track = service.get("abc123")
        assert track is not None
        trail_ts = extract_trail_timestamps(track)
        # At t=6.0 with 1.0s window, only points >= 5.0 should remain
        assert trail_ts == [5.0, 6.0]

    finally:
        await service.stop()
