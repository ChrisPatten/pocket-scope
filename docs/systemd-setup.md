# Running PocketScope as a systemd Service

This guide shows how to run the live view app from the Pi’s `.venv` as a self-restarting service.

---

## 1. Create an environment file

Store runtime arguments here so you can tweak them without editing the service unit.

```bash
sudo tee /etc/default/pocketscope-live-view >/dev/null <<'EOF'
POCKETSCOPE_URL="https://adsb.chrispatten.dev/data/aircraft.json"
POCKETSCOPE_CENTER="42.00748,-71.20899"
POCKETSCOPE_HOME="$HOME/.pocketscope"
POCKETSCOPE_RUNWAYS_FILE="$HOME/pocket-scope/src/pocketscope/assets/runways.json"
POCKETSCOPE_RUNWAYS_SQLITE="$POCKETSCOPE_HOME/runways.sqlite"
EOF
````

---

## 2. Create the systemd service unit

```bash
sudo tee /etc/systemd/system/pocketscope.service >/dev/null <<'EOF'
[Unit]
Description=PocketScope live view (TFT)
Wants=network-online.target
After=network-online.target
StartLimitBurst=10
StartLimitIntervalSec=60

[Service]
Type=simple
# Prefer the templated unit `pocketscope@.service` which accepts a username
# instance and avoids hardcoded paths. Example usage is shown below.
EnvironmentFile=-/etc/default/pocketscope-live-view
Environment=PYTHONUNBUFFERED=1
# Uncomment if you need to target the framebuffer directly:
# Environment=SDL_VIDEODRIVER=fbcon
# Environment=SDL_FBDEV=/dev/fb0

ExecStart=%h/pocket-scope/.venv/bin/python -m pocketscope \
  --url ${POCKETSCOPE_URL} \
  --center ${POCKETSCOPE_CENTER} \
  --tft \
  --runways-geojson ${POCKETSCOPE_RUNWAYS_FILE} \
  --runways-sqlite ${POCKETSCOPE_RUNWAYS_SQLITE} \
  --runway-icons

KillSignal=SIGINT
TimeoutStopSec=15
Restart=always
RestartSec=3

# Uncomment if you need additional device access:
# SupplementaryGroups=gpio,spi,video

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

---

## 3. Enable and start the service

```bash
sudo systemctl daemon-reload
# To enable for a specific user (for example `pocketscope`) using the
# templated unit, copy the unit and enable the instance:
#   sudo cp bootstrap_assets/pocketscope@.service /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now pocketscope@pocketscope.service

sudo systemctl daemon-reload
```

---

## 4. Check status and logs

```bash
systemctl status pocketscope.service --no-pager
journalctl -u pocketscope.service -f
```

---

## 5. Updating arguments

To change runtime flags, edit the environment file:

```bash
sudo nano /etc/default/pocketscope-live-view
```

Then restart the service:

```bash
sudo systemctl restart pocketscope.service
```

---

## 6. One-off test run (outside systemd)

Good for debugging before relying on systemd:

```bash
.venv/bin/activate
cd "$HOME/pocket-scope"
. .venv/bin/activate
python -m pocketscope \
  --url "https://adsb.chrispatten.dev/data/aircraft.json" \
  --center "42.00748,-71.20899" 
````

---

## 7. Capturing Screenshots in Service Mode

The live viewer installs a `SIGUSR1` handler that requests a screenshot on the next frame. PNGs are saved under:

```
~/.pocketscope/screenshots/
```

Trigger a capture:

```bash
sudo systemctl kill -s SIGUSR1 pocketscope.service
```

Check logs for the saved path:

```bash
journalctl -u pocketscope.service -n 50 | grep screenshot
```

You can also drop a file named `screenshot` inside `~/.pocketscope/commands/` to trigger a capture (the file is consumed and removed automatically).

