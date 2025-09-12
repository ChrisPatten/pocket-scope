## PR Notes: branch `pi-display` vs `dev`

Summary
-------

This document summarizes the changes on the `pi-display` branch relative to `dev`. It lists commits, files added/modified/deleted, notable functional changes, tests added/updated, migration/upgrade notes, and suggested reviewer guidance and QA steps.

Context
-------

- Source branch: `pi-display` (current)
- Target branch: `dev` (origin/dev)

Suggested PR title: "pi-display: add SPI TFT/ILI9341 backend, touch input, and UI/performace improvements"

Commits on branch (top → oldest)
---------------------------------

The branch contains the following commits (abbreviated SHA and message):

- 65ed8c1  finish getting things working on pi
- eb685e0  fix renamed parameters
- 77b2401  Merge branch 'dev' into pi-display
- 2afc604  add support for local readsb service
- da38624  documentation updates
- 6544c83  tweak info block/line changes
- 642c009  hardware reliability tweaks
- 4cb21bd  many ui/ux tweaks and fixes
- 84b8f70  support screen orientation flipping
- 1b0d576  fix status_overlay and softkeys scaling and configurability
- 286ac08  adjust settings and config handling
- 87164af  performance optimizations
- 17d5a58  configurable frame rate
- e1bdbfb  continue debugging json issue
- b289c80  timeout tweaks
- 1416a6e  json fix for pi
- 4b4415a  update spi frame buffer logic
- a09240c  add initial tft support

Files changed (summary)
-----------------------

Added
- .rsync-exclude
- docs/adsb-data-flow.md
- docs/systemd-setup.md
- src/pocketscope/assets/runways.json
- src/pocketscope/config.py
- src/pocketscope/data/runways_store.py
- src/pocketscope/ingest/adsb/file_source.py
- src/pocketscope/platform/display/ili9341_backend.py
- src/pocketscope/platform/display/spi_lock.py
- src/pocketscope/platform/input/xpt2046_touch.py
- src/pocketscope/render/airport_icon.py
- src/pocketscope/tests/test_airport_icon.py
- src/pocketscope/tests/test_runways_store.py
- src/pocketscope/tools/test_dump1090_fetch.py
- tests/platform/test_ili9341_backend.py
- tests/platform/test_ili9341_chunking.py
- tests/platform/test_integration_smoke.py
- tests/platform/test_xpt2046_touch.py
- tests/ui/test_altitude_filter_custom_bounds.py
- tests/ui/test_track_length_custom.py

Modified
- .pre-commit-config.yaml
- .vscode/settings.json
- README.md (pi branch content and version note)
- pyproject.toml (dependencies / project metadata)
- src/pocketscope/__init__.py
- src/pocketscope/app/live_view.py
- src/pocketscope/cli.py
- src/pocketscope/core/geo.py
- src/pocketscope/core/tracks.py
- src/pocketscope/ingest/adsb/__init__.py
- src/pocketscope/ingest/adsb/json_source.py
- src/pocketscope/ingest/adsb/playback_source.py
- src/pocketscope/platform/display/pygame_backend.py
- src/pocketscope/render/airports_layer.py
- src/pocketscope/render/labels.py
- src/pocketscope/render/sectors_layer.py
- src/pocketscope/render/view_ppi.py
- src/pocketscope/settings/schema.py
- src/pocketscope/settings/values.py
- src/pocketscope/settings/values.yml
- src/pocketscope/tools/config_watcher.py
- src/pocketscope/tools/record_replay.py
- src/pocketscope/ui/controllers.py
- src/pocketscope/ui/settings_screen.py
- src/pocketscope/ui/softkeys.py
- src/pocketscope/ui/status_overlay.py
- tests/core/test_tracks.py
- tests/ingest/test_dump1090_json_source.py
- tests/ui/test_settings_screen.py
- tests/ui/test_softkeys_settings.py
- tests/ui/test_track_length_trimming.py

Deleted
- src/pocketscope/examples/__init__.py
- src/pocketscope/examples/live_view.py
- tests/render/test_render_golden.py

Notable functional changes and rationale
--------------------------------------

- Embedded Pi display support: new `ili9341_backend.py` plus SPI locking (`spi_lock.py`) and updated framebuffer logic. This enables direct SPI TFT display support on Raspberry Pi hardware.
- Touch input: `xpt2046_touch.py` backend for XPT2046 touch controllers and tests under `tests/platform/`.
- Platform abstraction: additions and updates to `platform/display/*` backends and tests to support both Pygame and native SPI displays.
- UI/UX improvements: many tweaks to softkeys, status overlay, settings screen, scaling, and support for screen orientation flipping.
- Performance: frame rate configurability, rendering and track performance optimizations, and timeout tuning for network sources.
- Ingest sources: new file-based ADS-B ingest (`file_source.py`) and tweaks to `json_source`/`playback_source` to fix JSON issues and add support for local readsb.
- Airports / Runways: new runway data asset and a `runways_store` plus tests and an `airport_icon` renderer.
- Tests: multiple new platform and unit tests added to cover the new hardware codepaths and UI features. Some golden-render tests were removed (deleted file), likely replaced with more targeted tests.

Potential risks / review focus
----------------------------

- Hardware-only code: `ili9341_backend.py`, `spi_lock.py`, and `xpt2046_touch.py` must be reviewed carefully for race conditions, device locking, and error recovery — these run on Pi-only hardware and are gated by optional dependencies in `pyproject.toml`.
- Dependency markers: `pyproject.toml` was modified; ensure the conditional pygame vs pygame-ce logic and pi extras remain correct.
- Settings and schema changes: `settings/schema.py` and `values.yml` changed — confirm any default-setting migrations are backward-compatible and documented.
- Tests added: CI may need to skip Pi-hardware tests on non-Pi runners; ensure pytest markers or environment gating are present so CI doesn't fail on GitHub actions runners without hardware.
- Deleted golden render test: confirm its removal was intentional and replaced by other validations.

Files / areas to review closely
------------------------------

- src/pocketscope/platform/display/ili9341_backend.py
- src/pocketscope/platform/display/spi_lock.py
- src/pocketscope/platform/input/xpt2046_touch.py
- src/pocketscope/render/view_ppi.py (rendering changes)
- src/pocketscope/ui/status_overlay.py and softkeys
- src/pocketscope/settings/* (schema, values.yml)
- tests/platform/* (ensure CI gating)

Quick QA / smoke test steps
--------------------------

Run unit tests locally (fast subset):

```bash
python -m pytest -q tests/platform/test_ili9341_backend.py::test_ili9341_init -q
python -m pytest -q src/pocketscope/tests/test_runways_store.py -q
```

Run full test suite locally (may be slow):

```bash
python -m pytest -q
```

If testing on a Raspberry Pi with hardware attached:

1. Install pi extras: `pip install .[pi]` or use the `pi` extra from `pyproject.toml`.
2. Run the app in display mode: `pocketscope --backend ili9341` (or use configuration/cli flags added on this branch).

Suggested merge checklist for the PR
----------------------------------

- [ ] Confirm CI passes and that platform tests are appropriately skipped on non-Pi runners.
- [ ] Request review from maintainers experienced with Pi hardware (recommend: platform and render owners).
- [ ] Sanity-check `pyproject.toml` for dependency markers and version code paths.
- [ ] Verify settings schema migration/backwards compatibility and update docs if defaults changed.
- [ ] Verify README or docs mention new pi features and how to enable them.

Quality gates triage
--------------------

- Build: N/A (no compiled artifacts added). Status: Not run (file-only change).
- Lint/typecheck: Not run here. Recommend running `ruff`/`mypy` in CI and locally.
- Unit tests: Several new tests added — ensure CI gating. Status: Not run locally by this notes generator.
- Smoke test: Manual smoke steps provided above; recommend hardware test on Pi.

Requirements coverage
---------------------

- Diff branch vs dev and create comprehensive PR notes in a new md file: Done (this file)

Next steps / ownership
----------------------

- Please review the proposed changes in the files listed above.
- Create a PR using this branch against `dev`. Use the suggested title and copy relevant sections from this file into the PR description.
- If you want, I can also open a draft PR locally (create commit + push + open PR) — say the word and I will prepare the commit and push instructions.

-- End of PR notes
