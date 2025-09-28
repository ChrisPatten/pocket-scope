import asyncio
import os
from pathlib import Path

import pytest

from pocketscope.core.events import EventBus, unpack
from pocketscope.tools.config_watcher import ConfigWatcher
from pocketscope.theme import ThemeManager


@pytest.mark.asyncio
async def test_theme_file_change_triggers_event(tmp_path: Path, monkeypatch):
    # Point POCKETSCOPE_HOME to temp directory
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))
    home = tmp_path
    home.mkdir(parents=True, exist_ok=True)
    # Minimal settings file (empty mapping)
    (home / "settings.yml").write_text("{}\n", encoding="utf-8")
    # Initial themes override: patch bg color only
    (home / "themes.yml").write_text(
        """themes:\n  atc_classic:\n    palette:\n      bg: "#010101"\n""",
        encoding="utf-8",
    )

    # Ensure ThemeManager loads with our overridden POCKETSCOPE_HOME
    ThemeManager.reload({})
    before_bg = ThemeManager.color("bg")

    bus = EventBus()
    sub = bus.subscribe("theme.changed")
    watcher = ConfigWatcher(bus, poll_interval_s=0.05)
    await watcher.run()

    # Modify themes file with a new bg color
    await asyncio.sleep(0.1)
    (home / "themes.yml").write_text(
        """themes:\n  atc_classic:\n    palette:\n      bg: "#020202"\n""",
        encoding="utf-8",
    )
    os.utime(home / "themes.yml")  # ensure mtime change

    # Wait for watcher to detect change and publish. Access internal queue since
    # Subscription does not expose a non-iter interface; acceptable for tests.
    new_env = None
    from pocketscope.core.events import Envelope  # local import for typing

    for _ in range(40):  # up to ~2s
        await asyncio.sleep(0.05)
        try:
            env = sub._queue.get_nowait()  # type: ignore[attr-defined]
        except Exception:
            continue
        if isinstance(env, Envelope) and env.topic == "theme.changed":
            new_env = env  # type: ignore[assignment]
            break

    await watcher.stop()

    assert new_env is not None, "theme.changed event not published"
    payload = unpack(new_env.payload)
    assert payload["theme"] == ThemeManager.theme().name
    after_bg = ThemeManager.color("bg")
    assert before_bg != after_bg, "Background color did not change after edit"