#!/usr/bin/env bash
# ==============================================================================
# YAPF_Farm - Automated Linux (Ubuntu/Debian) Deployment Script
# ==============================================================================
# Usage:
#   chmod +x deploy_linux.sh
#   sudo ./deploy_linux.sh
# ==============================================================================

set -e

echo "=================================================="
echo "  YAPF_Farm - Linux Auto Deployment"
echo "=================================================="

if [ "$EUID" -ne 0 ]; then
  echo "[!] Please run as root (sudo ./deploy_linux.sh)"
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[*] Application directory: $APP_DIR"

echo "[*] Updating system packages & installing prerequisites..."
apt-get update -y
apt-get install -y python3 python3-pip python3-venv git curl ufw

echo "[*] Creating Python virtual environment..."
cd "$APP_DIR"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

if [ ! -d "$APP_DIR/.git" ]; then
    echo "[*] Linking directory to GitHub repository..."
    git -C "$APP_DIR" init -b main || true
    git -C "$APP_DIR" remote add origin https://github.com/tonypulseone-dot/yapf_farm.git || true
    git -C "$APP_DIR" fetch origin || true
    git -C "$APP_DIR" reset origin/main || true
    git -C "$APP_DIR" branch --set-upstream-to=origin/main main || true
fi

echo "[*] Installing dependencies..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "[*] Installing Playwright Chromium and system dependencies..."
.venv/bin/python -m playwright install --with-deps chromium

echo "[*] Creating systemd service (/etc/systemd/system/yapf-farm.service)..."
cat << EOF > /etc/systemd/system/yapf-farm.service
[Unit]
Description=YAPF_Farm Autonomous SEO Automation Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/run.py
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

echo "[*] Reloading and starting systemd service..."
systemctl daemon-reload
systemctl enable yapf-farm
systemctl restart yapf-farm

echo "[*] Configuring UFW firewall rules..."
ufw allow 80/tcp || true
ufw allow 8080/tcp || true

IP=$(hostname -I | awk '{print $1}')
echo "=================================================="
echo "  YAPF_Farm Successfully Deployed!"
echo "  Dashboard URL: http://$IP:8080 or http://$IP/"
echo "  Login: admin / pre12345"
echo "=================================================="
