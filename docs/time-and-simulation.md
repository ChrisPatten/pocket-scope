# Time & Simulation

Many PocketScope services depend on monotonic time. To keep them testable and repeatable the application provides pluggable clock implementations in `src/pocketscope/core/time.py`.

## Time Sources

- **`RealTimeSource`** – Wraps `asyncio` sleep and Python's `time.monotonic()` for production use.
- **`SimTimeSource`** – Maintains an in-memory clock that advances only when instructed, making deterministic tests and scripted demos possible.
- **`DerivedClock` Helpers** – Utility wrappers expose convenience functions (for example, to schedule callbacks at a target rate).

Both clock types implement the same protocol (`monotonic()`, `sleep()`, `time()`) so they can be passed interchangeably into services.

## Working with Simulated Time

```python
from pocketscope.core.time import SimTimeSource

sim_ts = SimTimeSource(start=0.0)

async def run_scenario() -> None:
    await sim_ts.sleep(5.0)

# Advance the scenario by hand
sleep_task = asyncio.create_task(run_scenario())
sim_ts.advance(5.0)
await sleep_task
```

This pattern is used throughout the tests to remove real-time delays. Because every subscriber pulls time information from the injected clock, advancing the simulation ensures that expiry checks, trail updates, and UI animations all progress deterministically.

## Scheduling Tips

- **Cooperative Sleep** – Always call `await ts.sleep()` rather than `asyncio.sleep()` directly so your code respects the injected clock.
- **Bounded Drift** – Time sources expose helper methods for computing elapsed intervals (`elapsed = ts.monotonic() - start`). Use them to avoid accumulating floating-point drift.
- **Testing Timeouts** – When you need to assert that something happened within a window, advance the simulated clock manually instead of relying on `asyncio.wait_for`.

Deterministic time is a core enabler for golden-image rendering tests, record/replay workflows, and automated regression suites.
