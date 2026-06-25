# Changelog

All notable changes to PocketScope are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version is the single source of truth in `src/pocketscope/__init__.py`
(`__version__`); bump it together with the entry here.

## [Unreleased]

## [0.3.0] - 2026-06-24

Raspberry Pi display stability and rendering performance pass. Targets the
ILI9341 SPI TFT handheld build; desktop behaviour is unchanged.

### Fixed
- **White-screen blackout on the Pi.** The display watchdog measured idleness
  from SPI activity and fired false-positive resets seconds after startup,
  whiting out the screen. It now measures idleness from frame delivery with a
  5 s timeout, and recovery is serialized through the SPI bus lock. Verified
  zero spurious recoveries on-device. See `WHITE_SCREEN_DIAGNOSIS.md`.
- **Autoscale parking at max range with no traffic.** When no aircraft were
  eligible, autoscale ignored `autoscale_max_range_nm`; it now clamps to the
  configured min/max. Default `range_nm` lowered to 50 nm.

### Added
- **Cached label rasterisation** (`PillowCanvas` / `FontCache`). Text glyphs
  are cached as color-independent coverage masks (LRU-bounded) and pasted as a
  solid fill, pixel-identical to `ImageDraw.text`. Cuts repeated label cost
  (sector names, cardinals, airport tags, ATC data blocks) 4–7×.
- **Shared vectorised boundary-geometry helpers** (`render/geom_np.py`):
  `_ring_visible_np`, `_rdp_keep_mask_np`, `_simplify_ring_np`, `_any_within_nm`,
  reused by both the state-boundary and sector overlays.
- Equivalence tests locking the NumPy fast paths to their scalar originals
  (cull, RDP, ENU batch) and pixel-equivalence tests for the glyph cache.

### Changed
- **RGB565 encode vectorised with NumPy.** The Pillow rawmode fast path never
  engaged (no packer in Pillow 12.1); the new NumPy path drops per-frame encode
  from ~432 ms to ~5 ms on-device.
- **State-boundary geometry rebuild gated on view change** and vectorised
  (cull + RDP simplify + ENU projection). Static-view `states` render dropped
  from ~648 ms (per rebuild) to ~1 ms; rebuilds now occur only when range,
  rotation, or center actually change.
- **Sector overlay** now culls via NumPy and RDP-simplifies outlines
  (~93k → ~21k vertices) with a process-lifetime array cache.

### Performance
- Combined on-device result: PPI `frame_ms` ~370 → ~305; per-frame encode and
  several label-heavy stages cut several-fold.
- Profiling note: the apparent "sectors ≈ 180 ms" stage cost was a wall-clock
  attribution artifact — `SectorsLayer.draw()` is ~5.6 ms of CPU, but its
  GIL-releasing PIL calls let the concurrent SPI push thread run during that
  stage. The genuine remaining bottleneck is the SPI transmit (~70–175 ms),
  which is bandwidth-bound.

## [0.2.0]

Baseline prior to this changelog. See the git history for details.

[Unreleased]: https://github.com/ChrisPatten/pocket-scope/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/ChrisPatten/pocket-scope/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ChrisPatten/pocket-scope/releases/tag/v0.2.0
