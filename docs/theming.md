# Theming & Palette

PocketScope ships with a lightweight theming system (see `pocketscope/theme.py`) that
replaces hard‑coded UI colors with named palette keys. The active theme is selected
via the persisted settings file (`theme` field, default `"atc_classic"`) and can be
customized per key using the `themeOverrides` mapping. Overrides accept hex values in
any of these forms: `#RGB`, `#RRGGBB`, `#RGBA`, `#RRGGBBAA` (alpha optional).

Lookup API:

```python
from pocketscope.theme import ThemeManager

# Ensure loaded (Settings model or simple dict with theme/themeOverrides).
ThemeManager.load({"theme": "atc_classic", "themeOverrides": {"range.ring": "#2C7A2C"}})

rgba = ThemeManager.color("label.text.primary")      # -> (r, g, b, a)
packed = ThemeManager.rgb565("ac.level.fill")        # 16‑bit RGB565 (for TFT)
```

Live reload: whenever settings are reloaded at runtime the configuration layer calls
`ThemeManager.reload(settings_dict)` so draws after a user edit immediately reflect
new colors without restarting.

## Palette Keys

The `atc_classic` built‑in palette defines all required keys (unit test enforces coverage):

### Core scope & layers
```
bg
range.ring, range.tick, range.text
map.border
sector.line, sector.label
airport.marker, airport.text
```

### Aircraft & tracks
```
ac.level.fill, ac.level.stroke
ac.climb.fill, ac.desc.fill   # used for vertical speed arrow indicators in simple labels
ac.pinned.stroke, ac.focus.stroke
track.head (trail fades head -> background color automatically)
```

### Labels & overlays
```
label.text.primary, label.text.dim, label.text.other, label.halo
status.bg, status.text
status.badge.live.bg, status.badge.live.text
status.badge.delay.bg, status.badge.delay.text
status.badge.stale.bg, status.badge.stale.text
infobar.accent
```

### Vertical profile panel
```
vprof.bg, vprof.border, vprof.text, vprof.muted,
vprof.axis, vprof.trace,
vprof.vs.pos, vprof.vs.neg, vprof.vs.neutral
```

Alpha: Any key may specify 8‑digit hex (e.g. `track.head` = `#6EFF6E80`). Trails
fade smoothly from `track.head` toward the background color (`bg`). Aircraft glyphs
now always use the neutral `ac.level.fill`; vertical rate dynamics are expressed via
the optional label arrow (so adjusting `ac.climb.fill` / `ac.desc.fill` only affects
that indicator, not the glyph body). The former `track.tail` key and unused keys
(`map.coast`, `compass.cardinal`, `compass.tick`, `infobar.bg`, `infobar.text`,
`alert.bg`, `alert.text`) were removed as the related UI elements are no longer
rendered. Overrides specifying removed keys are ignored; you can delete them from
settings with no functional impact. When alpha is omitted it defaults to 255.

## Adding a Theme
1. Add a new entry to the `THEMES` dict in `pocketscope/theme.py` with all required keys.
2. Run the test suite – `tests/ui/test_theme.py` will fail if any key is missing.
3. (Optional) Provide documentation/examples for the new theme in this file.

## Overriding Colors at Runtime
Edit your settings JSON (typically in the user config directory) and add:

```json
{
    "theme": "atc_classic",
    "themeOverrides": {
    "range.ring": "#2C7A2C",
        "status.badge.delay.bg": "#FF8800"
    }
}
```

The UI will automatically reload these values on save (no restart required).

## Fallback & Robustness
If a lookup key is missing or an override is invalid, the manager falls back to the
theme's base value. If the theme name is unknown it falls back to `atc_classic`.

---

See `README.md` for a broader project overview.
