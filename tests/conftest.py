from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture(fixtures_dir: Path) -> Callable[[str], Any]:
    def _load(name: str) -> Any:
        with (fixtures_dir / name).open("r", encoding="utf-8") as f:
            return json.load(f)

    return _load


@pytest.fixture(autouse=True)
def _isolate_settings_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force an isolated POCKETSCOPE_HOME per test.

    The stricter settings validation now raises on unknown keys; without
    isolation a developer's real ~/.pocketscope/settings.yml (which may
    include experimental / deprecated fields) could cause unrelated tests
    to fail. This keeps the test environment hermetic.
    """
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path / "pscope_home"))
