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
