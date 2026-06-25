# Rendering & Display

PocketScope renders the radar-style view using a layered canvas abstraction. Rendering code lives under `src/pocketscope/render/` and `src/pocketscope/ui/`.

## Canvas Abstraction

`render/canvas.py` defines drawing primitives (lines, circles, polygons, text) and colour handling that every backend implements. This decouples scene composition from the actual display technology.

## Layers & Views

- **`render/layers/*.py`** – Each overlay (airports, sectors, aircraft, trails) lives in a dedicated layer class. Layers subscribe to bus topics or data services and render onto the canvas when asked.
- **`render/view_ppi.py`** – The primary north-up plan position indicator. It draws range rings, ownship, aircraft glyphs, and data blocks using geometry helpers from `core/geo.py`. Aircraft glyph fill was simplified to always use the neutral `ac.level.fill` colour; vertical speed is now communicated (in "simple label" mode) via a small ▲ / ▼ arrow appended to the one-line label when climb/descent exceeds ±200 fpm (arrow coloured with `ac.climb.fill` / `ac.desc.fill`).
- **`render/labels.py`** – Handles ATC-style label layout, collision avoidance, and typography controls (font size, gaps, and alignment).

## Input Handling

`ui/controller.py` coordinates focus management, gesture handling, and layer composition. Input events arrive from the active backend (mouse, touch, soft keys) and are translated into actions such as range changes or aircraft pinning.

## Display Backends

- **Pygame** – The default desktop backend. Supports windowed or headless rendering, PNG snapshots, and keyboard shortcuts.
- **ILI9341 SPI TFT** – Targets the Raspberry Pi handheld hardware. Includes RGB565 encoding, hardware watchdog integration, and rotation options.
- **Web View** – Streams canvas updates over WebSocket to a minimal browser-based client for remote monitoring.

All backends share the same controller API. Switching between them is done through CLI flags (`--backend pygame`, `--backend tft`, `--backend web`).

## Testing & Automation

- **Deterministic Frames** – Rendering tests under `tests/render/` and `tests/ui/` compare generated frames against golden PNGs using the simulated clock.
- **Screenshot Hooks** – The UI exposes screenshot triggers that can be activated by keyboard shortcuts, soft keys, Unix signals, or dropping marker files. See [Screenshot automation](screenshots.md) for workflows.
- **Theme Reloading** – Palette changes saved to the configuration file propagate to the active controller so you can tune colours while the app is running.

When adding a new overlay or backend, focus on implementing the canvas protocol and keeping stateful logic inside the appropriate layer or service.

## Performance (Raspberry Pi)

The Pi handheld (ILI9341 SPI TFT) is the performance-critical target. A few
techniques keep the PPI frame within budget; understand them before optimising.

### Cached label rasterisation

`PillowCanvas.text()` does not re-rasterise glyphs every frame. `FontCache`
holds a color-independent **coverage mask** (L-mode) per `(text, size_px)`,
bounded by an LRU. `text()` pastes a solid fill through the cached mask, which
is pixel-identical to `ImageDraw.text` (same default anchor and `textbbox`
metrics, same 8-bit coverage blend). Semi-transparent fills and multi-line
strings fall back to the direct draw path so that identity guarantee holds.
This cut repeated label cost (sector names, cardinals, airport tags, ATC data
blocks) by 4–7× on-device. Pixel-equivalence is locked by
`tests/platform/test_pillow_text_cache.py`.

### Vectorised boundary geometry

`render/geom_np.py` holds the shared NumPy fast paths used by both the
state-boundary (`view_ppi.py`) and sector (`sectors_layer.py`) overlays:

- `_ring_visible_np` – view-radius cull (vertex / edge / enclosure test).
- `_rdp_keep_mask_np` – iterative Ramer–Douglas–Peucker simplification.
- `_simplify_ring_np` – adaptive-tolerance RDP with ENU projection.
- `_any_within_nm` – vectorised haversine cull (matches `core.geo.haversine_nm`).

Each is locked to its scalar original by equivalence tests
(`tests/render/test_states_vectorized.py`, `tests/render/test_sectors_cull.py`)
so a future change can't silently alter what gets culled or simplified. State
geometry is also rebuilt only when range, rotation, or center change (cached in
`render/geo_cache.py`); a static view reuses projected screen vertices.

### Profiling caveat: stage timing vs. the SPI push

The `ui.perf` log emits `view_detail=` per-stage millisecond figures. **These
are wall-clock, not CPU**, and can mislead. The SPI transmit of the framebuffer
(240×320×2 bytes) runs in a background thread; the render stages call into
Pillow C routines that release the GIL, so the SPI push overlaps whichever stage
is executing. The first heavy stage (sectors) absorbs that wait and can read as
~180 ms even though `SectorsLayer.draw()` is only ~5–6 ms of actual CPU. Always
confirm a stage is CPU-bound with internal timers before optimising it. The
genuine remaining bottleneck is the SPI transmit itself (bandwidth-bound);
levers there are the SPI clock rate and dirty-rectangle / partial updates.
