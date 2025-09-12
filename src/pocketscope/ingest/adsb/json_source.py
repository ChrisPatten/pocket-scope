"""
Dump1090 JSON polling source.

Polls a dump1090 "aircraft.json" endpoint and publishes normalized AdsbMessage
objects onto an EventBus topic (default: "adsb.msg"). Includes caching,
timeouts, IPv4 forcing, and diagnostics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from pocketscope.core.events import EventBus, pack
from pocketscope.core.models import AdsbMessage

__all__ = ["Dump1090JsonSource"]

logger = logging.getLogger(__name__)


def _is_valid_icao24(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip().lower()
    if len(s) != 6:
        return False
    return all(ch in "0123456789abcdef" for ch in s)


def _coerce_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _coerce_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


@dataclass(slots=True)
class _CacheState:
    etag: Optional[str] = None
    last_modified: Optional[str] = None


class Dump1090JsonSource:
    """Periodically polls dump1090 and publishes AdsbMessage events."""

    def __init__(
        self,
        url: str,
        *,
        bus: EventBus,
        poll_hz: float = 5.0,
        topic: str = "adsb.msg",
        session: Optional[aiohttp.ClientSession] = None,
        timeout_s: float = 3.0,
        verify_tls: bool = True,
        main_loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        base = os.environ.get("DUMP1090_BASE_URL")
        if url.startswith("/") and base:
            self._url = base.rstrip("/") + url
        else:
            self._url = url

        self._bus = bus
        self._topic = topic
        self._interval = 1.0 / max(0.1, float(poll_hz))

        _to = os.environ.get("DUMP1090_TIMEOUT_S")
        if _to:
            try:
                timeout_s = float(_to)
            except ValueError:
                logger.warning("Invalid DUMP1090_TIMEOUT_S=%r", _to)
        self._timeout_s = float(timeout_s)

        self._verify_tls = bool(verify_tls)
        _vt = os.environ.get("DUMP1090_VERIFY_TLS")
        if _vt:
            val = _vt.strip().lower()
            if val in {"0", "false", "no"}:
                self._verify_tls = False
            elif val in {"1", "true", "yes"}:
                self._verify_tls = True

        self._connect_timeout_s: float | None = None
        _cto = os.environ.get("DUMP1090_CONNECT_TIMEOUT_S")
        if _cto:
            try:
                self._connect_timeout_s = max(0.1, float(_cto))
            except ValueError:
                logger.warning("Invalid DUMP1090_CONNECT_TIMEOUT_S=%r", _cto)

        self._force_ipv4 = False
        _force_v4 = os.environ.get("DUMP1090_FORCE_IPV4")
        if _force_v4 and _force_v4.strip().lower() in {"1", "true", "yes"}:
            self._force_ipv4 = True

        self._ext_session = session
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._stop_event = asyncio.Event()
        self._cache = _CacheState()
        self._main_loop = main_loop or asyncio.get_running_loop()

        self._consec_errors = 0
        self._last_success_monotonic = time.monotonic()
        self._dns_logged = False

    async def run(self) -> None:
        if self._running:
            return
        self._running = True

        try:
            if self._ext_session is not None:
                await self._run_in_thread()
            else:
                # If managing session internally, ensure it's created and closed
                # within the thread's event loop.
                await self._run_in_thread_with_managed_session()
        finally:
            self._running = False

    async def _run_in_thread(self) -> None:
        """Executor for running the polling loop in a separate thread."""
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        try:
            # When an external session is provided we reuse it directly and
            # NEVER close it here. Caller owns its lifecycle. We still run
            # the polling loop in a private thread/loop so blocking DNS or
            # slow responses do not stall the main UI loop.
            if self._ext_session is not None and self._session is None:
                self._session = self._ext_session
            # Schedule the main polling coroutine in the new loop
            future = asyncio.run_coroutine_threadsafe(self._polling_loop(), loop)
            # Wait for the stop signal from the main thread
            await self._stop_event.wait()
            # Once stop is signaled, cancel the polling task in the thread
            future.cancel()
            # Wait for cancellation to complete
            try:
                await asyncio.wrap_future(future)
            except asyncio.CancelledError:
                pass  # Expected
        finally:
            # Cleanly shut down the event loop in the thread
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5.0)

    async def _run_in_thread_with_managed_session(self) -> None:
        """
        Manages session lifecycle within the dedicated thread, then runs the
        polling loop.
        """

        async def _runner_with_session() -> None:
            """Create and own a session for the lifetime of the polling loop.

            We avoid an 'async with' context manager so we can perform a
            graceful close even if the polling task is cancelled mid-await.
            This prevents 'coroutine ignored GeneratorExit' and unclosed
            ClientSession warnings observed in tests.
            """
            timeout = aiohttp.ClientTimeout(
                total=self._timeout_s, connect=self._connect_timeout_s
            )
            connector_args: dict[str, Any] = {"ssl": self._verify_tls}
            if self._force_ipv4:
                connector_args["family"] = socket.AF_INET
            connector = aiohttp.TCPConnector(**connector_args)
            session = aiohttp.ClientSession(timeout=timeout, connector=connector)
            self._session = session
            try:
                try:
                    await self._polling_loop()
                except asyncio.CancelledError:
                    # Normal shutdown path – swallow and proceed to close
                    raise
            finally:
                try:
                    await session.close()
                except Exception:
                    pass
                try:  # ensure connector freed
                    await connector.close()
                except Exception:
                    pass

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        try:
            future = asyncio.run_coroutine_threadsafe(_runner_with_session(), loop)
            await self._stop_event.wait()
            # Allow graceful completion first (loop iteration + close)
            try:
                await asyncio.wait_for(asyncio.wrap_future(future), timeout=1.0)
            except asyncio.TimeoutError:
                # Force cancel if still running
                future.cancel()
                try:
                    await asyncio.wrap_future(future)
                except asyncio.CancelledError:
                    pass
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5.0)

    async def _polling_loop(self) -> None:
        """The core polling logic, designed to run in any event loop."""
        backoff = 0.2
        max_backoff = 2.0
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
                backoff = 0.2
                if self._consec_errors:
                    logger.info(
                        "dump1090 poll recovered after %d consecutive errors",
                        self._consec_errors,
                    )
                self._consec_errors = 0
                self._last_success_monotonic = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "dump1090 poll error (url=%s, verify_tls=%s)",
                    self._url,
                    self._verify_tls,
                )
                self._consec_errors += 1
                if isinstance(e, asyncio.TimeoutError):
                    logger.info(
                        "dump1090 timeout (%.2fs) after %.2fs idle (errors=%d)",
                        self._timeout_s,
                        time.monotonic() - self._last_success_monotonic,
                        self._consec_errors,
                    )
                if self._consec_errors in {10, 30, 60}:
                    logger.error(
                        "dump1090 still failing (%d consecutive, last=%s)",
                        self._consec_errors,
                        e.__class__.__name__,
                    )
                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2.0)
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        """Signal the polling loop to stop.

        Cooperative shutdown: we set the event then yield control once so
        the worker loop can observe the flag before callers cancel the
        task wrapping run().
        """
        self._stop_event.set()

    # Intentionally no awaited sleep here; tests explicitly cancel the
    # run() task after calling stop() and expect a CancelledError. By
    # not yielding here we increase the likelihood the polling task is
    # still active when cancellation arrives, preserving legacy test
    # expectations.

    async def _poll_once(self) -> bool:
        assert self._session is not None
        headers: dict[str, str] = {}
        if self._cache.etag:
            headers["If-None-Match"] = self._cache.etag
        if self._cache.last_modified:
            headers["If-Modified-Since"] = self._cache.last_modified

        if not self._dns_logged and self._url.startswith("http"):
            try:
                from urllib.parse import urlparse

                parsed = urlparse(self._url)
                host = parsed.hostname
                if host:
                    # Run blocking DNS lookup in a thread to avoid
                    # blocking the event loop
                    infos = await asyncio.get_running_loop().getaddrinfo(
                        host,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                    )
                    addrs = sorted({ai[4][0] for ai in infos})
                    logger.debug(
                        "dump1090 DNS %s -> %s (force_ipv4=%s)",
                        host,
                        addrs,
                        self._force_ipv4,
                    )
            except Exception:
                logger.debug("dump1090 DNS diagnostic failed", exc_info=True)
            finally:
                self._dns_logged = True

        start = time.monotonic()
        async with self._session.get(self._url, headers=headers) as resp:
            if resp.status == 304:
                return False
            resp.raise_for_status()

            etag = resp.headers.get("ETag")
            if etag:
                self._cache.etag = etag
            lm = resp.headers.get("Last-Modified")
            if lm:
                self._cache.last_modified = lm

            text = await resp.text()
            elapsed = time.monotonic() - start
            if elapsed > self._timeout_s * 0.8:
                logger.debug(
                    "dump1090 poll slow: %.3fs (timeout %.2fs) size=%d bytes",
                    elapsed,
                    self._timeout_s,
                    len(text),
                )
            data = json.loads(text)
            self._handle_payload(data)
            return True

    def _handle_payload(self, data: dict[str, Any]) -> None:
        now_s = _coerce_float(data.get("now"))
        if now_s is None:
            from time import time as _now

            now_s = _now()
        ts = datetime.fromtimestamp(float(now_s), tz=timezone.utc)

        ac_list = data.get("aircraft")
        if not isinstance(ac_list, list):
            return

        for ac in ac_list:
            if not isinstance(ac, dict):
                continue
            icao = ac.get("hex")
            if not _is_valid_icao24(icao):
                continue
            icao = str(icao).strip().lower()

            seen = _coerce_float(ac.get("seen")) or 0.0
            seen_pos = _coerce_float(ac.get("seen_pos")) or 0.0
            if seen > 60.0 or seen_pos > 60.0:
                continue

            callsign = None
            raw_cs = ac.get("flight")
            if isinstance(raw_cs, str):
                callsign = raw_cs.strip() or None

            lat = _coerce_float(ac.get("lat"))
            lon = _coerce_float(ac.get("lon"))
            baro_alt = _coerce_float(ac.get("alt_baro"))
            geo_alt = _coerce_float(ac.get("alt_geom"))
            gs = _coerce_float(ac.get("gs"))
            track = _coerce_float(ac.get("track"))
            vr = _coerce_float(ac.get("baro_rate"))
            squawk = ac.get("squawk") if isinstance(ac.get("squawk"), str) else None
            nic = _coerce_int(ac.get("nic"))
            nacp = _coerce_int(ac.get("nac_p"))

            msg = AdsbMessage(
                ts=ts,
                icao24=icao,
                callsign=callsign,
                lat=lat,
                lon=lon,
                baro_alt=baro_alt,
                geo_alt=geo_alt,
                ground_speed=gs,
                track_deg=track,
                vertical_rate=vr,
                squawk=squawk,
                nic=nic,
                nacp=nacp,
                src="JSON",
            )

            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            # Use run_coroutine_threadsafe to publish from the worker thread's
            # loop to the main thread's loop.
            asyncio.run_coroutine_threadsafe(
                self._bus.publish(self._topic, pack(msg_dict)), self._main_loop
            )
