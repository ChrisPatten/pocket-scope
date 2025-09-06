# Running PocketScope as a systemd Service

This guide shows how to run the live view app from the Pi’s `.venv` as a self-restarting service.

---

## 1. Create an environment file

Store runtime arguments here so you can tweak them without editing the service unit.

```bash
sudo tee /etc/default/pocketscope-live-view >/dev/null <<'EOF'
POCKETSCOPE_URL="https://adsb.chrispatten.dev/data/aircraft.json"
POCKETSCOPE_CENTER="42.00748,-71.20899"
POCKETSCOPE_SECTORS="./sample_data/us_states.json"
POCKETSCOPE_FPS="15"
POCKETSCOPE_TFT="1"
POCKETSCOPE_HOME="/home/pocketscope/.pocketscope"
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

[Service]
Type=simple
User=pocketscope
WorkingDirectory=/home/pocketscope/pocket-scope
EnvironmentFile=-/etc/default/pocketscope-live-view
Environment=PYTHONUNBUFFERED=1
# Uncomment if you need to target the framebuffer directly:
# Environment=SDL_VIDEODRIVER=fbcon
# Environment=SDL_FBDEV=/dev/fb0

ExecStart=/home/pocketscope/pocket-scope/.venv/bin/python -m pocketscope.examples.live_view \
  --url ${POCKETSCOPE_URL} \
  --center ${POCKETSCOPE_CENTER} \
  --sectors ${POCKETSCOPE_SECTORS} \
  --tft \
  --fps ${POCKETSCOPE_FPS} \

KillSignal=SIGINT
TimeoutStopSec=15
Restart=always
RestartSec=3
StartLimitBurst=10
StartLimitIntervalSec=60

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
sudo systemctl enable pocketscope.service
sudo systemctl start pocketscope.service
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
cd /home/pocketscope/pocket-scope
. .venv/bin/activate
python -m pocketscope.examples.live_view \
  --url "https://adsb.chrispatten.dev/data/aircraft.json" \
  --center "42.00748,-71.20899" \
  --sectors "./sample_data/us_states.json" \
  --tft --fps 15 --range 30
```

