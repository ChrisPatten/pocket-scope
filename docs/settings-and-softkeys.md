# Settings & Soft Keys

PocketScope persists user preferences and exposes quick actions through a configurable soft key bar. This guide covers how settings are stored, validated, and applied at runtime.

## Configuration Storage

- Settings live at `~/.pocketscope/settings.json` by default. Override the directory by setting the `POCKETSCOPE_HOME` environment variable.
- The file contains UI preferences such as units, range, altitude filters, theme overrides, and demo mode flags.
- Writes are debounced to avoid excessive disk churn when multiple options change in quick succession.

## Validation & Hot Reload

`core/config.py` loads the JSON file into strongly-typed Pydantic models. When the file changes on disk:

1. The `ConfigWatcher` notices the modification.
2. The watcher validates the new contents.
3. A configuration update event is published on the bus.
4. Active controllers apply the changes live (for example, updating colours or range limits) without restarting the process.

Invalid updates log validation errors but keep the previous configuration active so the UI never enters a broken state.

## Soft Key Bar

`ui/softkeys.py` defines a row of context-aware buttons for the handheld hardware. Typical bindings include:

- Range adjustments (increase/decrease)
- Unit toggles (imperial/metric)
- Demo mode toggle
- Altitude filter presets
- Screenshot capture

Soft keys emit events back into the controller, which then updates settings or triggers one-off actions. The soft key bar is optional during development; enable it by constructing the bar and calling `UiController.enable_softkeys()`.

## Extending Settings

1. Add new fields to the settings model with sensible defaults.
2. Expose the option in the CLI or UI so users can modify it.
3. Update the watcher to broadcast relevant events if additional services need to react.
4. Document the option here and, if necessary, link to more detailed guides.

Centralising configuration management keeps PocketScope responsive to user tweaks while maintaining predictable behaviour across sessions.
