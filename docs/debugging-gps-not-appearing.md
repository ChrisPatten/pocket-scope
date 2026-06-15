# Debugging GPS Not Appearing in Logs

## Check Full Logs (Not Just GPS)

The GPS messages might be there but filtered out. Check the full logs:

```bash
# View recent logs without filtering
sudo journalctl -u pocketscope@pocketscope.service -n 100 --no-pager

# Look for these specific messages:
# - "[live_view] GPS source enabled on /dev/serial0"
# - "GPS reader started on /dev/serial0"
# - "ps_sensors not available, GPS disabled"
# - "Failed to initialize GPS source"
```

## Check if GPS is Actually Enabled

Verify the service is running with `--enable-gps`:

```bash
# Check the actual command being run
sudo systemctl show pocketscope@pocketscope.service -p ExecStart --value

# Or check the service file
sudo cat /etc/systemd/system/pocketscope@.service | grep -A 5 ExecStart
```

You should see `--enable-gps` in the command.

## Check if ps_sensors is Available

GPS requires the ps_sensors module. Check if it's installed:

```bash
# As the service user
sudo su - pocketscope
cd ~/pocket-scope
source .venv/bin/activate

# Try importing ps_sensors
python -c "import ps_sensors.gps.uart; print('ps_sensors available')"
```

If you get an ImportError, install ps_sensors:

```bash
cd ~/pocket-scope/ps_sensors
pip install -e .
```

## Check GPS Hardware

Verify the GPS device exists and is accessible:

```bash
# Check if GPS device exists
ls -l /dev/serial0 /dev/ttyAMA0 /dev/ttyUSB0 2>/dev/null

# Check permissions
ls -l /dev/serial0

# Test GPS directly (should see NMEA sentences)
sudo cat /dev/serial0 | head -5
```

## Check Application Startup Messages

Look for the initial startup messages:

```bash
# View logs from service start
sudo journalctl -u pocketscope@pocketscope.service --since "10 minutes ago" | grep -E "(GPS|gps|live_view|source enabled)"

# Or view all recent logs
sudo journalctl -u pocketscope@pocketscope.service -n 200 --no-pager | tail -50
```

## Expected Log Messages

**If GPS is working, you should see:**
```
[live_view] GPS source enabled on /dev/serial0
GPS reader started on /dev/serial0
GPS polling loop started
GPS fix published: lat=42.3601, lon=-71.0589, age=1.2s, fix_type=RMC:A
GPS status: 30 fixes published, 0 invalid/stale fixes filtered (last 30s)
```

**If ps_sensors is not available:**
```
[live_view] ps_sensors not available, GPS disabled: No module named 'ps_sensors'
```

**If GPS port can't be opened:**
```
Cannot open GPS port /dev/serial0: [Errno 2] No such file or directory: '/dev/serial0'
```

**If GPS is not enabled:**
```
(No GPS-related messages at all)
```

## Manual Test

Test GPS outside of systemd to see what happens:

```bash
# Stop the service
sudo systemctl stop pocketscope@pocketscope.service

# Run manually as the service user
sudo su - pocketscope
cd ~/pocket-scope
source .venv/bin/activate

# Run with GPS enabled and verbose output
python -m pocketscope \
  --url "https://adsb.chrispatten.dev/data/aircraft.json" \
  --center "42.00748,-71.20899" \
  --tft \
  --enable-gps \
  --gps-port /dev/serial0 \
  --gps-baudrate 9600
```

Watch for GPS messages in the console output.

## Check Status Overlay

Even if logs don't show GPS, check the display:
- Look for "GPS ok" or "GPS x" in the status overlay
- If you see "GPS x", GPS is enabled but not getting fixes
- If you don't see GPS status at all, GPS might not be enabled

## Common Issues

1. **ps_sensors not installed**: Install it in the venv
2. **GPS device doesn't exist**: Check `/dev/serial0` or configure different port
3. **GPS not enabled**: Verify `--enable-gps` is in ExecStart
4. **Silent failures**: Check full logs, not just grep for "gps"
5. **Wrong user permissions**: Service user might not have access to GPS device

## Quick Diagnostic Script

Run this to check everything:

```bash
#!/bin/bash
echo "=== GPS Diagnostic ==="
echo
echo "1. Service status:"
sudo systemctl is-active pocketscope@pocketscope.service
echo
echo "2. GPS flag in service:"
sudo grep -i "enable-gps" /etc/systemd/system/pocketscope@.service
echo
echo "3. GPS device:"
ls -l /dev/serial0 2>/dev/null || echo "  /dev/serial0 not found"
echo
echo "4. ps_sensors module:"
sudo -u pocketscope bash -c "cd ~/pocket-scope && source .venv/bin/activate && python -c 'import ps_sensors.gps.uart; print(\"  ps_sensors available\")'" 2>&1
echo
echo "5. Recent GPS logs:"
sudo journalctl -u pocketscope@pocketscope.service --since "5 minutes ago" | grep -i gps | tail -10
echo
echo "=== End Diagnostic ==="
```

