import os
import sys
import subprocess
import shutil

# Ensure Playwright finds Chromium across all user contexts and Windows SYSTEM service
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"C:\Users\Administrator\AppData\Local\ms-playwright"

# Setup resilient, non-blocking logging for Windows 24/7 background service
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(logs_dir, exist_ok=True)
service_log_path = os.path.join(logs_dir, "service.log")

class SafeStreamWriter:
    def __init__(self, stream, log_file):
        self.stream = stream
        self.log_file = log_file

    def write(self, text):
        if self.log_file:
            try:
                self.log_file.write(text)
            except Exception:
                pass
        if self.stream:
            try:
                self.stream.write(text)
            except Exception:
                pass

    def flush(self):
        if self.log_file:
            try:
                self.log_file.flush()
            except Exception:
                pass
        if self.stream:
            try:
                self.stream.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def fileno(self):
        if self.stream and hasattr(self.stream, 'fileno'):
            try:
                return self.stream.fileno()
            except Exception:
                pass
        return 1

try:
    log_file_handle = open(service_log_path, "a", encoding="utf-8", buffering=1, errors="replace")
    sys.stdout = SafeStreamWriter(sys.stdout, log_file_handle)
    sys.stderr = SafeStreamWriter(sys.stderr, log_file_handle)
except Exception:
    pass

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

def auto_update_on_startup():
    """
    Checks GitHub repository for updates and pulls them automatically before application start.
    Non-blocking and resilient: fails gracefully if offline or git is unavailable.
    """
    app_dir = os.path.dirname(os.path.abspath(__file__))
    git_dir = os.path.join(app_dir, ".git")

    if not os.path.exists(git_dir):
        return

    # Find git executable
    git_bin = shutil.which("git")
    if not git_bin and sys.platform == "win32":
        for candidate in [
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files\Git\bin\git.exe",
            r"C:\Program Files (x86)\Git\cmd\git.exe"
        ]:
            if os.path.exists(candidate):
                git_bin = candidate
                break

    if not git_bin:
        print("[AUTO-UPDATE] Git binary not found. Skipping auto-update check.")
        return

    print("==================================================")
    print(" [AUTO-UPDATE] Checking GitHub for new version...")
    try:
        # Fetch remote origin with short timeout
        subprocess.run(
            [git_bin, "-C", app_dir, "fetch", "origin", "main"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10
        )

        local_hash = subprocess.check_output(
            [git_bin, "-C", app_dir, "rev-parse", "HEAD"],
            text=True,
            timeout=5
        ).strip()

        remote_hash = subprocess.check_output(
            [git_bin, "-C", app_dir, "rev-parse", "origin/main"],
            text=True,
            timeout=5
        ).strip()

        if local_hash != remote_hash:
            print(f" [AUTO-UPDATE] New version found on GitHub! ({local_hash[:7]} -> {remote_hash[:7]})")
            print(" [AUTO-UPDATE] Pulling updates automatically...")
            pull_res = subprocess.run(
                [git_bin, "-C", app_dir, "pull", "--ff-only", "origin", "main"],
                capture_output=True,
                text=True,
                timeout=15
            )
            if pull_res.returncode == 0:
                print(" [AUTO-UPDATE] Successfully updated to latest GitHub version!")
                # Check if requirements changed
                diff_req = subprocess.run(
                    [git_bin, "-C", app_dir, "diff", local_hash, remote_hash, "--", "requirements.txt"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if diff_req.stdout.strip():
                    print(" [AUTO-UPDATE] Updating Python dependencies...")
                    subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"], timeout=60)
            else:
                print(f" [AUTO-UPDATE] Pull returned code {pull_res.returncode}. Proceeding with current code.")
        else:
            print(f" [AUTO-UPDATE] Application is up to date (commit: {local_hash[:7]}).")
    except Exception as e:
        print(f" [AUTO-UPDATE] Check skipped ({e}). Launching farm with current version.")
    print("==================================================")

if __name__ == "__main__":
    auto_update_on_startup()

    import uvicorn
    port = 80
    host = "0.0.0.0"
    print(f" Starting YAPF_Farm Service directly on http://{host}:{port}")
    print("==================================================")
    uvicorn.run("app.main:app", host=host, port=port, log_level="warning", access_log=False)
