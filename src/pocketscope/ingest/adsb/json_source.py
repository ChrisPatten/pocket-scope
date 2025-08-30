"""
Dump1090 JSON polling source.

This module provides Dump1090JsonSource which periodically polls a dump1090
"aircraft.json" endpoint and publishes normalized AdsbMessage events to the
EventBus. It implements light caching via ETag/Last-Modified and backs off on
transient failures.

Expected dump1090 fields (per-aircraft object):
 - hex: ICAO24 in lowercase hex (required)
 - flight: Callsign (optional, may include trailing spaces)
 - lat, lon: Position (optional; publish state without position if missing)
 - alt_baro: Baro altitude in feet (optional)
 - alt_geom: Geometric altitude in feet (optional)
 - gs: Ground speed in knots (optional)
 - track: Course over ground in degrees true (optional)
 - baro_rate: Vertical rate (ft/min) (optional)
 - squawk: Transponder code as string (optional)
 - nic: Navigation Integrity Category (optional)
 - nac_p: Navigation Accuracy Category for Position (optional)
 - seen, seen_pos: Seconds since last overall/pos update (optional; used to skip stale)

Top-level fields:
 - now: Wall time (seconds since epoch). Used for message timestamp if present.

Only aircraft with a valid 6-hex ICAO24 are published. Entries with obviously
stale timestamps (seen/seen_pos > 60 seconds) are skipped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
    for ch in s:
        if ch not in "0123456789abcdef":
            return False
    return True


def _coerce_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        # Reject NaN/inf implicitly by float() then isfinite? Keep simple here.
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
    """
    Polls dump1090 'aircraft.json' and publishes AdsbMessage to topic 'adsb.msg'.
    Default poll rate: 5 Hz (every 0.2 s). Handles transient errors with backoff.
    """

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
    ) -> None:
        # Support relative path via env var base
        base = os.environ.get("DUMP1090_BASE_URL")
        if url.startswith("/") and base:
            self._url = base.rstrip("/") + url
        else:
            self._url = url

        self._bus = bus
        self._topic = topic
        self._interval = 1.0 / max(0.1, float(poll_hz))
        self._timeout_s = float(timeout_s)
        self._verify_tls = bool(verify_tls)
        self._ext_session = session
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._stop_event = asyncio.Event()
        self._cache = _CacheState()

    async def run(self) -> None:
        if self._running:
            return
        self._running = True

        # Prepare session if not provided
        if self._ext_session is not None:
            self._session = self._ext_session
        else:
            timeout = aiohttp.ClientTimeout(total=self._timeout_s)
            connector = aiohttp.TCPConnector(ssl=self._verify_tls)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)

        backoff = 0.2
        max_backoff = 2.0

        try:
            while not self._stop_event.is_set():
                try:
                    await self._poll_once()
                    # Reset backoff on success
                    backoff = 0.2
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"dump1090 poll error: {e}")
                    # Exponential backoff up to max_backoff
                    await asyncio.sleep(backoff)
                    backoff = min(max_backoff, backoff * 2.0)
                # Maintain cadence
                await asyncio.sleep(self._interval)
        finally:
            if self._session and self._ext_session is None:
                try:
                    await self._session.close()
                except Exception:
                    pass
            self._running = False

    async def stop(self) -> None:
        self._stop_event.set()

    async def _poll_once(self) -> bool:
        assert self._session is not None
        headers: dict[str, str] = {}
        if self._cache.etag:
            headers["If-None-Match"] = self._cache.etag
        if self._cache.last_modified:
            headers["If-Modified-Since"] = self._cache.last_modified

        async with self._session.get(self._url, headers=headers) as resp:
            if resp.status == 304:
                return False
            resp.raise_for_status()

            # Cache headers
            etag = resp.headers.get("ETag")
            if etag:
                self._cache.etag = etag
            lm = resp.headers.get("Last-Modified")
            if lm:
                self._cache.last_modified = lm

            # Parse JSON payload
            # We read text then json.loads to avoid aiohttp's type ignoring
            text = await resp.text()
            data = json.loads(text)
            self._handle_payload(data)
            return True

    def _handle_payload(self, data: dict[str, Any]) -> None:
        # Determine timestamp
        now_s = _coerce_float(data.get("now"))
        if now_s is None:
            from time import time as _now

            now_s = _now()
        ts = datetime.fromtimestamp(float(now_s), tz=timezone.utc)

        ac_list = data.get("aircraft")
        if not isinstance(ac_list, list):
            return

        for ac in ac_list:
            if not isinstance(ac, dict):  # defensive
                continue
            icao = ac.get("hex")
            if not _is_valid_icao24(icao):
                continue
            icao = str(icao).strip().lower()

            # Staleness check
            seen = _coerce_float(ac.get("seen")) or 0.0
            seen_pos = _coerce_float(ac.get("seen_pos")) or 0.0
            if seen > 60.0 or seen_pos > 60.0:
                continue

            callsign_raw = ac.get("flight")
            callsign = None
            if isinstance(callsign_raw, str):
                callsign = callsign_raw.strip() or None

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

            # Serialize with ISO timestamp for msgpack
            msg_dict = msg.model_dump()
            msg_dict["ts"] = msg.ts.isoformat()
            # Fire-and-forget publish (async caller)
            # Use create_task? EventBus.publish is async; call directly but do
            # not await here. However, we're in sync context; schedule
            # publishing via asyncio.create_task to avoid blocking on many
            # aircraft.
            asyncio.create_task(self._bus.publish(self._topic, pack(msg_dict)))
