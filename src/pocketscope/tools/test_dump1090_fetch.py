#!/usr/bin/env python3
"""
Test script for debugging dump1090 fetch pipeline.

This script replicates the exact data flow from live_view.py with detailed
instrumentation to identify where failures occur in the ADS-B data pipeline.

Usage:
    python -m pocketscope.tools.test_dump1090_fetch --url https://adsb.chrispatten.dev/data/aircraft.json
    
Environment variables for debugging:
    DUMP1090_TIMEOUT_S=10.0           # Increase timeout
    DUMP1090_VERIFY_TLS=false         # Disable TLS verification
    DUMP1090_FORCE_IPV4=true          # Force IPv4 connections
    DEBUG_LEVEL=2                     # Set debug verbosity (0-3)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict

from pocketscope.core.events import EventBus, pack, unpack
from pocketscope.core.models import AdsbMessage
from pocketscope.core.time import RealTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.ingest.adsb.json_source import Dump1090JsonSource

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Silence some noisy loggers unless we want full debug
debug_level = int(os.environ.get("DEBUG_LEVEL", "1"))
if debug_level < 3:
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class InstrumentedDump1090Source(Dump1090JsonSource):
    """Instrumented version of Dump1090JsonSource with detailed logging."""

    def __init__(self, *args, **kwargs):
        """Initialize with detailed configuration logging."""
        logger.info("=== INITIALIZING DUMP1090 SOURCE ===")
        logger.info("Args: %s", args)
        logger.info("Kwargs: %s", {k: v for k, v in kwargs.items() if k != "bus"})

        # Log environment variables
        env_vars = [
            "DUMP1090_TIMEOUT_S",
            "DUMP1090_CONNECT_TIMEOUT_S",
            "DUMP1090_VERIFY_TLS",
            "DUMP1090_FORCE_IPV4",
            "DUMP1090_BASE_URL",
        ]
        logger.info("Environment variables:")
        for var in env_vars:
            value = os.environ.get(var, "<not set>")
            logger.info("  %s=%s", var, value)

        super().__init__(*args, **kwargs)

        # Log final configuration
        logger.info("Final configuration:")
        logger.info("  URL: %s", self._url)
        logger.info("  Poll interval: %.2fs", self._interval)
        logger.info("  Timeout: %.2fs", self._timeout_s)
        logger.info("  Connect timeout: %s", self._connect_timeout_s)
        logger.info("  Verify TLS: %s", self._verify_tls)
        logger.info("  Force IPv4: %s", self._force_ipv4)
        logger.info("=== SOURCE INITIALIZED ===")

    async def run(self) -> None:
        """Instrumented run method with detailed error tracking."""
        logger.info("=== STARTING DUMP1090 SOURCE ===")

        # Session setup logging
        logger.info("Setting up HTTP session...")
        start_time = time.monotonic()

        try:
            await super().run()
        except Exception as e:
            logger.error("=== SOURCE RUN FAILED ===")
            logger.error("Exception type: %s", type(e).__name__)
            logger.error("Exception message: %s", str(e))
            logger.error("Runtime before failure: %.2fs", time.monotonic() - start_time)
            raise
        finally:
            logger.info("=== SOURCE RUN ENDED ===")

    async def _poll_once(self) -> bool:
        """Instrumented polling with detailed timing and error information."""
        logger.info("--- Starting poll cycle ---")
        cycle_start = time.monotonic()

        try:
            # Pre-request logging
            headers = {}
            if self._cache.etag:
                headers["If-None-Match"] = self._cache.etag
                logger.debug("Using cached ETag: %s", self._cache.etag)
            if self._cache.last_modified:
                headers["If-Modified-Since"] = self._cache.last_modified
                logger.debug(
                    "Using cached Last-Modified: %s", self._cache.last_modified
                )

            logger.info("Making HTTP request to: %s", self._url)
            logger.debug("Request headers: %s", headers)

            # DNS resolution timing
            if not self._dns_logged:
                logger.info("Performing DNS resolution...")
                dns_start = time.monotonic()

                try:
                    from urllib.parse import urlparse

                    parsed = urlparse(self._url)
                    host = parsed.hostname
                    port = parsed.port or (443 if parsed.scheme == "https" else 80)

                    if host:
                        infos = await asyncio.get_running_loop().getaddrinfo(host, port)
                        addrs = sorted({ai[4][0] for ai in infos})
                        dns_time = time.monotonic() - dns_start
                        logger.info("DNS resolution completed in %.3fs", dns_time)
                        logger.info("Resolved addresses: %s", addrs)
                        logger.info("Force IPv4: %s", self._force_ipv4)
                except Exception as e:
                    dns_time = time.monotonic() - dns_start
                    logger.warning("DNS resolution failed after %.3fs: %s", dns_time, e)
                finally:
                    self._dns_logged = True

            # HTTP request timing
            request_start = time.monotonic()
            logger.debug("Starting HTTP request...")

            assert self._session is not None, "Session not initialized"

            async with self._session.get(self._url, headers=headers) as resp:
                connect_time = time.monotonic() - request_start
                logger.info("HTTP connection established in %.3fs", connect_time)
                logger.info("Response status: %d %s", resp.status, resp.reason)
                logger.debug("Response headers: %s", dict(resp.headers))

                if resp.status == 304:
                    logger.info("Response: 304 Not Modified (using cached data)")
                    return False

                # Raise for HTTP errors with detailed logging
                if resp.status >= 400:
                    logger.error("HTTP error response: %d %s", resp.status, resp.reason)
                    logger.error("Response headers: %s", dict(resp.headers))
                    try:
                        error_text = await resp.text()
                        logger.error("Error response body: %s", error_text[:500])
                    except Exception:
                        logger.error("Could not read error response body")
                    resp.raise_for_status()

                # Cache header processing
                etag = resp.headers.get("ETag")
                if etag:
                    self._cache.etag = etag
                    logger.debug("Cached new ETag: %s", etag)

                lm = resp.headers.get("Last-Modified")
                if lm:
                    self._cache.last_modified = lm
                    logger.debug("Cached new Last-Modified: %s", lm)

                # Response body reading
                logger.debug("Reading response body...")
                body_start = time.monotonic()
                text = await resp.text()
                body_time = time.monotonic() - body_start

                total_time = time.monotonic() - request_start
                logger.info(
                    "Response received in %.3fs (connect: %.3fs, body: %.3fs)",
                    total_time,
                    connect_time,
                    body_time,
                )
                logger.info("Response size: %d bytes", len(text))

                # Timeout warning
                if total_time > self._timeout_s * 0.8:
                    logger.warning(
                        "Request took %.3fs (%.1f%% of timeout %.2fs)",
                        total_time,
                        100 * total_time / self._timeout_s,
                        self._timeout_s,
                    )

                # JSON parsing
                logger.debug("Parsing JSON response...")
                parse_start = time.monotonic()
                try:
                    data = json.loads(text)
                    parse_time = time.monotonic() - parse_start
                    logger.debug("JSON parsed in %.3fs", parse_time)
                except json.JSONDecodeError as e:
                    logger.error("JSON parsing failed: %s", e)
                    logger.error("Response text preview: %s", text[:200])
                    raise

                # Data processing
                logger.debug("Processing aircraft data...")
                process_start = time.monotonic()
                aircraft_count = len(data.get("aircraft", []))
                logger.info("Processing %d aircraft records", aircraft_count)

                self._handle_payload(data)

                process_time = time.monotonic() - process_start
                logger.debug("Data processed in %.3fs", process_time)

                cycle_time = time.monotonic() - cycle_start
                logger.info("--- Poll cycle completed in %.3fs ---", cycle_time)

                return True

        except asyncio.TimeoutError:
            cycle_time = time.monotonic() - cycle_start
            logger.error("=== TIMEOUT ERROR ===")
            logger.error("Timeout occurred after %.3fs", cycle_time)
            logger.error("Configured timeout: %.2fs", self._timeout_s)
            logger.error("Connect timeout: %s", self._connect_timeout_s)
            logger.error("URL: %s", self._url)
            logger.error(
                "Session config: verify_tls=%s, force_ipv4=%s",
                self._verify_tls,
                self._force_ipv4,
            )
            raise
        except Exception as e:
            cycle_time = time.monotonic() - cycle_start
            logger.error("=== REQUEST ERROR ===")
            logger.error("Error type: %s", type(e).__name__)
            logger.error("Error message: %s", str(e))
            logger.error("Time before error: %.3fs", cycle_time)
            logger.error("URL: %s", self._url)
            raise

    def _handle_payload(self, data: Dict[str, Any]) -> None:
        """Instrumented payload handling with detailed message processing."""
        logger.debug("=== PROCESSING PAYLOAD ===")

        # Timestamp processing
        now_s = data.get("now")
        logger.debug("Server timestamp: %s", now_s)

        if now_s is None:
            now_s = time.time()
            logger.debug("Using local timestamp: %s", now_s)

        ts = datetime.fromtimestamp(float(now_s), tz=timezone.utc)
        logger.debug("Parsed timestamp: %s", ts.isoformat())

        # Aircraft list processing
        aircraft_list = data.get("aircraft", [])
        logger.info("Found %d aircraft in response", len(aircraft_list))

        valid_aircraft = 0
        processed_aircraft = 0
        skipped_stale = 0

        for i, ac in enumerate(aircraft_list):
            if not isinstance(ac, dict):
                logger.warning("Aircraft %d: not a dictionary, skipping", i)
                continue

            # ICAO validation
            icao = ac.get("hex")
            if not self._is_valid_icao24(icao):
                logger.debug("Aircraft %d: invalid ICAO24 '%s', skipping", i, icao)
                continue

            icao = str(icao).strip().lower()

            # Staleness check
            seen = self._coerce_float(ac.get("seen")) or 0.0
            seen_pos = self._coerce_float(ac.get("seen_pos")) or 0.0

            if seen > 60.0 or seen_pos > 60.0:
                logger.debug(
                    "Aircraft %d (%s): stale data (seen=%.1f, seen_pos=%.1f), skipping",
                    i,
                    icao,
                    seen,
                    seen_pos,
                )
                skipped_stale += 1
                continue

            valid_aircraft += 1

            # Field extraction
            fields = {
                "callsign": ac.get("flight", "").strip() or None,
                "lat": self._coerce_float(ac.get("lat")),
                "lon": self._coerce_float(ac.get("lon")),
                "baro_alt": self._coerce_float(ac.get("alt_baro")),
                "geo_alt": self._coerce_float(ac.get("alt_geom")),
                "ground_speed": self._coerce_float(ac.get("gs")),
                "track_deg": self._coerce_float(ac.get("track")),
                "vertical_rate": self._coerce_float(ac.get("baro_rate")),
                "squawk": ac.get("squawk")
                if isinstance(ac.get("squawk"), str)
                else None,
                "nic": self._coerce_int(ac.get("nic")),
                "nacp": self._coerce_int(ac.get("nac_p")),
            }

            if debug_level >= 2:
                logger.debug("Aircraft %d (%s): %s", i, icao, fields)

            # Create message
            try:
                msg = AdsbMessage(
                    ts=ts,
                    icao24=icao,
                    callsign=fields["callsign"],
                    lat=fields["lat"],
                    lon=fields["lon"],
                    baro_alt=fields["baro_alt"],
                    geo_alt=fields["geo_alt"],
                    ground_speed=fields["ground_speed"],
                    track_deg=fields["track_deg"],
                    vertical_rate=fields["vertical_rate"],
                    squawk=fields["squawk"],
                    nic=fields["nic"],
                    nacp=fields["nacp"],
                    src="JSON",
                )

                # Publish to event bus
                msg_dict = msg.model_dump()
                msg_dict["ts"] = msg.ts.isoformat()
                asyncio.create_task(self._bus.publish(self._topic, pack(msg_dict)))
                processed_aircraft += 1

                if debug_level >= 1:
                    logger.debug(
                        "Published message for %s: %s",
                        icao,
                        f"pos=({fields['lat']},{fields['lon']}) alt={fields['baro_alt']}",
                    )

            except Exception as e:
                logger.warning(
                    "Aircraft %d (%s): failed to create message: %s", i, icao, e
                )

        logger.info(
            "Payload processing complete: %d total, %d valid, %d processed, %d stale",
            len(aircraft_list),
            valid_aircraft,
            processed_aircraft,
            skipped_stale,
        )
        logger.debug("=== PAYLOAD PROCESSED ===")

    def _is_valid_icao24(self, s: Any) -> bool:
        """Validate ICAO24 with logging."""
        if not isinstance(s, str):
            return False
        s = s.strip()
        if len(s) != 6:
            return False
        try:
            int(s, 16)
            return True
        except ValueError:
            return False

    def _coerce_float(self, v: Any) -> float | None:
        """Coerce value to float with error handling."""
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    def _coerce_int(self, v: Any) -> int | None:
        """Coerce value to int with error handling."""
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None


class TestRunner:
    """Main test runner that orchestrates the pipeline test."""

    def __init__(self, url: str, test_duration: float = 30.0):
        """Initialize test runner."""
        self.url = url
        self.test_duration = test_duration
        self.start_time = time.monotonic()

        # Statistics tracking
        self.stats = {
            "requests_attempted": 0,
            "requests_successful": 0,
            "requests_failed": 0,
            "messages_received": 0,
            "tracks_created": 0,
            "total_aircraft_seen": set(),
            "errors": [],
        }

        logger.info("=== TEST RUNNER INITIALIZED ===")
        logger.info("URL: %s", url)
        logger.info("Test duration: %.1fs", test_duration)

    async def run_test(self) -> None:
        """Run the complete pipeline test."""
        logger.info("=== STARTING PIPELINE TEST ===")

        # Initialize components (matching live_view.py exactly)
        logger.info("Initializing components...")

        ts = RealTimeSource()
        bus = EventBus()
        tracks = TrackService(bus, ts, expiry_s=300.0)

        # Create instrumented source
        src = InstrumentedDump1090Source(self.url, bus=bus, poll_hz=1.0)

        # Set up message monitoring
        message_sub = bus.subscribe("adsb.msg")
        track_sub = bus.subscribe("tracks.updated")

        logger.info("Starting services...")

        # Start track service
        await tracks.run()

        # Start source in background
        src_task = asyncio.create_task(src.run(), name="test_source")

        # Monitor messages and tracks
        monitor_task = asyncio.create_task(
            self._monitor_messages(message_sub, track_sub, tracks), name="test_monitor"
        )

        try:
            # Run for specified duration
            await asyncio.sleep(self.test_duration)
            logger.info("Test duration reached, stopping...")

        except KeyboardInterrupt:
            logger.info("Test interrupted by user")

        finally:
            # Cleanup
            logger.info("Cleaning up...")

            await src.stop()
            await tracks.stop()

            # Cancel tasks
            src_task.cancel()
            monitor_task.cancel()

            # Wait for cleanup
            await asyncio.gather(src_task, monitor_task, return_exceptions=True)

            # Close subscriptions
            await message_sub.close()
            await track_sub.close()
            await bus.close()

            # Print final statistics
            self._print_final_stats()

        logger.info("=== PIPELINE TEST COMPLETED ===")

    async def _monitor_messages(self, message_sub, track_sub, tracks) -> None:
        """Monitor incoming messages and track updates."""
        logger.info("Starting message monitoring...")

        try:
            # Monitor both message and track streams
            async def monitor_messages():
                async for envelope in message_sub:
                    try:
                        msg_data = unpack(envelope.payload)
                        self.stats["messages_received"] += 1

                        icao = msg_data.get("icao24", "unknown")
                        self.stats["total_aircraft_seen"].add(icao)

                        if debug_level >= 1:
                            logger.debug("Received message: %s", icao)

                    except Exception as e:
                        logger.warning("Error processing message: %s", e)

            async def monitor_tracks():
                async for envelope in track_sub:
                    try:
                        track_data = unpack(envelope.payload)
                        if debug_level >= 1:
                            logger.debug("Track update: %s", track_data)
                    except Exception as e:
                        logger.warning("Error processing track update: %s", e)

            # Run both monitors concurrently
            await asyncio.gather(monitor_messages(), monitor_tracks())

        except asyncio.CancelledError:
            logger.debug("Message monitoring cancelled")
        except Exception as e:
            logger.error("Error in message monitoring: %s", e)

    def _print_final_stats(self) -> None:
        """Print comprehensive test statistics."""
        runtime = time.monotonic() - self.start_time

        logger.info("=== FINAL TEST STATISTICS ===")
        logger.info("Total runtime: %.2fs", runtime)
        logger.info("Messages received: %d", self.stats["messages_received"])
        logger.info("Unique aircraft seen: %d", len(self.stats["total_aircraft_seen"]))
        logger.info(
            "Message rate: %.2f msg/s", self.stats["messages_received"] / runtime
        )

        if self.stats["total_aircraft_seen"]:
            logger.info(
                "Aircraft ICAOs: %s",
                sorted(list(self.stats["total_aircraft_seen"]))[:10],
            )

        if self.stats["errors"]:
            logger.info("Errors encountered: %d", len(self.stats["errors"]))
            for error in self.stats["errors"][:5]:  # Show first 5 errors
                logger.info("  %s", error)


async def main_async(args: argparse.Namespace) -> None:
    """Main async function matching live_view.py structure."""
    try:
        runner = TestRunner(args.url, args.duration)
        await runner.run_test()
    except Exception as e:
        logger.error("Test failed with exception: %s", e, exc_info=True)
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Test dump1090 fetch pipeline with detailed instrumentation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080/data/aircraft.json",
        help="dump1090 aircraft.json URL (default: %(default)s)",
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Test duration in seconds (default: %(default)s)",
    )

    parser.add_argument(
        "--debug-level",
        type=int,
        choices=[0, 1, 2, 3],
        default=1,
        help="Debug verbosity level (0=minimal, 3=maximum)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Set debug level globally
    global debug_level
    debug_level = args.debug_level
    os.environ["DEBUG_LEVEL"] = str(debug_level)

    # Adjust logging level based on debug level
    if debug_level == 0:
        logging.getLogger().setLevel(logging.WARNING)
    elif debug_level == 1:
        logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("Starting dump1090 fetch test...")
    logger.info("Debug level: %d", debug_level)
    logger.info("Target URL: %s", args.url)

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.info("Test interrupted")
        sys.exit(1)


if __name__ == "__main__":
    main()
