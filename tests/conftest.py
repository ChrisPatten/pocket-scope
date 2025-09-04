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
