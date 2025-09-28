# Spatial DB & Theming / UI Enhancements Refactor

Target branch: `dev`
Source branch: `optimize-performance`

## Summary
Introduce a lightweight spatial data stack (SQLite + R*Tree + WKB) replacing
previous ad‑hoc in‑memory airport/runway/state handling. Adds geometry helpers,
map data provider caching, theming consolidation, vertical profile UI panel,
and refined airport identifier filtering. Substantial test expansion ensures
determinism and performance safety for Pi targets.

## Key Changes
1. Spatial / Data Layer
   - Added `data/spatial.py` (haversine, bbox, WKB encode/decode, centroids).
   - Added `data/db.py` query helpers registering custom `HAV_MILES` function.
   - Added ingestion script `data/ingest_geojson_to_sqlite.py` + schema.sql.
   - Introduced on‑disk R*Tree indices for airports, runways, US states.
   - Replaced legacy `data/airports.py` (removed) with DB + queries.
   - Added small LRU cache (`data/cache.py`) for tile/feature reuse.

2. Map Data Provider
   - New `map/data_provider.py` orchestrates spatial queries + cache key logic.
   - Provides `get_features_near(lat, lon, extra_airports)` returning structured
     airports/runways/states tuple for render layers.

3. Rendering / Theming
   - Added central `theme.py` and `ui/theme.py` to unify color + font tokens.
   - Airport icon + airports layer updated to theme awareness and geometry
     centroids from DB (removes bespoke per‑file parsing logic).
   - Added state boundary theming + tests (`test_states_theming.py`).

4. UI / Interaction
   - Added `ui/vertical_profile.py` (initial vertical profile panel scaffold).
   - Settings + softkeys updated to surface new altitude / autoscale options.
   - Removed obsolete `ui/settings_screen.py` in favor of modular controllers.

5. Airport Filtering Logic
   - Display rule: show airports with exactly 3 alphabetic chars (e.g. LAX,
     JFK) OR any ident explicitly added via `extra_airports` list.
   - Implemented in `should_display_airport()` with comprehensive tests.

6. Tests & Coverage
   - Added spatial unit tests (`tests/data/test_spatial.py`).
   - Added DB + ingestion tests (`tests/data/test_map_db.py`).
   - Added theming + airport icon tests (`test_airport_icon_theme.py`,
     `test_states_theming.py`, `test_theme.py`).
   - Added UI autoscale + vertical profile tests.
   - Removed superseded tests referencing deleted modules:
     * `tests/render/test_airports_unit.py`
     * `tests/ui/test_settings_screen.py`
     * `tests/src/pocketscope/tests/test_runways_store.py` (if existed; diff
       shows deletion of `test_runways_store.py`).

7. Tooling / Docs / Infra
   - Added `bootstrap.sh` for environment + data ingest bootstrap.
   - Added `Makefile` with common targets (bootstrap, tests, lint, ingest).
   - Expanded docs: `screenshots.md`, `spatial.md`, `theming.md`.
   - Updated `systemd-setup.md` and `README.md` to reflect spatial DB + new
     workflow.

## Motivation
Previous in‑memory JSON / per‑file parsing incurred repeated allocations and
non‑indexed spatial scans, impeding cold‑start and frame latency. Introducing a
single SQLite file with R*Tree reduces CPU for map feature queries, enabling
faster ring / label rendering within 5 FPS target at 20 aircraft while keeping
memory footprint modest. Central theming and caching reduce redundant style
and geometry calculations.

## Performance Considerations
* R*Tree + bounding box prefilter greatly reduces candidate feature sets.
* Haversine implemented in Python but confined to coarse candidate list; can
  be vectorized later if profiling demands.
* LRU cache prevents re‑query when panning within small deltas (< grid size).
* WKB storage avoids external GEOS/SpatiaLite deps, keeping deploy footprint
  low for Pi Zero 2 W.

## Breaking / Behavioral Changes
* Direct imports from removed `pocketscope.data.airports` are no longer valid.
  Use `MapDataProvider` queries.
* Airport display now filtered: non 3‑alpha idents suppressed unless provided
  in `extra_airports`. Adjust config / caller code if additional airports must
  render.
* Settings screen monolith removed; controllers modularized (ensure any local
  custom UI integrations adapt to new structure).

## Migration Guide
1. Run ingestion for bundled / custom GeoJSON:
   `python -m pocketscope.data.ingest_geojson_to_sqlite --airports airports.geojson --runways runways.geojson --states states.geojson --out map.db --replace`
2. Provide `MAP_DB_PATH` (if applicable) in runtime config / wiring.
3. Replace legacy airport/runway JSON access with:
   ```python
   provider = MapDataProvider(db_path)
   features = provider.get_features_near(lat, lon, extra_airports=["KJFK"])
   airports, runways, states = features.airports, features.runways, features.states
   ```
4. Use `theme` module tokens for new UI elements instead of hard‑coded tuples.

## Testing & Quality
* All pre‑commit hooks pass (black, ruff, isort, mypy, pytest).
* Test suite expanded (see Added Tests) – all green at submission.
* Warnings observed relate to external libs (pygame, PIL font absence) and are
  pre‑existing; no new warnings introduced by spatial layer.

## Future Follow‑Ups (Not in This PR)
* Vectorized / C‑accelerated haversine if profiling indicates hotspot.
* Additional thematic layers (terrain contours) once DB ingest extended.
* Configurable cache eviction metrics + telemetry.
* Optional async ingestion pipeline for dynamic map updates.

## Risk Assessment & Mitigations
| Risk | Mitigation |
|------|------------|
| Geometry parsing edge cases | Strict validation + tests on polygons & points |
| Airport visibility regressions | Explicit filtering tests + extra list param |
| Performance regressions | LRU caching + R*Tree bounding box prefilter |
| DB corruption | Simple recreate via ingestion script (`--replace`) |

## Verification Snapshot
Diff stats: 57 files changed, 26,470 insertions, 1,925 deletions.

## PR Checklist
- [x] New modules typed + docstrings
- [x] Tests added / updated
- [x] Removed obsolete code + tests
- [x] Docs updated (README + dedicated spatial/theming pages)
- [x] Pre‑commit hooks passing
- [x] No new failing tests or type errors

## Suggested PR Title
Spatial DB integration, theming consolidation, vertical profile, and airport filtering

## Suggested PR Description (One‑liner)
Add SQLite + R*Tree backed spatial data layer, centralized theming, vertical
profile UI, airport filtering logic, and expanded tests replacing legacy in‑
memory map data handling.
