AutoScale Controller

The AutoScale controller proposes dynamic PPI range and altitude-band
updates to keep ~N aircraft visible. It's implemented in
`src/pocketscope/ui/autoscale_controller.py` and is driven by an
`autoscale` block in `settings.json`.

Key points:

- Pure logic: module returns proposals and does not perform UI I/O.
- Tunables live in `settings.json` under the `autoscale` key. Defaults
  are provided by the application and can be overridden.
- The live viewer (`pocketscope.app.live_view`) creates a background
  1 Hz task that ticks the controller and applies safe proposals.

Example `settings.json` snippet:

{
  "autoscale": {
    "enabled": true,
    "target_count": 12
  }
}

Full list of supported `autoscale` settings and defaults

The live viewer reads the `autoscale` object from your `settings.json`.
Below are every supported key, the default value, and a short meaning.

- `enabled` (bool, default: true)
  - Enable/disable the autoscaler background controller.
- `target_count` (int, default: 12)
  - Desired number of aircraft to keep visible in the PPI view.
- `deadband_low_ratio` (float, default: 0.8)
  - Low threshold as a fraction of `target_count` before scaling out/expanding.
- `deadband_high_ratio` (float, default: 1.2)
  - High threshold as a fraction of `target_count` before scaling in/trimming.
- `ema_alpha` (float, default: 0.4)
  - Exponential moving average smoothing factor applied to observed counts.
- `confirm_ticks` (int, default: 2)
  - Number of consecutive 1-second ticks the condition must hold before a change is proposed.
- `radius_nm_min` (float, default: 3.0)
  - Minimum allowed PPI radius (nautical miles).
- `radius_nm_max` (float, default: 60.0)
  - Maximum allowed PPI radius (nautical miles).
- `zoom_step_factor_in` (float, default: 0.8696)
  - Multiplier applied to radius when zooming in (makes radius smaller).
- `zoom_step_factor_out` (float, default: 1.15)
  - Multiplier applied to radius when zooming out (makes radius larger).
- `alt_min_floor_ft` (int, default: 0)
  - Lowest allowed altitude floor (ft) for autoscale altitude band.
- `alt_max_ceiling_ft` (int, default: 45000)
  - Highest allowed altitude ceiling (ft) for autoscale altitude band.
- `alt_step_expand_ft` (int, default: 2000)
  - How much to expand the altitude ceiling (ft) when traffic is low and zoom out isn't possible.
- `alt_margin_ft` (int, default: 500)
  - Margin added when snapping altitude ceilings/floors to keep focused aircraft visible.
- `alt_min_band_ft` (int, default: 1500)
  - Minimum width (ft) of an altitude band when trimming ceilings.
- `zoom_cooldown_s` (float, default: 2.0)
  - Minimum seconds between radius changes (debounce for zoom actions).
- `alt_cooldown_s` (float, default: 2.5)
  - Minimum seconds between altitude-band changes.
- `max_changes_per_5s` (int, default: 2)
  - Rate limit: maximum number of changes allowed over a rolling 5 second window.
- `prefer_zoom_bias` (float, default: 0.7)
  - Bias used when deciding whether to prefer zoom changes over altitude-band changes.
- `protect_focused` (bool, default: true)
  - If true, the controller will attempt to keep the focused/selected aircraft in view and inside the altitude band.
- `include_high_when_quiet` (bool, default: true)
  - When traffic is very light this can influence whether high-altitude aircraft are considered; see implementation for details.

Complete example `settings.json` including all defaults

{
  "autoscale": {
    "enabled": true,
    "target_count": 12,
    "deadband_low_ratio": 0.8,
    "deadband_high_ratio": 1.2,
    "ema_alpha": 0.4,
    "confirm_ticks": 2,
    "radius_nm_min": 3.0,
    "radius_nm_max": 60.0,
    "zoom_step_factor_in": 0.8696,
    "zoom_step_factor_out": 1.15,
    "alt_min_floor_ft": 0,
    "alt_max_ceiling_ft": 45000,
    "alt_step_expand_ft": 2000,
    "alt_margin_ft": 500,
    "alt_min_band_ft": 1500,
    "zoom_cooldown_s": 2.0,
    "alt_cooldown_s": 2.5,
    "max_changes_per_5s": 2,
    "prefer_zoom_bias": 0.7,
    "protect_focused": true,
    "include_high_when_quiet": true
  }
}

See `tests/test_autoscale_controller.py` for unit-test examples and the
built-in demo harness in `autoscale_controller.py`.
