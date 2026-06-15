# Installing ps_sensors Module

The `ps_sensors` module must be installed in the PocketScope virtual environment for GPS to work.

## Installation Steps

### On Your Device

```bash
# Switch to the service user
sudo su - pocketscope

# Navigate to the ps_sensors directory
cd ~/pocket-scope/ps_sensors

# Install in the PocketScope venv
~/pocket-scope/.venv/bin/pip install -e .

# Verify installation
~/pocket-scope/.venv/bin/python -c "import ps_sensors.gps.uart; print('ps_sensors installed successfully')"
```

### One-Liner (Run as pocketscope user)

```bash
sudo -u pocketscope bash -c "cd ~/pocket-scope/ps_sensors && ~/pocket-scope/.venv/bin/pip install -e ."
```

### Verify Installation

```bash
# Test import
sudo -u pocketscope bash -c "cd ~/pocket-scope && source .venv/bin/activate && python -c 'import ps_sensors.gps.uart; print(\"✓ ps_sensors available\")'"
```

## After Installation

1. **Restart the service:**
   ```bash
   sudo systemctl restart pocketscope@pocketscope.service
   ```

2. **Check logs for GPS messages:**
   ```bash
   sudo journalctl -u pocketscope@pocketscope.service -f | grep -i gps
   ```

3. **Expected output:**
   ```
   [live_view] GPS source enabled on /dev/serial0
   GPS reader started on /dev/serial0
   GPS fix published: lat=42.3601, lon=-71.0589, age=1.2s, fix_type=RMC:A
   ```

## Troubleshooting

### If pip install fails:

```bash
# Check if ps_sensors directory exists
ls -la ~/pocket-scope/ps_sensors

# Check if pyproject.toml exists
ls -la ~/pocket-scope/ps_sensors/pyproject.toml

# Install dependencies first if needed
cd ~/pocket-scope/ps_sensors
~/pocket-scope/.venv/bin/pip install -e . --verbose
```

### If still not working:

```bash
# Check Python path
sudo -u pocketscope bash -c "cd ~/pocket-scope && source .venv/bin/activate && which python && python --version"

# Check if module is in site-packages
sudo -u pocketscope bash -c "cd ~/pocket-scope && source .venv/bin/activate && python -c 'import sys; print(sys.path)'"
```

## Dependencies

ps_sensors may require:
- `pyserial` (for GPS UART)
- Other dependencies listed in `ps_sensors/pyproject.toml`

These should be installed automatically when you run `pip install -e .`

