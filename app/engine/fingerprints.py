import random
import json

DESKTOP_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 YaBrowser/24.4.1.912 Yowser/2.5 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 YaBrowser/24.6.0.1874 Yowser/2.5 Safari/537.36"
]

MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 YaApp_Android/24.41 YaSearchBrowser/24.41 Mobile Safari/537.36"
]

DESKTOP_RESOLUTIONS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
    {"width": 1600, "height": 900},
    {"width": 1440, "height": 900}
]

MOBILE_RESOLUTIONS = [
    {"width": 393, "height": 873},
    {"width": 412, "height": 915},
    {"width": 390, "height": 844},
    {"width": 414, "height": 896}
]

WEBGL_VENDORS = [
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (Intel)", "renderer": "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (AMD)", "renderer": "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)"}
]

def generate_fingerprint(device_type="desktop"):
    """Generate authentic, high-reputation browser fingerprint dictionary."""
    is_mobile = (device_type == "android")
    
    if is_mobile:
        ua = random.choice(MOBILE_USER_AGENTS)
        res = random.choice(MOBILE_RESOLUTIONS)
        platform = "Linux armv8l"
        is_touch = True
    else:
        ua = random.choice(DESKTOP_USER_AGENTS)
        res = random.choice(DESKTOP_RESOLUTIONS)
        platform = "Win32"
        is_touch = False

    webgl = random.choice(WEBGL_VENDORS)
    
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
        "hardware_concurrency": random.choice([4, 6, 8, 12, 16]),
        "device_memory": random.choice([8, 16]),
        "webgl_vendor": webgl["vendor"],
        "webgl_renderer": webgl["renderer"],
        "has_touch": is_touch
    }
    return fp
