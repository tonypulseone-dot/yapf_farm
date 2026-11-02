@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [*] Starting YAPF_Farm locally...
python run.py
pause
