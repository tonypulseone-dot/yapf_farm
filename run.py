import os
import sys

# Ensure Playwright finds Chromium across all user contexts and Windows SYSTEM service
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"C:\Users\Administrator\AppData\Local\ms-playwright"

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import uvicorn

if __name__ == "__main__":
    port = 80
    host = "0.0.0.0"
    print("==================================================")
    print(f" Starting YAPF_Farm Service directly on http://{host}:{port}")
    print("==================================================")
    uvicorn.run("app.main:app", host=host, port=port, log_level="warning", access_log=False)
