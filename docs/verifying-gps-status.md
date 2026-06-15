# Verifying GPS Status When Service is Running

This guide provides multiple methods to verify that GPS is working correctly when PocketScope is running as a service.

## Method 1: Check Status Overlay on Display

**Visual Indicator (Easiest)**

When PocketScope is running, look at the status overlay at the top of the display:

- **`GPS ok`** = GPS is working and providing valid fixes
- **`GPS x`** = GPS is not available, stale, or not initialized

The status overlay updates in real-time, so you'll see it change from `GPS x` to `GPS ok` when a GPS fix is acquired.

## Method 2: Check Service Logs

**Using journalctl (systemd service)**

If PocketScope is running as a systemd service:

```bash
# View recent GPS-related logs
sudo journalctl -u pocketscope@*.service -f | grep -i gps

# View all recent logs with GPS context
sudo journalctl -u pocketscope@*.service --since "5 minutes ago" | grep -i gps

# View structured JSON logs (if JSON logging enabled)
sudo journalctl -u pocketscope@*.service -o json | jq 'select(.logger | contains("gps"))'
```

**Expected log messages:**

✅ **GPS Working:**
```
{"level":"INFO","logger":"pocketscope.ingest.gps.ps_sensors_source","msg":"GPS reader started on /dev/serial0"}
{"level":"DEBUG","logger":"pocketscope.ingest.gps.ps_sensors_source","msg":"Published GPS fix: lat=42.3601, lon=-71.0589, age=1.2s"}
```

❌ **GPS Not Working:**
```
{"level":"WARNING","logger":"pocketscope.ingest.gps.ps_sensors_source","msg":"Cannot open GPS port /dev/serial0: ..."}
{"level":"WARNING","logger":"pocketscope.ingest.gps.ps_sensors_source","msg":"ps_sensors module not available: ..."}
```

**Using log files (if file logging enabled)**

Check the log file location (typically `~/.pocketscope/logs/` or configured in settings):

```bash
# Tail GPS logs
tail -f ~/.pocketscope/logs/pocketscope.log | grep -i gps

# Search for GPS messages
grep -i gps ~/.pocketscope/logs/pocketscope.log | tail -20
```

## Method 3: Check GPS Hardware Directly

**Verify GPS device exists and is accessible:**

```bash
# Check if GPS device exists
ls -l /dev/serial0 /dev/ttyAMA0 /dev/ttyUSB0 2>/dev/null

# Check GPS device permissions
ls -l /dev/serial0

# Test GPS device with cat (should see NMEA sentences)
sudo cat /dev/serial0 | head -5
```

**Expected output (NMEA sentences):**
```
$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A
$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47
```

## Method 4: Monitor GPS Events via Event Bus

**Create a simple monitoring script:**

Create `~/check_gps_events.py`:

```python
#!/usr/bin/env python3
"""Monitor GPS events from PocketScope event bus."""

import asyncio
import sys
from datetime import datetime

# Add PocketScope to path
sys.path.insert(0, '/path/to/pocket-scope/src')

from pocketscope.core.events import EventBus, unpack
from pocketscope.core.models import GpsFix

async def monitor_gps():
    bus = EventBus()
    sub = bus.subscribe("gps.position")
    
    if sub is None:
        print("ERROR: Could not subscribe to gps.position topic")
        return
    
    print("Monitoring GPS events... (Press Ctrl+C to stop)")
    print("-" * 60)
    
    try:
        async for env in sub:
            try:
                data = unpack(env.payload)
                fix = GpsFix.model_validate(data)
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] GPS Fix:")
                print(f"  Lat: {fix.lat:.6f}")
                print(f"  Lon: {fix.lon:.6f}")
                print(f"  Time: {fix.ts.isoformat()}")
                print("-" * 60)
            except Exception as e:
                print(f"ERROR parsing GPS event: {e}")
    except KeyboardInterrupt:
        print("\nStopped monitoring")

if __name__ == "__main__":
    asyncio.run(monitor_gps())
```

Run it:
```bash
python3 ~/check_gps_events.py
```

**Note:** This requires PocketScope to be running and publishing GPS events. You may need to adjust the import path.

## Method 5: Check GPS Process Status

**Verify GPS task is running:**

```bash
# Check if GPS-related processes are running
ps aux | grep -i gps

# Check PocketScope process and its threads
ps -T -p $(pgrep -f pocketscope) | grep -i gps
```

## Method 6: Enable Debug Logging

**Temporarily increase GPS logging verbosity:**

Edit your PocketScope settings or set environment variable:

```bash
# Set debug logging for GPS module
export POCKETSCOPE_LOGGING_LEVEL=DEBUG
export POCKETSCOPE_LOGGING_LOGGERS='{"pocketscope.ingest.gps": "DEBUG"}'

# Restart service
sudo systemctl restart pocketscope@*.service

# Watch debug logs
sudo journalctl -u pocketscope@*.service -f | grep -i gps
```

**Expected debug output:**
```
{"level":"DEBUG","logger":"pocketscope.ingest.gps.ps_sensors_source","msg":"GPS polling loop started"}
{"level":"DEBUG","logger":"pocketscope.ingest.gps.ps_sensors_source","msg":"Published GPS fix: lat=42.3601, lon=-71.0589, age=1.2s"}
```

## Method 7: Check GPS Fix Quality

**Using ps_sensors CLI tool (if available):**

```bash
# If ps_sensors CLI is installed
ps_sensors gps latest

# Or use the Python module directly
python3 -c "
from ps_sensors.gps.uart import GpsUartReader
reader = GpsUartReader(port='/dev/serial0')
reader.start()
fix = reader.latest()
print(f'Fix Type: {fix.fix_type}')
print(f'Lat: {fix.lat_deg}')
print(f'Lon: {fix.lon_deg}')
print(f'Age: {fix.age_s:.1f}s')
reader.stop()
"
```

## Method 8: Verify PPI Center Updates

**Visual verification:**

1. Note the current center coordinates (shown in status overlay if LOC element enabled)
2. Move the device to a new location
3. Wait 10-30 seconds for GPS to acquire new fix
4. Verify the PPI center (ownship marker) moves to reflect new position
5. Range rings should re-center around new location

## Troubleshooting Checklist

If GPS shows `GPS x`:

- [ ] Is `--enable-gps` flag set in service configuration?
- [ ] Is ps_sensors module installed? (`pip list | grep ps-sensors`)
- [ ] Does GPS device exist? (`ls -l /dev/serial0`)
- [ ] Are permissions correct? (`sudo chmod 666 /dev/serial0` if needed)
- [ ] Is GPS hardware powered and receiving satellite signals?
- [ ] Check logs for specific error messages
- [ ] Verify GPS port matches hardware (`--gps-port` flag)
- [ ] Check GPS baudrate matches device (`--gps-baudrate` flag)

## Quick Status Check Script

Create `~/check_pocketscope_gps.sh`:

```bash
#!/bin/bash
# Quick GPS status check for PocketScope service

echo "=== PocketScope GPS Status Check ==="
echo

# Check service status
echo "1. Service Status:"
systemctl is-active pocketscope@*.service 2>/dev/null && echo "  ✓ Service is running" || echo "  ✗ Service not running"
echo

# Check GPS device
echo "2. GPS Device:"
if [ -e /dev/serial0 ]; then
    echo "  ✓ /dev/serial0 exists"
    ls -l /dev/serial0
else
    echo "  ✗ /dev/serial0 not found"
fi
echo

# Check recent GPS logs
echo "3. Recent GPS Logs:"
journalctl -u pocketscope@*.service --since "2 minutes ago" --no-pager | grep -i gps | tail -5
if [ $? -ne 0 ]; then
    echo "  (No GPS logs found)"
fi
echo

# Check ps_sensors module
echo "4. ps_sensors Module:"
python3 -c "import ps_sensors; print('  ✓ ps_sensors installed')" 2>/dev/null || echo "  ✗ ps_sensors not installed"
echo

echo "=== Check Complete ==="
```

Make it executable and run:
```bash
chmod +x ~/check_pocketscope_gps.sh
~/check_pocketscope_gps.sh
```

## Expected Behavior Summary

**When GPS is working correctly:**
- Status overlay shows `GPS ok`
- Logs show "GPS reader started" and periodic "Published GPS fix" messages
- PPI center updates when device moves
- No error messages in logs

**When GPS is not working:**
- Status overlay shows `GPS x`
- Logs show warnings about port errors or missing module
- PPI center remains at CLI `--center` coordinates
- Error messages indicate specific issue (port, permissions, module, etc.)

