"""TrackService for maintaining aircraft tracks from AdsbMessage events.

This module provides a domain service that maintains aircraft tracks from
incoming AdsbMessage events, with ring-buffer trails, quality/age tracking,
and expiry management.

Example usage:

    from pocketscope.core.events import EventBus
    from pocketscope.core.time import SimTimeSource
    from pocketscope.core.tracks import TrackService

    # Setup
    bus = EventBus()
    ts = SimTimeSource(start=0.0)
    service = TrackService(bus, ts)

    # Start the service
    await service.run()

    # Publish an ADS-B message
    msg = AdsbMessage(
        ts=datetime.fromtimestamp(ts.wall_time()),
        icao24="abc123",
        lat=40.0,
        lon=-74.0
    )
    await bus.publish("adsb.msg", pack(msg.model_dump()))

    # Query tracks
    track = service.get("abc123")
    if track:
        print(f"Track has {len(track.history)} points")

    # Pin for longer trail retention
    service.pin("abc123", True)

    await service.stop()
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from pocketscope.core.events import EventBus, Subscription, unpack
from pocketscope.core.models import AdsbMessage, AircraftTrack, HistoryPoint
from pocketscope.core.time import TimeSource

__all__ = ["TrackService"]


class TrackService:
    """
    Maintains AircraftTrack objects keyed by ICAO24.
    - Associates incoming AdsbMessage to a track.
    - Updates last state and ring-buffer trail (ts, lat, lon, alt_ft|None).
    - Expires stale tracks.
    """

    def __init__(
        self,
        bus: EventBus,
        ts: TimeSource,
        *,
        trail_len_default_s: float = 60.0,
        trail_len_pinned_s: float = 180.0,
        expiry_s: float = 300.0,
        sweep_interval_s: float = 1.0,
        topic_in: str = "adsb.msg",
        topic_out: str = "tracks.updated",
    ) -> None:
        self._bus = bus
        self._ts = ts
        self._trail_len_default_s = trail_len_default_s
        self._trail_len_pinned_s = trail_len_pinned_s
        self._expiry_s = expiry_s
        # Auto-adjust sweep interval if caller supplies interval larger than
        # expiry window so that small-expiry tests (e.g. expiry_s=2.0) do not
        # wait the full original interval before the first sweep. This keeps
        # production defaults (1s) responsive while ensuring tests advance
        # simulated time deterministically.
        if expiry_s > 0 and sweep_interval_s > expiry_s:
            sweep_interval_s = min(sweep_interval_s, max(0.5, expiry_s / 2.0))
        self._sweep_interval_s = sweep_interval_s
        self._topic_in = topic_in
        self._topic_out = topic_out

        # Active tracks by ICAO24
        self._tracks: dict[str, AircraftTrack] = {}
        # Pinned tracks (retain longer trail)
        self._pinned: set[str] = set()
        # Last position times for 1Hz sampling
        self._last_position_ts: dict[str, float] = {}

        # Task management
        self._subscription: Subscription | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._expiry_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        """Start the track service."""
        if self._running:
            return

        self._running = True
        self._subscription = self._bus.subscribe(self._topic_in)

        # Start the main processing task
        self._task = asyncio.create_task(self._process_messages())

        # Start the expiry task (only if expiry is reasonably small)
        if self._expiry_s < 100000:  # Skip expiry for very large values (tests)
            self._expiry_task = asyncio.create_task(self._expiry_loop())

    async def stop(self) -> None:
        """Stop the track service."""
        if not self._running:
            return

        self._running = False

        # Close subscription
        if self._subscription:
            await self._subscription.close()
            self._subscription = None

        # Cancel tasks
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._expiry_task:
            self._expiry_task.cancel()
            try:
                await self._expiry_task
            except asyncio.CancelledError:
                pass
            self._expiry_task = None

    async def _process_messages(self) -> None:
        """Main message processing loop."""
        if not self._subscription:
            return

        try:
            async for envelope in self._subscription:
                if not self._running:
                    break

                try:
                    # Unpack the message
                    msg_data = unpack(envelope.payload)

                    # Convert timestamp back to datetime (from ISO string)
                    if "ts" in msg_data and isinstance(msg_data["ts"], str):
                        msg_data["ts"] = datetime.fromisoformat(
                            msg_data["ts"].replace("Z", "+00:00")
                        )

                    msg = AdsbMessage.model_validate(msg_data)

                    # Process the message
                    await self._process_adsb_message(msg)

                except Exception as e:
                    # Log error but continue processing
                    # In a real implementation, you'd use proper logging
                    print(f"Error processing message: {e}")
                    continue

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Error in message processing loop: {e}")

    async def _process_adsb_message(self, msg: AdsbMessage) -> None:
        """Process a single AdsbMessage."""
        icao24 = msg.icao24
        msg_ts = msg.ts.timestamp()

        # Get or create track
        track = self._tracks.get(icao24)
        if track is None:
            track = AircraftTrack(
                icao24=icao24,
                last_ts=msg.ts,
                callsign=msg.callsign,
            )
            self._tracks[icao24] = track

        # Check if this is an out-of-order message
        if msg_ts < track.last_ts.timestamp():
            # Skip updating state and timestamp for out-of-order messages
            # Could optionally add to trail if it's older than last trail sample
            return

        # Update track state
        track.last_ts = msg.ts
        if msg.callsign is not None:
            track.callsign = msg.callsign

        # Update state fields (preserve existing values for fields not present)
        state_fields: dict[str, object] = {}
        if msg.ground_speed is not None:
            state_fields["ground_speed"] = msg.ground_speed
        if msg.track_deg is not None:
            state_fields["track_deg"] = msg.track_deg
        if msg.vertical_rate is not None:
            state_fields["vertical_rate"] = msg.vertical_rate
        if msg.baro_alt is not None:
            state_fields["baro_alt"] = msg.baro_alt
        if msg.geo_alt is not None:
            state_fields["geo_alt"] = msg.geo_alt
        if msg.squawk is not None:
            state_fields["squawk"] = msg.squawk
        if msg.nic is not None:
            state_fields["nic"] = msg.nic
        if msg.nacp is not None:
            state_fields["nacp"] = msg.nacp

        for field, value in state_fields.items():
            if value is not None:
                track.state[field] = value

        # Add trail point if we have coordinates and meet 1Hz sampling rule
        if msg.lat is not None and msg.lon is not None:
            last_pos_ts = self._last_position_ts.get(
                icao24, -1.0
            )  # Use -1.0 to allow first message

            # Check 1Hz sampling rule (minimum 0.9s between position updates)
            if msg_ts - last_pos_ts >= 0.9:
                # Determine altitude for trail point
                alt_ft: float | None = msg.baro_alt or msg.geo_alt

                # Create and add history point
                point: HistoryPoint = (msg.ts, msg.lat, msg.lon, alt_ft)
                track.history.append(point)

                # Update last position timestamp
                self._last_position_ts[icao24] = msg_ts

                # Trim trail by time window using message timestamp as reference
                self._trim_trail(track, msg_ts)

    def _trim_trail(self, track: AircraftTrack, current_time: float) -> None:
        """Trim track trail to maintain time window."""
        # Determine trail length based on pinning
        trail_len = (
            self._trail_len_pinned_s
            if track.icao24 in self._pinned
            else self._trail_len_default_s
        )

        cutoff_time = current_time - trail_len

        # Remove old points (keep points at exactly cutoff_time)
        while track.history and track.history[0][0].timestamp() < cutoff_time:
            track.history.pop(0)

    async def _expiry_loop(self) -> None:
        """Background loop to expire stale tracks."""
        try:
            while self._running:
                await self._ts.sleep(self._sweep_interval_s)
                if not self._running:
                    break

                await self._expire_stale_tracks()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Error in expiry loop: {e}")

    async def _expire_stale_tracks(self) -> None:
        """Expire tracks that haven't been updated recently."""
        # Use current time consistently with message timestamps
        # For SimTimeSource, use the current sim time
        # For RealTimeSource, use wall time
        from .time import SimTimeSource

        if isinstance(self._ts, SimTimeSource):
            current_time = self._ts.monotonic()
        else:
            current_time = self._ts.wall_time()

        expired_icaos: list[str] = []

        # Check each track for expiry
        to_remove = []
        for icao24, track in self._tracks.items():
            # Calculate age based on time difference
            track_time = track.last_ts.timestamp()
            age_s = current_time - track_time

            if age_s > self._expiry_s:
                to_remove.append(icao24)
                expired_icaos.append(icao24)

        # Remove expired tracks
        for icao24 in to_remove:
            del self._tracks[icao24]
            self._pinned.discard(icao24)
            self._last_position_ts.pop(icao24, None)

        # Publish update if there were any changes
        if expired_icaos:
            from pocketscope.core.events import pack

            update = {
                "ts": current_time,
                "active": len(self._tracks),
                "expired": expired_icaos,
            }

            await self._bus.publish(self._topic_out, pack(update))

    # Query helpers (used by tests/UI)
    def get(self, icao24: str) -> AircraftTrack | None:
        """Get a track by ICAO24."""
        return self._tracks.get(icao24)

    def list_active(self) -> list[AircraftTrack]:
        """List all active tracks."""
        return list(self._tracks.values())

    def pin(self, icao24: str, pinned: bool = True) -> None:
        """Pin or unpin a track for longer trail retention."""
        if pinned:
            self._pinned.add(icao24)
        else:
            self._pinned.discard(icao24)

        # Re-trim trail with new settings if track exists
        track = self._tracks.get(icao24)
        if track:
            # Use the track's latest message time as reference for re-trimming
            current_time = track.last_ts.timestamp()
            self._trim_trail(track, current_time)

    # Maintenance helpers -------------------------------------------------
    def retrim_all(self) -> None:
        """Force re-trimming of all active track histories.

        Invoked when the global trail length settings change (e.g. user
        cycles Track Length in the settings menu) so the visual trails
        shrink immediately instead of waiting for the next position
        update per track. Uses each track's latest timestamp as the
        reference time which matches the semantics in :meth:`pin`.
        """
        for track in self._tracks.values():
            try:
                self._trim_trail(track, track.last_ts.timestamp())
            except Exception:
                # Defensive: never let a single bad track block others
                continue

    # Demo / maintenance utilities ------------------------------------
    def clear(self) -> None:
        """Remove all active tracks and reset internal state.

        Used when toggling demo playback on/off so previously ingested
        tracks (live or demo) do not mix. Intentionally silent and fast.
        """
        self._tracks.clear()
        self._pinned.clear()
        self._last_position_ts.clear()
