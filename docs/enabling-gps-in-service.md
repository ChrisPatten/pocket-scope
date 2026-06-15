# Enabling GPS in PocketScope Service

## Quick Fix

The service file needs to be updated to include the `--enable-gps` flag. Here's how:

### Option 1: Update the Service File (Recommended)

Edit the systemd service file:

```bash
sudo nano /etc/systemd/system/pocketscope@.service
```

Find the `ExecStart` line and add GPS flags:

```ini
ExecStart=%h/pocket-scope/.venv/bin/python -m pocketscope \
  --url ${POCKETSCOPE_URL} \
  --center ${POCKETSCOPE_CENTER} \
  --tft \
  --enable-gps \
  --gps-port ${POCKETSCOPE_GPS_PORT:-/dev/serial0} \
  --gps-baudrate ${POCKETSCOPE_GPS_BAUDRATE:-9600}
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart pocketscope@pocketscope.service
```

### Option 2: Use Environment Variables

Edit `/etc/default/pocketscope`:

```bash
sudo nano /etc/default/pocketscope
```

Add GPS configuration (optional - defaults will be used if not set):

```bash
POCKETSCOPE_GPS_PORT="/dev/serial0"
POCKETSCOPE_GPS_BAUDRATE="9600"
```

Then update the service file to use these variables (as shown in Option 1) and restart.

## Verify GPS is Enabled

After restarting, check the logs:

```bash
# Check if GPS is being initialized
sudo journalctl -u pocketscope@pocketscope.service -n 50 | grep -i gps

# Look for these messages:
# ✓ "[live_view] GPS source enabled on /dev/serial0"
# ✓ "GPS reader started on /dev/serial0"
# ✓ "GPS fix published: lat=..., lon=..."

# Or check full logs (not just GPS)
sudo journalctl -u pocketscope@pocketscope.service -f
```

## Troubleshooting

If you see errors:

1. **"ps_sensors module not available"**
   ```bash
   # Install ps_sensors module
   cd ~/pocket-scope/ps_sensors
   ~/pocket-scope/.venv/bin/pip install -e .
   ```

2. **"Cannot open GPS port /dev/serial0"**
   ```bash
   # Check if device exists
   ls -l /dev/serial0
   
   # Check permissions (may need to add user to dialout group)
   sudo usermod -a -G dialout $USER
   # Then logout/login or restart service
   ```

3. **GPS not showing fixes**
   ```bash
   # Check GPS hardware directly
   sudo cat /dev/serial0 | head -5
   # Should see NMEA sentences like: $GPRMC,...
   ```

4. **Service keeps restarting**
   ```bash
   # Check for errors
   sudo journalctl -u pocketscope@pocketscope.service -n 100 --no-pager
   ```

## Status Overlay

Once GPS is enabled and working, you should see:
- **`GPS ok`** in the status overlay when GPS is providing valid fixes
- **`GPS x`** when GPS is unavailable or stale

The PPI center (ownship marker) will automatically follow your GPS position when valid fixes are available.

