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

echo "==> Creating configuration files..."
CONFIG_DIR="$HOME/.pocketscope"
mkdir -p "$CONFIG_DIR"
cp "$TARGET_DIR/bootstrap_assets/settings.yml" "$CONFIG_DIR/settings.yml"

echo "==> Building basemap database..."
python -m pocketscope.data.ingest_geojson_to_sqlite \
  --airports "$TARGET_DIR/src/pocketscope/assets/airports.json" \
  --runways "$TARGET_DIR/src/pocketscope/assets/runways.json" \
  --states "$TARGET_DIR/src/pocketscope/assets/us_states.json" \
  --out "$CONFIG_DIR/pocketscope.db" --replace

echo "==> Configuring systemd service..."
SERVICE_FILE="/etc/systemd/system/pocketscope@.service"
ENVIRONMENT_FILE="/etc/default/pocketscope"
# Install environment defaults (will expand $HOME for the user when sourced)
sudo cp "$TARGET_DIR/bootstrap_assets/pocketscope.env" "$ENVIRONMENT_FILE"
# Install the templated systemd unit so it can be instantiated per-user
sudo cp "$TARGET_DIR/bootstrap_assets/pocketscope@.service" "$SERVICE_FILE"

echo "==> Ensuring SPI and GPIO are enabled..."
sudo raspi-config nonint do_spi 0

echo "==> Reloading systemd and enabling service..."
sudo systemctl daemon-reload
# Enable the service instance for the current user (useful on single-user Pi)
sudo systemctl enable --now pocketscope@${USER}.service || true

echo "==> Restarting to apply all changes..."
shutdown -r now
