#!/usr/bin/env bash
# bootstrap.sh — Raspberry Pi (Debian Bookworm) bootstrap for PocketScope
set -euo pipefail

REPO_URL="https://github.com/ChrisPatten/pocket-scope.git"
BRANCH="${1:-dev}"
TARGET_DIR="$HOME/pocket-scope"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$TARGET_DIR/.venv"

if [[ -z "$REPO_URL" ]]; then
  echo "ERROR: Please provide the Git repository URL."
  echo "Usage: $0 [BRANCH=dev]"
  exit 1
fi

echo "==> Updating apt and installing system dependencies..."
sudo apt-get update -y
sudo apt-get install -y \
  git \
  $PYTHON_BIN \
  python3-venv \
  python3-pip \
  build-essential \
  libffi-dev \
  libssl-dev \
  libatlas-base-dev \
  libopenblas-dev \
  libopenjp2-7

# Ensure target directory exists
mkdir -p "$TARGET_DIR"

echo "==> Cloning or updating repository..."
if [[ -d "$TARGET_DIR/.git" ]]; then
  echo "Repo already exists at $TARGET_DIR; fetching updates..."
  git -C "$TARGET_DIR" fetch --all --prune
else
  git clone "$REPO_URL" "$TARGET_DIR"
fi

echo "==> Checking out branch: $BRANCH"
git -C "$TARGET_DIR" checkout "$BRANCH"
git -C "$TARGET_DIR" pull --ff-only || true

echo "==> Creating Python virtual environment at: $VENV_DIR"
if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
  $PYTHON_BIN -m venv "$VENV_DIR"
fi

echo "==> Activating venv and upgrading pip/setuptools/wheel..."
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
pip install --upgrade pip setuptools wheel

echo "==> Installing project in editable mode with [pi] extra..."
cd "$TARGET_DIR"
pip install -e ".[pi]"

echo "==> Configuring systemd service..."
SERVICE_FILE="/etc/systemd/system/pocketscope.service"
ENVIRONMENT_FILE="/etc/default/pocketscope"
sudo tee "$ENVIRONMENT_FILE" >/dev/null <<'EOF'
POCKETSCOPE_URL="https://adsb.chrispatten.dev/data/aircraft.json"
POCKETSCOPE_CENTER="42.00748,-71.20899"
POCKETSCOPE_HOME="/home/pocketscope/.pocketscope"
POCKETSCOPE_RUNWAYS_SQLITE="/home/pocketscope/.pocketscope/runways.sqlite"
EOF

sudo tee "$SERVICE_FILE" >/dev/null <<'EOF'
[Unit]
Description=PocketScope live view (TFT)
Wants=network-online.target
After=network-online.target
StartLimitBurst=10
StartLimitIntervalSec=60

[Service]
Type=simple
User=pocketscope
WorkingDirectory=/home/pocketscope/pocket-scope
EnvironmentFile=-/etc/default/pocketscope-live-view
Environment=PYTHONUNBUFFERED=1
# Uncomment if you need to target the framebuffer directly:
# Environment=SDL_VIDEODRIVER=fbcon
# Environment=SDL_FBDEV=/dev/fb0

ExecStart=/home/pocketscope/pocket-scope/.venv/bin/python -m pocketscope \
  --url ${POCKETSCOPE_URL} \
  --center ${POCKETSCOPE_CENTER} \
  --tft \

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

echo "==> Ensuring SPI and GPIO are enabled..."
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_gpio 0

echo "==> Reloading systemd and enabling service..."
sudo systemctl daemon-reload
sudo systemctl enable pocketscope.service

echo "==> Restarting to apply all changes..."
shutdown -r now
