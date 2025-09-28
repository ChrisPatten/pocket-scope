from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pocketscope.data.cache import LRUCache, key_for_center
from pocketscope.data.db import get_connection, query_airports, query_runways, query_states
from pocketscope.data.ingest_geojson_to_sqlite import main as ingest_main
from pocketscope.map.data_provider import MapDataProvider


@pytest.fixture()
def map_fixtures_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "map"


@pytest.fixture()
def map_db_path(tmp_path: Path, map_fixtures_dir: Path) -> Path:
    db_path = tmp_path / "map.db"
    args = [
        "--airports",
        str(map_fixtures_dir / "airports_small.geojson"),
        "--runways",
        str(map_fixtures_dir / "runways_small.geojson"),
        "--states",
        str(map_fixtures_dir / "states_small.geojson"),
        "--out",
        str(db_path),
        "--replace",
    ]
    assert ingest_main(args) == 0
    return db_path


def test_ingest_counts(map_db_path: Path) -> None:
    conn = sqlite3.connect(map_db_path)
    try:
        airports = conn.execute("SELECT COUNT(*) FROM airports").fetchone()[0]
        runways = conn.execute("SELECT COUNT(*) FROM runways").fetchone()[0]
        states = conn.execute("SELECT COUNT(*) FROM us_states").fetchone()[0]
    finally:
        conn.close()
    assert airports == 2
    assert runways == 2
    assert states == 1


def test_rtree_prefilter_reduces_candidates(map_db_path: Path) -> None:
    with get_connection(str(map_db_path)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM airports").fetchone()[0]
        params = {"minx": -71.05, "maxx": -70.95, "miny": 41.95, "maxy": 42.05}
        cand = conn.execute(
            """
            SELECT COUNT(*) FROM airports_rtree
            WHERE minx <= :maxx AND maxx >= :minx
              AND miny <= :maxy AND maxy >= :miny
            """,
            params,
        ).fetchone()[0]
    assert total == 2
    assert cand == 1


def test_query_returns_expected_features(map_db_path: Path) -> None:
    with get_connection(str(map_db_path)) as conn:
        minx, miny, maxx, maxy = (-71.5, 41.5, -70.5, 42.5)
        # Test without extra_airports - should return empty set since TEST1, TEST2 don't match 3-alpha pattern
        airports = query_airports(conn, 42.0, -71.0, minx, miny, maxx, maxy, 150.0)
        idents = {ap["ident"] for ap in airports}
        assert idents == set()  # No airports should match the 3-alpha pattern
        
        # Test with extra_airports - should return the test airports
        airports = query_airports(conn, 42.0, -71.0, minx, miny, maxx, maxy, 150.0, ["TEST1", "TEST2"])
        idents = {ap["ident"] for ap in airports}
        assert {"TEST1", "TEST2"} <= idents
        
        runways = query_runways(conn, 42.0, -71.0, minx, miny, maxx, maxy, 150.0)
        states = query_states(conn, minx, miny, maxx, maxy)
    runway_ids = {rw["id"] for rw in runways}
    assert runway_ids == {"R1", "R2"}
    assert states and states[0]["name"] == "Test State"


def test_map_provider_uses_cache(map_db_path: Path) -> None:
    provider = MapDataProvider(str(map_db_path), cache=LRUCache(max_entries=4))
    first = provider.get_features_near(42.0, -71.0, ["TEST1", "TEST2"])
    second = provider.get_features_near(42.0, -71.0, ["TEST1", "TEST2"])
    assert first is second
    # Moving less than grid size should reuse cache key
    move_key = key_for_center(42.0, -71.0, 150.0)
    nearby_key = key_for_center(42.0001, -71.0001, 150.0)
    assert move_key == nearby_key
    third = provider.get_features_near(42.0001, -71.0001, ["TEST1", "TEST2"])
    assert third is second


def test_airport_identifier_filtering() -> None:
    """Test that airport filtering works correctly for 3-alpha chars and extra_airports."""
    from pocketscope.data.db import should_display_airport
    
    # Test 3-letter alpha identifiers (should be displayed)
    assert should_display_airport("LAX") == True
    assert should_display_airport("SFO") == True
    assert should_display_airport("JFK") == True
    assert should_display_airport("lax") == True  # case insensitive
    
    # Test identifiers with numbers (should NOT be displayed unless in extra list)
    assert should_display_airport("20AK") == False
    assert should_display_airport("4AZ8") == False
    assert should_display_airport("Z55") == False
    assert should_display_airport("KJFK") == False  # 4 chars, starts with K
    
    # Test extra_airports list
    extra = ["20AK", "4AZ8", "KJFK"]
    assert should_display_airport("20AK", extra) == True
    assert should_display_airport("4AZ8", extra) == True
    assert should_display_airport("KJFK", extra) == True
    assert should_display_airport("Z55", extra) == False  # not in extra list
    
    # Test 3-letter alpha still works with extra_airports
    assert should_display_airport("LAX", extra) == True
    assert should_display_airport("SFO", extra) == True
    
    # Test edge cases
    assert should_display_airport("") == False
    assert should_display_airport("  ") == False
