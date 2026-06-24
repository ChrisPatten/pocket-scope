# White Screen on Pi — Diagnosis

**Date:** 2026-06-24
**Symptom:** On the Raspberry Pi, the UI loads and the theme is applied, then a few
seconds after startup the TFT "whites out" (goes to a blank white screen).

## Summary

The white screen is **not** a display hardware fault. It is a **false-positive
watchdog reset** triggered by slow software rendering:

> Slow geometry rendering → a frame exceeds the 2 s display watchdog → the
> watchdog resets the panel mid-write → VRAM clears to white and stays white.

Two independent bugs are involved:

- **Direct cause (white-out):** the ILI9341 hardware watchdog treats a slow frame
  as a hardware hang and resets a healthy panel, racing the render thread.
- **Root cause (perf):** the scope is parked at 80 nm range rendering thousands of
  boundary vertices on the Pi, so frames take 800–1945 ms.

A third, unrelated issue (network outage to the feeder) is what keeps the scope
parked at max range with no traffic.

## Chain of events

1. **The app is stuck at 80 nm range with 0 aircraft.**
   Default startup range is `100.0` (`bootstrap_assets/settings.yml:4`), clamped to
   the 80 nm ceiling (`max_range_nm=80.0`). Autoscale can't pull it back in because
   there are **0 tracks** — and the network is down
   (`dump1090 timeout ... adsb.chrispatten.dev`), so no aircraft ever arrive to
   trigger a zoom-in.

2. **At 80 nm, every frame renders the full US-state boundary + sector geometry.**
   Logs show `simp=raw=8695|out=514`, `state_simplify_ms≈617`, and `view_detail`
   `states≈1126 ms` + `sector≈196 ms`, plus `r_enc≈430 ms` RGB565 encoding.
   Result: frames take **800–1945 ms** (`ui.frame.slow`, `ui.slow_window`).

3. **The ILI9341 watchdog fires falsely.**
   `_watchdog_run` (`src/pocketscope/platform/display/ili9341_backend.py:767`) calls
   `_attempt_recover` whenever `time.monotonic() - self._last_ok > 2.0`. But
   `_last_ok` is only refreshed *after a complete frame push*
   (`ili9341_backend.py:645`). A legitimately slow ~2 s frame starves the timestamp,
   so the watchdog concludes the hardware hung when it's just software being slow.

4. **Recovery whites out the panel.**
   `_attempt_recover` → `reset_and_init()` does a software reset + display-on, which
   clears panel VRAM to **white** (`ili9341_backend.py:741-758`). Worse, it runs on
   the watchdog thread under `_recover_lock` (`:743`), while the render thread is
   mid-transfer writing frame chunks under a *different* lock (`SPI_BUS_LOCK`,
   `:627`). The two are not mutually exclusive, so the reset races the half-written
   frame and the panel stays white.

## Key code locations

| Concern | Location |
| --- | --- |
| Watchdog timeout (2.0 s) | `src/pocketscope/platform/display/ili9341_backend.py:767` |
| `_last_ok` only set after full push | `ili9341_backend.py:645` |
| Recovery resets panel → white VRAM | `ili9341_backend.py:741-758` |
| Lock mismatch: recovery vs. push | `_recover_lock` `:743` vs. `SPI_BUS_LOCK` `:627` |
| States render pipeline | `src/pocketscope/render/view_ppi.py:443-826` |
| Douglas–Peucker `_simplify` | `src/pocketscope/render/view_ppi.py:680-761` |
| Default startup range | `bootstrap_assets/settings.yml:4` (`range_nm: 100.0`) |
| Max range ceiling | `max_range_nm=80.0` |
| Feeder network timeout | `src/pocketscope/ingest/adsb/json_source.py` (poll to `adsb.chrispatten.dev`) |

## Resolution (implemented 2026-06-24)

**A. Display watchdog hardening** (`src/pocketscope/platform/display/ili9341_backend.py`)
- Added `_last_activity`, stamped when a frame *enters* `present()`/`end_frame()`
  and on push success. The watchdog now measures idleness from frame *delivery*,
  not the last completed push, so a slow-but-progressing render no longer looks
  like a hardware hang.
- Watchdog timeout is now a constructor arg `watchdog_timeout_s` (default **5 s**,
  was a hardcoded 2 s) — comfortably above worst-case frame time.
- `reset_and_init` now holds `SPI_BUS_LOCK` as well as `_recover_lock`, so a
  recovery can never interleave with an in-flight frame write.

**B. Range / perf** (`src/pocketscope/ui/controllers.py`, `bootstrap_assets/settings.yml`)
- The no-eligible-aircraft autoscale branch now respects `autoscale_max_range_nm`
  (50 nm) instead of falling back to the 80 nm controller ceiling — so a no-traffic
  view stays bounded. This helps the running device immediately (its settings
  already cap at 50 nm).
- Default `range_nm` lowered from `100.0` to `50.0` so startup is not above every
  ceiling.

**Tests added:**
- `tests/platform/test_ili9341_watchdog.py` — idle timer is driven by frame
  activity, not just completed pushes; timeout is configurable.
- `tests/ui/test_autoscale_no_traffic_clamp.py` — no-traffic clamps to the
  autoscale max, not the controller ceiling.

The watchdog change cures the symptom on its own; the range change removes what trips
it. The network outage to the feeder (nginx proxy down) was a separate issue and was
fixed independently.
