from __future__ import annotations

from pathlib import Path

from pocketscope.data.airports import load_airports_json, nearest_airports


def test_load_and_normalize(tmp_path: Path) -> None:
    # Use the fixture file
    path = "tests/data/airports_ma.json"
    airports = load_airports_json(path)
    idents = {ap.ident for ap in airports}
    assert "KBOS" in idents and "KBED" in idents
    # Types and normalization
    kbos = [ap for ap in airports if ap.ident == "KBOS"][0]
    assert isinstance(kbos.lat, float) and isinstance(kbos.lon, float)
    assert kbos.ident == "KBOS"


essentials_center = (42.00748, -71.20899)


def test_nearest_selection() -> None:
    airports = load_airports_json("tests/data/airports_ma.json")
    lat, lon = essentials_center
    near = nearest_airports(lat, lon, airports, max_nm=50.0, k=3)
    assert 1 <= len(near) <= 3
    # Expect a few likely candidates to be included if within range
    idents = {ap.ident for ap in near}
    expected_any = {"KBOS", "KBED", "KORH"}
    assert idents & expected_any


def test_cull_by_range() -> None:
    airports = load_airports_json("tests/data/airports_ma.json")
    lat, lon = essentials_center
    near_10 = nearest_airports(lat, lon, airports, max_nm=10.0, k=3)
    near_50 = nearest_airports(lat, lon, airports, max_nm=50.0, k=3)
    assert len(near_10) <= len(near_50)
