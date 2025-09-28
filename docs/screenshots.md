Screenshots & Automation
========================

PocketScope provides a lightweight, backend-agnostic screenshot facility so you can capture the current PPI (and overlays) without disrupting the frame loop. Images are written as PNG files.

Output Location
---------------

All screenshots default to:

```
~/.pocketscope/screenshots/
```

The directory is created automatically (falls back to `./screenshots/` if the home path is not writable).

Trigger Methods
---------------

1. Soft Key: A `Shot` soft key (when the bar is enabled) immediately saves a PNG and prints the path.
2. Keyboard: Press `F12` in the desktop (pygame) window.
3. Signal: Send `SIGUSR1` to the live viewer process (works well under `systemd`).
4. Command File Drop: Create a file named `screenshot` (any suffix accepted, e.g. `screenshot123`) inside:
   `~/.pocketscope/commands/` — the UI loop scans ~2× per second, deletes the file, and captures a screenshot.
5. API Call: Invoke `UiController.screenshot(path=None)` directly from code. Provide a `path` string to override the destination file name.
6. Deferred (Signal/File) Path: `UiController.request_screenshot()` sets a flag so capture happens safely on the next frame boundary (used internally by signal + file triggers).

Example (Python API)
--------------------

```python
from pocketscope.ui.controllers import UiController

# ui: existing UiController instance
path = ui.screenshot()  # returns '/home/user/.pocketscope/screenshots/pocketscope-YYYYMMDD-HHMMSS.png'
print("Saved", path)

# Deferred (signal-style) request
ui.request_screenshot()  # will save on next frame; non-blocking
```

Example (systemd signal)
------------------------

```bash
sudo systemctl kill -s SIGUSR1 pocketscope.service
journalctl -u pocketscope.service -n 20 | grep screenshot
```

Command File Trigger
--------------------

```bash
mkdir -p ~/.pocketscope/commands
touch ~/.pocketscope/commands/screenshot_my_note
# Within ~0.5s a PNG will appear in ~/.pocketscope/screenshots
```

Notes
-----

- The operation is best-effort; backend failures are logged but non-fatal.
- The mechanism is safe for signal contexts (actual file I/O deferred to main loop).
- PNG contents reflect the fully rendered frame including overlays, labels, and vertical profile panel if active.

Testing
-------

See `tests/ui/test_screenshot.py` for the stub display test ensuring `UiController.screenshot()` writes a PNG file and returns the path.

Related Theme Keys
------------------

Screenshots naturally include any active theme; modify `theme` / `themeOverrides` in `settings.json` to immediately change captured color palettes.

See Also
--------

- `README.md` (Theming & Palette, UI Controls)
- `docs/theming.md` (full palette key reference)
*** End Patch