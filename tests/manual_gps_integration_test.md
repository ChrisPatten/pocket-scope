# GPS Integration Manual Test Guide

This document describes how to manually test the GPS integration end-to-end.

## Prerequisites

1. Install PocketScope with dependencies:
   ```bash
   cd /Users/chrispatten/workspace/pocket-scope
   pip install -e .
   ```

2. Install ps_sensors module (if testing with real GPS hardware):
   ```bash
   cd /Users/chrispatten/workspace/pocket-scope/ps_sensors
   pip install -e .
   ```

## Test 1: GPS Source Adapter Unit Tests

Run the GPS source adapter tests:

```bash
cd /Users/chrispatten/workspace/pocket-scope
python -m pytest tests/ingest/test_ps_sensors_gps.py -v
```

Expected: All tests pass, including:
- `test_gps_source_valid_fix` - GPS publishes valid fixes
- `test_gps_source_invalid_fix_not_published` - Invalid fixes are filtered
- `test_gps_source_stale_fix_not_published` - Stale fixes are filtered
- `test_gps_fix_validation` - Fix validation logic works
- `test_gps_fix_conversion` - ps_sensors to PocketScope conversion works

## Test 2: UI Controller GPS Centering Tests

Run the UI controller GPS tests:

```bash
cd /Users/chrispatten/workspace/pocket-scope
python -m pytest tests/ui/test_gps_centering.py -v
```

Expected: All tests pass, including:
- `test_gps_updates_center_coordinates` - GPS updates center
- `test_gps_fallback_to_cli_center` - Falls back to CLI when stale
- `test_gps_last_known_position_fallback` - Uses last known position
- `test_gps_coordinate_validation` - Validates coordinates
- `test_gps_status_flag_updates` - GPS status flag updates correctly

## Test 3: End-to-End Integration (Desktop with Mock GPS)

Test GPS integration without real hardware using event bus:

```bash
cd /Users/chrispatten/workspace/pocket-scope
python -c "
import asyncio
from datetime import datetime, timezone
from pocketscope.core.events import EventBus, pack
from pocketscope.core.models import GpsFix

async def test():
    bus = EventBus()
    
    # Simulate GPS fix
    fix = GpsFix(
        ts=datetime.now(timezone.utc),
        lat=42.3601,
        lon=-71.0589,
    )
    
    # Subscribe to GPS events
    sub = bus.subscribe('gps.position')
    
    # Publish fix
    fix_dict = fix.model_dump()
    fix_dict['ts'] = fix.ts.isoformat()
    await bus.publish('gps.position', pack(fix_dict))
    
    # Receive event
    env = await sub.__anext__()
    print('GPS event received successfully!')
    print(f'Payload size: {len(env.payload)} bytes')
    
asyncio.run(test())
"
```

Expected output:
```
GPS event received successfully!
Payload size: XXX bytes
```

## Test 4: Live Application with GPS Enabled

Test with the full application (requires ps_sensors and GPS hardware):

```bash
cd /Users/chrispatten/workspace/pocket-scope
python -m pocketscope --enable-gps --playback sample_data/demo_adsb.jsonl
```

Expected behavior:
1. Application starts successfully
2. Console shows: `[live_view] GPS source enabled on /dev/serial0`
3. If GPS hardware available: `GPS reader started on /dev/serial0`
4. If GPS not available: `ps_sensors not available, GPS disabled`
5. Status overlay shows `GPS ok` when fix is valid
6. Status overlay shows `GPS x` when no fix or stale
7. PPI center follows GPS position when available
8. Falls back to CLI center when GPS is unavailable

## Test 5: GPS CLI Flags

Test GPS configuration flags:

```bash
# Custom GPS port
python -m pocketscope --enable-gps --gps-port /dev/ttyUSB0 --playback sample_data/demo_adsb.jsonl

# Custom baud rate
python -m pocketscope --enable-gps --gps-baudrate 115200 --playback sample_data/demo_adsb.jsonl
```

Expected: Application respects custom GPS settings

## Test 6: GPS Fallback Behavior

To test fallback behavior, you can manually modify GPS timestamps in the UI controller:

1. Start application with GPS enabled
2. Observe initial GPS fix (if available)
3. Wait 15 seconds without new GPS data
4. Verify center falls back to last known position
5. Wait 65 seconds total
6. Verify center falls back to CLI argument

## Verification Checklist

- [ ] GPS source adapter tests pass
- [ ] UI controller GPS tests pass
- [ ] Event bus GPS publishing works
- [ ] Application starts with `--enable-gps` flag
- [ ] GPS status indicator shows in status overlay
- [ ] PPI center updates from GPS when available
- [ ] Fallback to CLI center works when GPS unavailable
- [ ] GPS port and baudrate flags work
- [ ] No linter errors in new code
- [ ] All syntax checks pass

## Success Criteria

All tests pass and the application:
1. Successfully integrates ps_sensors GPS data
2. Updates ownship location (PPI center) from GPS
3. Shows GPS status in status overlay
4. Implements proper fallback cascade
5. Handles GPS unavailability gracefully
6. Provides CLI flags for GPS configuration

