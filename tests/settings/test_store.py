from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from pocketscope.settings.schema import Settings
from pocketscope.settings.store import SettingsStore, SettingsLoadError


def test_load_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))
    s = SettingsStore.load()
    assert isinstance(s, Settings)
    assert s.units == "nm_ft_kt"


def test_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))
    s = Settings(units="km_m_kmh", range_nm=5.0)
    SettingsStore.save(s)
    s2 = SettingsStore.load()
    assert s2.units == "km_m_kmh"
    assert s2.range_nm == 5.0


def test_unknown_field_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))
    p = SettingsStore.settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"units": "nm_ft_kt", "bogus_field": 1}))
    with pytest.raises(SettingsLoadError):
        SettingsStore.load()


def test_legacy_track_length_mode_allows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))
    p = SettingsStore.settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Provide only legacy key; should migrate to numeric track_length_s
    p.write_text(yaml.safe_dump({"track_length_mode": "medium"}))
    s = SettingsStore.load()
    assert abs(s.track_length_s - 45.0) < 1e-6


def test_corrupt_raises_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))
    p = SettingsStore.settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{broken")
    with pytest.raises(SettingsLoadError):
        SettingsStore.load()


@pytest.mark.asyncio
async def test_save_debounced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POCKETSCOPE_HOME", str(tmp_path))
    calls: list[int] = []

    orig = SettingsStore.save

    def fake_save(settings: Settings) -> None:
        calls.append(1)
        orig(settings)

    monkeypatch.setattr(SettingsStore, "save", fake_save)
    s = Settings()
    SettingsStore.save_debounced(s, delay_s=0.1)
    SettingsStore.save_debounced(s, delay_s=0.1)
    await asyncio.sleep(0.2)
    assert len(calls) == 1
    data = yaml.safe_load(SettingsStore.settings_path().read_text())
    assert data["units"] == "nm_ft_kt"
