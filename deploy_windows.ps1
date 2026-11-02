# ==============================================================================
# YAPF_Farm - Automated Windows Server Deployment Script
# ==============================================================================
# Run this script in an elevated PowerShell (Run as Administrator):
# powershell -ExecutionPolicy Bypass -File deploy_windows.ps1
# ==============================================================================

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  YAPF_Farm - Windows Server Auto Deployment" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $AppDir) { $AppDir = (Get-Location).Path }
Write-Host "[*] Application directory: $AppDir" -ForegroundColor Yellow

# 1. Check Python
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    if (Test-Path "C:\Program Files\Python311\python.exe") {
        $PythonExe = "C:\Program Files\Python311\python.exe"
    } elseif (Test-Path "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe") {
        $PythonExe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    }
}

if (-not $PythonExe) {
    Write-Host "[!] Python not found. Downloading Python 3.11.9 installer..." -ForegroundColor Yellow
    $installerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    $installerPath = "$env:TEMP\python-3.11.9-amd64.exe"
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath
    Write-Host "[*] Installing Python 3.11 silently..." -ForegroundColor Yellow
    Start-Process -FilePath $installerPath -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 TargetDir=""C:\Program Files\Python311""" -Wait
    $PythonExe = "C:\Program Files\Python311\python.exe"
}

Write-Host "[+] Using Python: $PythonExe" -ForegroundColor Green

# 2. Check Git for Auto-Updates
$GitExe = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $GitExe -and (Test-Path "C:\Program Files\Git\cmd\git.exe")) {
    $GitExe = "C:\Program Files\Git\cmd\git.exe"
}
if (-not $GitExe) {
    Write-Host "[!] Git not found. Downloading Git for Windows..." -ForegroundColor Yellow
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        (New-Object Net.WebClient).DownloadFile("https://github.com/git-for-windows/git/releases/download/v2.44.0.windows.1/Git-2.44.0-64-bit.exe", "$env:TEMP\git_setup.exe")
        Start-Process "$env:TEMP\git_setup.exe" -ArgumentList "/VERYSILENT /NORESTART /SP- /CLOSEAPPLICATIONS" -Wait
        Remove-Item "$env:TEMP\git_setup.exe" -Force -ErrorAction SilentlyContinue
        $GitExe = "C:\Program Files\Git\cmd\git.exe"
        Write-Host "[+] Git installed successfully." -ForegroundColor Green
    } catch {
        Write-Host "[!] Could not install Git automatically: $_" -ForegroundColor DarkYellow
    }
} else {
    Write-Host "[+] Using Git: $GitExe" -ForegroundColor Green
}

# 3. Connect directory to GitHub if .git missing
if ($GitExe -and -not (Test-Path "$AppDir\.git")) {
    Write-Host "[*] Linking directory to GitHub repository..." -ForegroundColor Yellow
    & $GitExe -C "$AppDir" init -b main | Out-Null
    & $GitExe -C "$AppDir" remote add origin https://github.com/tonypulseone-dot/yapf_farm.git | Out-Null
    & $GitExe -C "$AppDir" fetch origin | Out-Null
    & $GitExe -C "$AppDir" reset origin/main | Out-Null
    & $GitExe -C "$AppDir" branch --set-upstream-to=origin/main main | Out-Null
    Write-Host "[+] Repository linked to origin/main." -ForegroundColor Green
}

# 4. Install dependencies
Write-Host "[*] Installing Python dependencies..." -ForegroundColor Yellow
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r "$AppDir\requirements.txt"

# 5. Install Playwright Chromium & set shared path
Write-Host "[*] Installing Playwright Chromium browser..." -ForegroundColor Yellow
$SharedBrowsers = "C:\ProgramData\ms-playwright"
if (-not (Test-Path $SharedBrowsers)) {
    New-Item -ItemType Directory -Path $SharedBrowsers -Force | Out-Null
}
[Environment]::SetEnvironmentVariable("PLAYWRIGHT_BROWSERS_PATH", $SharedBrowsers, "Machine")
$env:PLAYWRIGHT_BROWSERS_PATH = $SharedBrowsers

& $PythonExe -m playwright install chromium

# Also create junction for Windows SYSTEM profile so service tasks find it
$SysProfilePlaywright = "C:\Windows\system32\config\systemprofile\AppData\Local\ms-playwright"
if (-not (Test-Path $SysProfilePlaywright)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $SysProfilePlaywright) -Force | Out-Null
    cmd /c "mklink /J ""$SysProfilePlaywright"" ""$SharedBrowsers""" | Out-Null
}

# 6. Configure Windows Firewall for port 80 and 8080
Write-Host "[*] Configuring Windows Firewall..." -ForegroundColor Yellow
netsh advfirewall firewall add rule name="YAPF_Farm_80" dir=in action=allow protocol=TCP localport=80 | Out-Null
netsh advfirewall firewall add rule name="YAPF_Farm_8080" dir=in action=allow protocol=TCP localport=8080 | Out-Null

# 7. Create or update Windows Scheduled Task for 24/7 background operation
Write-Host "[*] Registering 24/7 autonomous Windows Task (YAPF_Farm)..." -ForegroundColor Yellow
$TaskName = "YAPF_Farm"
schtasks /delete /tn $TaskName /f 2>$null | Out-Null
schtasks /create /tn $TaskName /tr "`"$PythonExe`" `"$AppDir\run.py`"" /sc onstart /ru "SYSTEM" /rl HIGHEST /f | Out-Null

# 8. Start the service (run.py will automatically auto-update on launch!)
Write-Host "[*] Starting YAPF_Farm service..." -ForegroundColor Yellow
schtasks /run /tn $TaskName | Out-Null

Start-Sleep -Seconds 3
Write-Host "==================================================" -ForegroundColor Green
Write-Host "  YAPF_Farm Successfully Deployed!" -ForegroundColor Green
Write-Host "  Auto-updates: Enabled (checks GitHub before launch)" -ForegroundColor Green
Write-Host "  Web Dashboard: http://localhost:8080 or http://localhost/" -ForegroundColor Green
Write-Host "  Login: admin / pre12345" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
