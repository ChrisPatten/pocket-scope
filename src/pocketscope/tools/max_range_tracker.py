"""Max-range tracker for ADS-B targets.

Tracks, for each ICAO24, the farthest horizontal distance (NM) observed
from a given site location, along with bearing/azimuth (deg true), the
aircraft altitude (ft) at that point, and the position/time of the record.

Provides a small CLI that can ingest from:
 - dump1090 JSON endpoint (via existing Dump1090JsonSource), or
 - a JSONL playback trace (via FilePlaybackSource),
and writes a JSON file with per-ICAO maxima and a summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pocketscope.core.events import EventBus, Subscription, unpack
from pocketscope.core.geo import range_bearing_from
from pocketscope.core.models import AdsbMessage
from pocketscope.core.time import RealTimeSource, TimeSource


@dataclass
class MaxRangeRecord:
    icao24: str
    distance_nm: float
    bearing_deg: float
    alt_ft: Optional[float]
    lat: float
    lon: float
    ts_iso: str


class MaxRangeTracker:
    """Tracks per-ICAO maximum horizontal range from a fixed site."""

    def __init__(self, *, site_lat: float, site_lon: float) -> None:
        self._site_lat = float(site_lat)
        self._site_lon = float(site_lon)
        self._records: Dict[str, MaxRangeRecord] = {}
        self._sub: Subscription | None = None
        self._running: bool = False

    def records(self) -> Dict[str, MaxRangeRecord]:
        return self._records

    async def run(self, bus: EventBus, topic: str = "adsb.msg") -> None:
        if self._running:
            return
        self._running = True
        self._sub = bus.subscribe(topic)
        try:
            async for env in self._sub:
                data = unpack(env.payload)
                # Coerce ts back to datetime if serialized as ISO string
                ts = data.get("ts")
                if isinstance(ts, str):
                    try:
                        data["ts"] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        data["ts"] = datetime.now(timezone.utc)
                try:
                    msg = AdsbMessage.model_validate(data)
                except Exception:
                    continue
                self._process(msg)
        finally:
            self._running = False
            if self._sub is not None:
                await self._sub.close()
                self._sub = None

    def _process(self, msg: AdsbMessage) -> None:
        # Need position to compute range
        if msg.lat is None or msg.lon is None:
            return
        rng_nm, brg_deg = range_bearing_from(
            self._site_lat, self._site_lon, float(msg.lat), float(msg.lon)
        )
        # Altitude preference: geometric, else barometric
        alt_ft = msg.geo_alt if msg.geo_alt is not None else msg.baro_alt

        cur = self._records.get(msg.icao24)
        if cur is None or rng_nm > cur.distance_nm:
            rec = MaxRangeRecord(
                icao24=msg.icao24,
                distance_nm=float(rng_nm),
                bearing_deg=float(brg_deg),
                alt_ft=float(alt_ft) if alt_ft is not None else None,
                lat=float(msg.lat),
                lon=float(msg.lon),
                ts_iso=msg.ts.astimezone(timezone.utc).isoformat(),
            )
            self._records[msg.icao24] = rec

    def to_json(self) -> str:
        payload = {
            "summary": self._summary(),
            "per_icao": {k: asdict(v) for k, v in sorted(self._records.items())},
        }
        return json.dumps(payload, indent=2)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    def _summary(self) -> Dict[str, Any]:
        n = len(self._records)
        farthest: Optional[MaxRangeRecord] = None
        for r in self._records.values():
            if farthest is None or r.distance_nm > farthest.distance_nm:
                farthest = r
        s: Dict[str, Any] = {"icao_count": n}
        if farthest is not None:
            s["max_distance_nm"] = farthest.distance_nm
            s["max_icao24"] = farthest.icao24
            s["bearing_deg"] = farthest.bearing_deg
            s["alt_ft"] = farthest.alt_ft
            s["lat"] = farthest.lat
            s["lon"] = farthest.lon
            s["ts_iso"] = farthest.ts_iso
        return s


async def _run_with_source(args: argparse.Namespace) -> None:
    ts: TimeSource = RealTimeSource()
    bus = EventBus()
    tracker = MaxRangeTracker(
        site_lat=float(args.center[0]), site_lon=float(args.center[1])
    )

    # Choose source
    src: Any
    if args.playback:
        # Lazy import to avoid optional deps at import time
        from pocketscope.ingest.adsb.playback_source import FilePlaybackSource

        src = FilePlaybackSource(
            args.playback, ts=ts, bus=bus, speed=float(args.speed), loop=True
        )
    else:
        from pocketscope.ingest.adsb.json_source import Dump1090JsonSource

        src = Dump1090JsonSource(args.url, bus=bus, poll_hz=float(args.poll_hz))

    # Run source + tracker + periodic saver
    async def saver_task() -> None:
        interval = float(args.save_interval)
        out = args.out
        if not out:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                tracker.save(out)
                # Emit a brief status line to console
                summary = tracker._summary()
                icao_count = summary.get("icao_count", 0)
                max_nm = summary.get("max_distance_nm", 0.0)
                max_icao = summary.get("max_icao24", "-")
                ts_iso = summary.get("ts_iso", "")
                print(
                    f"[maxrange] saved {out} | ICAOs {icao_count} | farthest {max_nm:.1f} nm ({max_icao}) @ {ts_iso}",  # noqa: E501
                    flush=True,
                )
            except Exception:
                pass

    t_src = asyncio.create_task(src.run(), name="adsb_source")
    t_trk = asyncio.create_task(tracker.run(bus), name="max_range_tracker")
    t_svr = asyncio.create_task(saver_task(), name="periodic_saver")

    try:
        await asyncio.gather(t_src, t_trk)
    except asyncio.CancelledError:
        pass
    finally:
        # On exit, save once
        if args.out:
            try:
                tracker.save(args.out)
            except Exception:
                pass
        # Stop source
        try:
            await src.stop()
        except Exception:
            pass
        for t in (t_src, t_trk, t_svr):
            if not t.done():
                t.cancel()
        await asyncio.gather(t_src, t_trk, t_svr, return_exceptions=True)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Track per-ICAO max range from a site")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--url",
        default="http://127.0.0.1:8080/data/aircraft.json",
        help="dump1090 aircraft.json URL",
    )
    src.add_argument("--playback", help="Path to ADS-B JSONL trace for playback")
    p.add_argument(
        "--center",
        type=lambda s: tuple(map(float, s.split(","))),
        required=True,
        help="Site center lat,lon (degrees)",
    )
    p.add_argument("--out", type=str, required=True, help="Output JSON path")
    p.add_argument(
        "--poll-hz", type=float, default=1.0, help="Poll rate for dump1090 JSON"
    )
    p.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier")
    p.add_argument(
        "--save-interval", type=float, default=30.0, help="Periodic save interval (s)"
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(_run_with_source(args))
    except KeyboardInterrupt:
        pass


__all__ = ["MaxRangeTracker", "MaxRangeRecord", "main", "parse_args"]

if __name__ == "__main__":
    main()
