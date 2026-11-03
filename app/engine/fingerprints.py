import random
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("fingerprints")

# Cached detected Chrome version
_DETECTED_CHROME_VERSION = None

def get_installed_chrome_version() -> str:
    """
    Detects the real version of Chromium installed in Playwright.
    Falls back to modern high-trust version (126.0.6478.127) if detection fails.
    """
    global _DETECTED_CHROME_VERSION
    if _DETECTED_CHROME_VERSION:
        return _DETECTED_CHROME_VERSION
    
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            v = b.version
            b.close()
            if v:
                _DETECTED_CHROME_VERSION = v
                return v
    except Exception:
        pass
    
    _DETECTED_CHROME_VERSION = "126.0.6478.182"
    return _DETECTED_CHROME_VERSION

ANDROID_VERSIONS = [10, 11, 12, 13, 14, 15, 16]

ANDROID_DEVICES = [
    # Samsung
    {"brand": "samsung", "model": "SM-S928B", "name": "Galaxy S24 Ultra", "screen": {"width": 412, "height": 915}},
    {"brand": "samsung", "model": "SM-S918B", "name": "Galaxy S23 Ultra", "screen": {"width": 412, "height": 915}},
    {"brand": "samsung", "model": "SM-S911B", "name": "Galaxy S23", "screen": {"width": 393, "height": 851}},
    {"brand": "samsung", "model": "SM-A546B", "name": "Galaxy A54 5G", "screen": {"width": 412, "height": 915}},
    {"brand": "samsung", "model": "SM-A556B", "name": "Galaxy A55 5G", "screen": {"width": 412, "height": 915}},
    {"brand": "samsung", "model": "SM-G998B", "name": "Galaxy S21 Ultra", "screen": {"width": 412, "height": 915}},
    
    # Xiaomi / Redmi / POCO
    {"brand": "Xiaomi", "model": "2312DRA50G", "name": "Redmi Note 13 Pro 5G", "screen": {"width": 393, "height": 873}},
    {"brand": "Xiaomi", "model": "22101316G", "name": "Redmi Note 12 Pro", "screen": {"width": 393, "height": 873}},
    {"brand": "Xiaomi", "model": "23049PCD8G", "name": "POCO F5", "screen": {"width": 393, "height": 873}},
    {"brand": "Xiaomi", "model": "2201116PG", "name": "POCO X4 Pro 5G", "screen": {"width": 393, "height": 873}},
    {"brand": "Xiaomi", "model": "2201117PG", "name": "POCO M4 Pro", "screen": {"width": 393, "height": 873}},

    # Google Pixel
    {"brand": "Google", "model": "Pixel 8", "name": "Pixel 8", "screen": {"width": 412, "height": 915}},
    {"brand": "Google", "model": "Pixel 7 Pro", "name": "Pixel 7 Pro", "screen": {"width": 412, "height": 892}},
    {"brand": "Google", "model": "Pixel 7a", "name": "Pixel 7a", "screen": {"width": 412, "height": 915}},
    
    # Realme / Tecno
    {"brand": "realme", "model": "RMX3771", "name": "realme 11 Pro+", "screen": {"width": 393, "height": 873}},
    {"brand": "TECNO", "model": "TECNO CK7n", "name": "TECNO CAMON 20 Premier", "screen": {"width": 393, "height": 873}}
]

DESKTOP_RESOLUTIONS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1600, "height": 900},
    {"width": 1366, "height": 768}
]

WEBGL_VENDORS_DESKTOP = [
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (Intel)", "renderer": "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (AMD)", "renderer": "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)"}
]

WEBGL_VENDORS_MOBILE = [
    {"vendor": "ARM", "renderer": "Mali-G710 MC10"},
    {"vendor": "ARM", "renderer": "Mali-G77 MC9"},
    {"vendor": "ARM", "renderer": "Mali-G68 MC4"},
    {"vendor": "Qualcomm", "renderer": "Adreno (TM) 740"},
    {"vendor": "Qualcomm", "renderer": "Adreno (TM) 730"},
    {"vendor": "Qualcomm", "renderer": "Adreno (TM) 642L"},
    {"vendor": "Qualcomm", "renderer": "Adreno (TM) 619"}
]

def generate_fingerprint(device_type: str = "random") -> Dict[str, Any]:
    """
    Generate authentic, high-reputation browser fingerprint dictionary.
    device_type: 'android', 'desktop', or 'random' (80% Android / 20% Desktop like Alpine).
    """
    if device_type == "random" or not device_type:
        device_type = "android" if random.random() < 0.80 else "desktop"

    chrome_ver = get_installed_chrome_version()
    major_ver = chrome_ver.split('.')[0] if '.' in chrome_ver else "126"

    if device_type == "android":
        android_ver = random.choice(ANDROID_VERSIONS)
        device = random.choice(ANDROID_DEVICES)
        res = device["screen"]
        ua = f"Mozilla/5.0 (Linux; Android {android_ver}; {device['model']}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Mobile Safari/537.36"
        platform = "Linux armv8l"
        is_touch = True
        webgl = random.choice(WEBGL_VENDORS_MOBILE)
        hw_cores = random.choice([6, 8])
        dev_mem = random.choice([6, 8, 12])
    else:
        res = random.choice(DESKTOP_RESOLUTIONS)
        win_sub = random.choice(["Win64; x64", "WOW64"])
        ua = f"Mozilla/5.0 (Windows NT 10.0; {win_sub}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36"
        platform = "Win32"
        is_touch = False
        webgl = random.choice(WEBGL_VENDORS_DESKTOP)
        hw_cores = random.choice([4, 6, 8, 12, 16])
        dev_mem = random.choice([8, 16, 32])

    fp = {
        "user_agent": ua,
        "device_type": device_type,
        "viewport": res,
        "screen": {
            "width": res["width"],
            "height": res["height"],
            "color_depth": 24
        },
        "platform": platform,
        "languages": ["ru-RU", "ru", "en-US", "en"],
        "hardware_concurrency": hw_cores,
        "device_memory": dev_mem,
        "webgl_vendor": webgl["vendor"],
        "webgl_renderer": webgl["renderer"],
        "has_touch": is_touch,
        "chrome_major": major_ver,
        "chrome_version": chrome_ver
    }
    return fp
