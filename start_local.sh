#!/usr/bin/env bash
# Run YAPF_Farm locally on macOS or Linux
cd "$(dirname "$BASH_SOURCE")"

if [ ! -d ".venv" ]; then
    echo "[*] Creating virtual environment .venv..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m playwright install chromium
fi

echo "[*] Starting YAPF_Farm..."
.venv/bin/python run.py
