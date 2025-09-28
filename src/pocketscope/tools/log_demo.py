"""Generate sample logs in both human and JSON styles."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pocketscope.boot import boot
from pocketscope.logging import context_scope, new_request_id
from pocketscope.logging.instrumentation import log_call, measure_latency
from pocketscope.logging.telemetry import get_registry

logger = logging.getLogger("pocketscope.demo")


@log_call(level="INFO")
def simulate_interaction(action: str) -> None:
    logger.info("interaction", extra={"action": action})


@measure_latency(name="demo_latency_seconds")
def expensive_operation(duration: float) -> None:
    import time

    time.sleep(duration)


def main() -> None:
    demo_settings = Path(os.environ.get("POCKETSCOPE_SETTINGS", ""))
    boot(demo_settings if demo_settings.exists() else None)
    registry = get_registry()
    registry.counter("demo_runs_total").inc()
    with context_scope(request_id=new_request_id()):
        simulate_interaction("tap")
        expensive_operation(0.01)
    logger.warning("demo warning", extra={"range_nm": 20})


if __name__ == "__main__":
    main()
