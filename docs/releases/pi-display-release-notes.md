# Release notes — pi-display

Release date: 2025-09-12
Source branch: `pi-display` (compare vs `dev`)

Summary
-------

This release adds Raspberry Pi display and input support, rendering and UI improvements, performance tuning, and several new tests and assets. It is intended for users running PocketScope on Pi hardware but remains backwards-compatible for desktop/backed by Pygame.

Highlights
----------

- Added native SPI TFT display backend for ILI9341-compatible panels (`src/pocketscope/platform/display/ili9341_backend.py`).
- SPI locking helper to serialize frame buffer writes (`spi_lock.py`).
- Touch input driver for XPT2046 controllers (`xpt2046_touch.py`).
- Orientation flipping, softkeys and status overlay scaling improvements.
- Configurable frame rate and rendering performance optimizations.
- Support for local readsb/dump1090 services and fixes to JSON ingest handling.
- New runway asset data and runway lookup/store plus `airport_icon` renderer.
- New platform and unit tests targeting hardware codepaths; updated UI tests.

Notable commits
---------------

- a09240c  add initial tft support
- 4b4415a  update spi frame buffer logic
- 1416a6e  json fix for pi
- b289c80  timeout tweaks
- e1bdbfb  continue debugging json issue
- 17d5a58  configurable frame rate
- 87164af  performance optimizations
- 286ac08  adjust settings and config handling
- 1b0d576  fix status_overlay and softkeys scaling and configurability
- 84b8f70  support screen orientation flipping
- 4cb21bd  many ui/ux tweaks and fixes
- 642c009  hardware reliability tweaks
- 6544c83  tweak info block/line changes
- da38624  documentation updates
- 2afc604  add support for local readsb service
- eb685e0  fix renamed parameters
- 65ed8c1  finish getting things working on pi

Files of interest
-----------------

- New hardware/platform files:
  - `src/pocketscope/platform/display/ili9341_backend.py`
  - `src/pocketscope/platform/display/spi_lock.py`
  - `src/pocketscope/platform/input/xpt2046_touch.py`

- Rendering/UI:
  - `src/pocketscope/render/view_ppi.py` (ppi rendering updates)
  - `src/pocketscope/ui/status_overlay.py`, `ui/softkeys.py`, `ui/settings_screen.py`

- Ingest & assets:
  - `src/pocketscope/ingest/adsb/file_source.py`
  - `src/pocketscope/assets/runways.json`
  - `src/pocketscope/data/runways_store.py`

- Tests: new platform tests under `tests/platform/` and unit tests under `src/pocketscope/tests/`.

Upgrade notes
-------------

- If you plan to use the Pi hardware features, install the `pi` extras:

  pip install .[pi]

- The project uses conditional dependencies for Pygame vs `pygame-ce` per Python version. Ensure your environment matches your Python version and that `spidev`/`RPi.GPIO` are only installed on Pi.
- Settings schema and defaults were adjusted; on first run after upgrade, verify UI defaults and softkey mappings. Backup existing settings file if you have custom presets.

Testing and CI guidance
----------------------

- Several new hardware-focused tests were added. CI runners without Pi hardware must skip or mark these tests with an appropriate pytest marker (e.g., `@pytest.mark.skipif(not_on_pi, reason=...)`) or use an environment variable.
- Recommended local verification steps:

  - Non-Pi quick check (desktop):

    python -m pytest -q tests/ui/test_settings_screen.py::test_settings_screen

  - Pi hardware smoke test (with display + touch attached):

    pip install .[pi]
    # run a minimal init/test script or use the CLI flags to select ili9341 backend
    pocketscope --backend ili9341

Known limitations
-----------------

- The SPI backend and touch driver are hardware-specific and not exercised on CI by default. They require attached hardware and correct kernel drivers.
- Some golden-frame rendering tests were removed in favor of focused unit tests; if you rely on pixel-perfect golden tests, re-review the test matrix.

Security and stability notes
--------------------------

- SPI and touch code includes device I/O; ensure the process runs with appropriate permissions and that device nodes (e.g., `/dev/spidev0.0`) are correctly configured.
- The branch includes improved error handling and timeouts for ingest sources, but operators should monitor for transient JSON parse issues when pointing at older dump1090/readsb instances.

Release checklist
-----------------

- [ ] Confirm CI lint/type checks pass (mypy/ruff/black)
- [ ] Ensure hardware tests are gated in CI
- [ ] Update README/docs to highlight Pi support and install instructions (note: README in this branch already includes a pi note)
- [ ] Tag a release once merged (recommended: v0.2.0 if following semantic versioning for new hardware functionality)

Contacts / reviewers
--------------------

- Platform/Hardware: maintainers familiar with the `platform` and `render` modules
- UI/UX: reviewers for `ui/*` and `render/*` changes

How to include these notes in the PR
----------------------------------

Copy the contents of this file into the PR description or reference it directly from the branch. Use the commit list above as the changelog for reviewers.

---

Generated by local repo diff on branch `pi-display` vs `origin/dev`.
