# Fixing Service Path Issue

## Problem

The service is failing with:
```
Failed to locate executable /root/pocket-scope/.venv/bin/python: No such file or directory
```

This happens because `%h` in systemd templated services doesn't always expand correctly in `ExecStart`.

## Solution: Use Wrapper Script

### Step 1: Install the wrapper script

Copy the wrapper script to a system location:

```bash
sudo cp ~/pocket-scope/bootstrap_assets/pocketscope-wrapper.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/pocketscope-wrapper.sh
```

### Step 2: Update the service file

Copy the updated service file:

```bash
sudo cp ~/pocket-scope/bootstrap_assets/pocketscope@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### Step 3: Restart the service

```bash
sudo systemctl restart pocketscope@pocketscope.service
```

## Alternative: Use Absolute Path

If you prefer not to use a wrapper script, you can edit the service file directly:

```bash
sudo nano /etc/systemd/system/pocketscope@.service
```

Change the ExecStart line to use an absolute path:

```ini
ExecStart=/home/pocketscope/pocket-scope/.venv/bin/python -m pocketscope \
  --url ${POCKETSCOPE_URL} \
  --center ${POCKETSCOPE_CENTER} \
  --tft \
  --enable-gps \
  --gps-port ${POCKETSCOPE_GPS_PORT:-/dev/serial0} \
  --gps-baudrate ${POCKETSCOPE_GPS_BAUDRATE:-9600}
```

Replace `/home/pocketscope` with the actual home directory of the user running the service.

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart pocketscope@pocketscope.service
```

## Verify It's Working

Check the service status:

```bash
sudo systemctl status pocketscope@pocketscope.service
```

Check the logs:

```bash
sudo journalctl -u pocketscope@pocketscope.service -n 50 --no-pager
```

You should see GPS-related messages if GPS is enabled and working.

