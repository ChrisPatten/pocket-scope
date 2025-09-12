import asyncio
from typing import Any, cast

import pytest

from pocketscope.core.events import EventBus, pack, unpack


@pytest.mark.asyncio
async def test_basic_pub_sub() -> None:
    bus = EventBus(default_maxsize=8)
    sub = bus.subscribe("t1")

    async def consumer(collected: list[tuple[float, bytes]]) -> None:
        async for env in sub:
            collected.append((env.ts, env.payload))

    results: list[tuple[float, bytes]] = []
    consumer_task = asyncio.create_task(consumer(results))

    for i in range(3):
        await bus.publish("t1", pack({"i": i}))

    await asyncio.sleep(0)
    await bus.close()
    await consumer_task

    assert [unpack(p) for _, p in results] == [{"i": 0}, {"i": 1}, {"i": 2}]
    # Monotonic timestamps non-decreasing
    ts = [t for t, _ in results]
    assert ts == sorted(ts)


@pytest.mark.asyncio
async def test_multiple_subscribers() -> None:
    bus = EventBus(default_maxsize=8)
    s1 = bus.subscribe("t")
    s2 = bus.subscribe("t")

    out1: list[Any] = []
    out2: list[Any] = []

    async def c1() -> None:
        async for env in s1:
            out1.append(unpack(env.payload))

    async def c2() -> None:
        async for env in s2:
            out2.append(unpack(env.payload))

    t1 = asyncio.create_task(c1())
    t2 = asyncio.create_task(c2())

    for i in range(5):
        await bus.publish("t", pack(i))

    await bus.close()
    await asyncio.gather(t1, t2)

    assert out1 == [0, 1, 2, 3, 4]
    assert out2 == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_backpressure_drop_oldest() -> None:
    bus = EventBus(default_maxsize=2)
    sub = bus.subscribe("a")

    received: list[int] = []

    async def consumer() -> None:
        async for env in sub:
            received.append(int(unpack(env.payload)))

    ct = asyncio.create_task(consumer())

    # Publish 5 quickly without giving consumer time to drain
    for i in range(5):
        await bus.publish("a", pack(i))

    await bus.close()
    await ct

    # With drop-oldest and queue size 2, only last 2 should survive
    assert received == [3, 4]
    m = bus.metrics()
    stats = m.topics["a"]
    assert stats.drops == 3


@pytest.mark.asyncio
async def test_close_unblocks_subscribers() -> None:
    bus = EventBus()
    sub = bus.subscribe("z")

    async def consumer() -> int:
        n = 0
        async for _ in sub:
            n += 1
        return n

    t = asyncio.create_task(consumer())
    await asyncio.sleep(0.01)
    await bus.close()
    assert await t == 0


@pytest.mark.asyncio
async def test_concurrent_publishers() -> None:
    bus = EventBus(default_maxsize=128)
    sub = bus.subscribe("x")

    async def producer(n: int, count: int) -> None:
        for i in range(count):
            await bus.publish("x", pack((n, i)))
            # Yield control periodically to allow consumer to run
            if i % 5 == 0:
                await asyncio.sleep(0)

    outputs: list[tuple[int, int]] = []

    async def consumer() -> None:
        async for env in sub:
            outputs.append(cast(tuple[int, int], tuple(unpack(env.payload))))

    pcount = 5
    per = 50

    # Start consumer task first to ensure it gets scheduled
    ct = asyncio.create_task(consumer())

    # Give consumer a chance to start
    await asyncio.sleep(0)

    # Create and run producer tasks
    tasks = [asyncio.create_task(producer(n, per)) for n in range(pcount)]

    # Wait for all producers to finish
    await asyncio.gather(*tasks)

    # Close bus and wait for consumer to finish
    await bus.close()
    await ct

    assert len(outputs) == pcount * per
    # All tuples present (order across producers isn't guaranteed)
    assert set(outputs) == {(n, i) for n in range(pcount) for i in range(per)}


def test_pack_unpack_roundtrip() -> None:
    data = {
        "a": 1,
        "b": [1, 2, 3],
        "c": b"bytes",
    }
    b = pack(data)
    assert isinstance(b, (bytes, bytearray))
    back = unpack(b)
    assert back == data
