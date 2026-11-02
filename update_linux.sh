#!/usr/bin/env bash
# ==============================================================================
# YAPF_Farm - Linux Auto Update Script
# ==============================================================================

set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

echo "[*] Pulling latest updates from Git..."
git pull origin main

if [ -d ".venv" ]; then
    echo "[*] Updating Python dependencies..."
    .venv/bin/pip install -r requirements.txt
fi

if systemctl is-active --quiet yapf-farm; then
    echo "[*] Restarting yapf-farm systemd service..."
    sudo systemctl restart yapf-farm
fi

echo "[+] YAPF_Farm updated successfully!"
