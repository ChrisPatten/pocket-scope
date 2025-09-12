from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from pocketscope.core.events import EventBus, unpack
from pocketscope.core.models import AdsbMessage
from pocketscope.ingest.adsb.json_source import Dump1090JsonSource


async def _start_test_server(
    responses: list[tuple[int, dict[str, Any]]],
) -> tuple[web.AppRunner, web.TCPSite, str]:
    """Start a simple aiohttp server that serves a sequence of JSON responses.

    Each call pops the next (status, body) tuple; if exhausted, repeat last.
    """
    app = web.Application()

    state = {"idx": 0}

    async def handler(request: web.Request) -> web.Response:
        i = state["idx"]
        if i >= len(responses):
            i = len(responses) - 1
        status, body = responses[i]
        state["idx"] = min(state["idx"] + 1, len(responses))
        return web.json_response(body, status=status)

    app.router.add_get("/data/aircraft.json", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    # mypy: aiohttp's private attribute is not typed; narrow at runtime
    server = site._server
    assert server is not None
    sockets = getattr(server, "sockets", None)
    assert sockets, "Server sockets not available"
    port = sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/data/aircraft.json"
    return runner, site, url


@pytest.mark.asyncio
async def test_dump1090_json_source_maps_and_publishes(
    tmp_path: Path, load_fixture
) -> None:
    # Prepare fixture and server
    fixture = load_fixture("aircraft_sample.json")
    runner, site, url = await _start_test_server([(200, fixture)])

    bus = EventBus()
    sub = bus.subscribe("adsb.msg")
    src = Dump1090JsonSource(url, bus=bus, poll_hz=20.0)

    task = asyncio.create_task(src.run())

    # Collect a few messages
    msgs: list[AdsbMessage] = []

    try:
        # Wait for a couple publishes
        for _ in range(5):
            env = await asyncio.wait_for(sub.__anext__(), timeout=2.0)
            d = unpack(env.payload)
            # Restore ts for validation
            d["ts"] = datetime.fromisoformat(d["ts"].replace("Z", "+00:00"))
            msgs.append(AdsbMessage.model_validate(d))
            if len(msgs) >= 2:
                break

        assert msgs, "No messages received"
        # Pick the first with lat/lon for stronger checks
        m = None
        for x in msgs:
            if x.lat is not None and x.lon is not None:
                m = x
                break
        assert m is not None
        # Validate basic mapping
        assert len(m.icao24) == 6 and m.icao24 == m.icao24.lower()
        # These fields come from the fixture
        assert isinstance(m.lat, float)
        assert isinstance(m.lon, float)
        # Optional numeric fields preserved when present
        if m.baro_alt is not None:
            assert isinstance(m.baro_alt, float)
        if m.ground_speed is not None:
            assert isinstance(m.ground_speed, float)
        if m.track_deg is not None:
            assert isinstance(m.track_deg, float)

    finally:
        await src.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await sub.close()
        await bus.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_dump1090_json_source_backoff_and_recovery(load_fixture) -> None:
    # First response 500, then good
    fixture = load_fixture("aircraft_sample.json")
    runner, site, url = await _start_test_server(
        [(500, {"error": "boom"}), (200, fixture)]
    )

    bus = EventBus()
    sub = bus.subscribe("adsb.msg")
    src = Dump1090JsonSource(url, bus=bus, poll_hz=20.0)

    task = asyncio.create_task(src.run())

    try:
        # First poll fails; ensure we still eventually get a message
        env = await asyncio.wait_for(sub.__anext__(), timeout=5.0)
        d = unpack(env.payload)
        assert _is_valid_icao(d["icao24"])  # helper below
    finally:
        await src.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await sub.close()
        await bus.close()
        await runner.cleanup()


def _is_valid_icao(s: Any) -> bool:
    return isinstance(s, str) and len(s) == 6 and s == s.lower()
