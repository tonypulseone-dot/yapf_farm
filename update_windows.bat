@echo off
chcp 65001 >nul
echo ==================================================
echo   YAPF_Farm - Windows Auto Update
echo ==================================================

cd /d "%~dp0"

echo [*] Pulling latest updates from Git...
git pull origin main

echo [*] Updating Python dependencies...
python -m pip install -r requirements.txt

echo [*] Restarting YAPF_Farm service...
schtasks /end /tn YAPF_Farm >nul 2>&1
timeout /t 2 /nobreak >nul
schtasks /run /tn YAPF_Farm >nul 2>&1

echo [+] Update completed successfully!
pause
