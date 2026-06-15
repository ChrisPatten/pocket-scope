# Debugging Service Failure

## Check the Actual Error

The service is crashing immediately. To see the actual error:

```bash
# View the last 100 lines of logs to see the actual Python/application error
sudo journalctl -u pocketscope@pocketscope.service -n 100 --no-pager

# Or follow logs in real-time
sudo journalctl -u pocketscope@pocketscope.service -f
```

## Common Issues and Fixes

### Issue 1: Wrapper Script Not Found

If you see: `Failed to locate executable /usr/local/bin/pocketscope-wrapper.sh`

**Fix:** Install the wrapper script:
```bash
sudo cp ~/pocket-scope/bootstrap_assets/pocketscope-wrapper.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/pocketscope-wrapper.sh
```

### Issue 2: Python Path Not Found

If you see: `Failed to locate executable /home/pocketscope/pocket-scope/.venv/bin/python`

**Fix:** Verify the Python path exists:
```bash
# Check if the venv exists
ls -l /home/pocketscope/pocket-scope/.venv/bin/python

# If it doesn't exist, check where Python actually is
which python3
# Or check if there's a venv elsewhere
find /home/pocketscope -name "python" -type f 2>/dev/null | grep venv
```

### Issue 3: Missing Dependencies

If you see Python import errors, install dependencies:
```bash
cd /home/pocketscope/pocket-scope
/home/pocketscope/pocket-scope/.venv/bin/pip install -e .
```

### Issue 4: Environment Variables Not Set

If you see errors about missing `POCKETSCOPE_URL` or `POCKETSCOPE_CENTER`:

**Fix:** Check `/etc/default/pocketscope`:
```bash
cat /etc/default/pocketscope
```

Make sure it has:
```bash
POCKETSCOPE_URL="https://adsb.chrispatten.dev/data/aircraft.json"
POCKETSCOPE_CENTER="42.00748,-71.20899"
```

## Quick Fix: Use Direct Path (No Wrapper)

If the wrapper script is causing issues, edit the service file to use a direct path:

```bash
sudo nano /etc/systemd/system/pocketscope@.service
```

Change ExecStart to:
```ini
ExecStart=/home/pocketscope/pocket-scope/.venv/bin/python -m pocketscope \
  --url ${POCKETSCOPE_URL} \
  --center ${POCKETSCOPE_CENTER} \
  --tft \
  --enable-gps \
  --gps-port ${POCKETSCOPE_GPS_PORT:-/dev/serial0} \
  --gps-baudrate ${POCKETSCOPE_GPS_BAUDRATE:-9600}
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl restart pocketscope@pocketscope.service
```

## Test Outside systemd First

Before fixing the service, test that PocketScope runs manually:

```bash
# Switch to the service user
sudo su - pocketscope

# Activate venv and test
cd ~/pocket-scope
source .venv/bin/activate
python -m pocketscope --url "https://adsb.chrispatten.dev/data/aircraft.json" --center "42.00748,-71.20899" --tft --enable-gps

# If that works, the service should work too
```

## Check Service Configuration

Verify the service file is correct:

```bash
# View the actual service file
sudo cat /etc/systemd/system/pocketscope@.service

# Check if it matches what you expect
```

## Stop the Restart Loop

If the service keeps restarting, stop it first:

```bash
sudo systemctl stop pocketscope@pocketscope.service
# Fix the issue
# Then start it again
sudo systemctl start pocketscope@pocketscope.service
```

