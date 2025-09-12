"""
Local JSON file polling ADS-B source.

Reads a local dump1090-style JSON file (aircraft.json) at a configurable
poll rate and publishes normalized AdsbMessage objects onto an EventBus
topic (default: "adsb.msg"). The implementation mirrors the public
interface of Dump1090JsonSource (run/stop) so it can be used
interchangeably from the examples.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pocketscope.core.events import EventBus, pack
from pocketscope.core.models import AdsbMessage

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


class LocalJsonFileSource:
    """Poll a local JSON file and publish ADS-B messages.

    Args:
        path: path to the JSON file (dump1090 "aircraft.json" style)
        bus: EventBus to publish to
        poll_hz: how often to read the file (defaults to 1.0)
        topic: EventBus topic (default: "adsb.msg")
    """

    def __init__(
        self, path: str, *, bus: EventBus, poll_hz: float = 1.0, topic: str = "adsb.msg"
    ) -> None:
        self._path = Path(path)
        self._bus = bus
        self._topic = topic
        self._interval = 1.0 / max(0.1, float(poll_hz))
        self._running = False
        self._stop_event = asyncio.Event()
        self._main_loop = asyncio.get_running_loop()
        # Track last modification so we can optionally skip identical reads
        self._last_mtime: float | None = None

    async def run(self) -> None:
        if self._running:
            return
        self._running = True

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        try:
            future = asyncio.run_coroutine_threadsafe(self._polling_loop(), loop)
            await self._stop_event.wait()
            future.cancel()
            try:
                await asyncio.wrap_future(future)
            except asyncio.CancelledError:
                pass
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5.0)
            self._running = False

    async def stop(self) -> None:
        self._stop_event.set()

    async def _polling_loop(self) -> None:
        """Loop that reads the JSON file and publishes messages."""
        backoff = 0.2
        max_backoff = 2.0
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
                backoff = 0.2
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("local json file poll error %s", self._path)
                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2.0)
            await asyncio.sleep(self._interval)

    async def _poll_once(self) -> bool:
        """Read the file and publish any contained ADS-B messages."""
        p = self._path
        try:
            stat = p.stat()
        except FileNotFoundError:
            logger.debug("local json file not found: %s", p)
            return False

        mtime = float(stat.st_mtime)
        # If file unchanged since last read, still process (user asked to refresh)
        # but we track mtime in case caller wants to skip identical reads later.
        try:
            with p.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            logger.warning("failed to read local json %s: %s", p, e)
            return False

        # Use same payload handling as the dump1090 source
        self._handle_payload(data)
        self._last_mtime = mtime
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
            # Publish into main loop
            try:
                asyncio.run_coroutine_threadsafe(
                    self._bus.publish(self._topic, pack(msg_dict)), self._main_loop
                )
            except Exception:
                logger.exception("failed to publish adsb message from local file")
