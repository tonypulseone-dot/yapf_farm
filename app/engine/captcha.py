import asyncio
import base64
import json
import logging
import random
import time
import urllib.parse
import urllib.request
import ssl
from app.database import get_setting

logger = logging.getLogger("captcha_engine")
ssl_ctx = ssl._create_unverified_context()

SERVICE_ENDPOINTS = {
    "capify": {
        "in": "https://api.capify.solutions/in.php",
        "res": "https://api.capify.solutions/res.php"
    },
    "capguru": {
        "in": "https://api.cap.guru/in.php",
        "res": "https://api.cap.guru/res.php"
    },
    "botlab": {
        "in": "https://botlab.top/in.php",
        "res": "https://botlab.top/res.php"
    },
    "capsola": {
        "in": "https://api.capsola.solutions/in.php",
        "res": "https://api.capsola.solutions/res.php"
    },
    "rucaptcha": {
        "in": "https://rucaptcha.com/in.php",
        "res": "https://rucaptcha.com/res.php"
    },
    "captrix": {
        "in": "https://api.captrix.solutions/in.php",
        "res": "https://api.captrix.solutions/res.php"
    }
}

def get_service_key(service_name: str) -> str:
    key_setting = f"key_{service_name.lower()}"
    return get_setting(key_setting, "").strip()

async def submit_captcha_task(service: str, base64_image: str, textinstructions: str = "") -> str:
    """Submits task to specified service via 2captcha/in.php standard protocol."""
    key = get_service_key(service)
    if not key and service != "xevil":
        return None

    if service == "xevil":
        xevil_url = get_setting("key_xevil_url", "http://127.0.0.1:80").rstrip("/")
        in_url = f"{xevil_url}/in.php"
        key = get_setting("key_xevil_key", "dummy")
    else:
        conf = SERVICE_ENDPOINTS.get(service)
        if not conf:
            return None
        in_url = conf["in"]

    payload = {
        "method": "base64",
        "key": key,
        "body": base64_image,
        "json": 1
    }
    if textinstructions:
        payload["textinstructions"] = textinstructions

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(in_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})

    loop = asyncio.get_event_loop()
    try:
        resp = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=15.0, context=ssl_ctx))
        res_json = json.loads(resp.read().decode("utf-8"))
        if res_json.get("status") == 1:
            return str(res_json.get("request"))
    except Exception as e:
        logger.warning(f"Error submitting to {service}: {e}")
    return None

async def poll_captcha_result(service: str, task_id: str, max_timeout: int = 60) -> str:
    """Polls res.php for solution until timeout or failure."""
    key = get_service_key(service)
    if service == "xevil":
        xevil_url = get_setting("key_xevil_url", "http://127.0.0.1:80").rstrip("/")
        res_url = f"{xevil_url}/res.php"
        key = get_setting("key_xevil_key", "dummy")
    else:
        conf = SERVICE_ENDPOINTS.get(service)
        if not conf:
            return None
        res_url = conf["res"]

    start_time = time.time()
    loop = asyncio.get_event_loop()

    while time.time() - start_time < max_timeout:
        await asyncio.sleep(2.5)
        query = urllib.parse.urlencode({
            "key": key,
            "action": "get",
            "id": task_id,
            "json": 1
        })
        url = f"{res_url}?{query}"
        req = urllib.request.Request(url)

        try:
            resp = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10.0, context=ssl_ctx))
            res_json = json.loads(resp.read().decode("utf-8"))
            if res_json.get("status") == 1:
                return res_json.get("request")
            if res_json.get("request") != "CAPCHA_NOT_READY":
                logger.warning(f"Service {service} returned error: {res_json.get('request')}")
                return None
        except Exception as e:
            logger.warning(f"Error polling {service}: {e}")
            await asyncio.sleep(2)

    return None

async def solve_with_priorities(captcha_type: str, base64_image: str, textinstructions: str = "") -> dict:
    """
    Attempts to solve captcha by traversing configured prioritized services in order.
    If service 1 fails or has no key, falls back to service 2, 3, etc.
    """
    if captcha_type == "text":
        services_raw = get_setting("captcha_text_services", "capify,capguru,botlab,capsola,rucaptcha")
    else:
        services_raw = get_setting("captcha_image_services", "capify,capguru,botlab,capsola,rucaptcha")

    services = [s.strip().lower() for s in services_raw.split(",") if s.strip()]
    timeout = int(get_setting("captcha_timeout_seconds", "60"))

    for service in services:
        key = get_service_key(service)
        if not key and service != "xevil":
            continue

        logger.info(f"Attempting to solve {captcha_type} captcha via: {service}")
        task_id = await submit_captcha_task(service, base64_image, textinstructions)
        if task_id:
            solution = await poll_captcha_result(service, task_id, max_timeout=timeout)
            if solution:
                logger.info(f"Captcha successfully solved by {service}!")
                return {"solved": True, "service": service, "solution": solution}
            else:
                logger.warning(f"Service {service} failed to solve. Falling back to next...")
        else:
            logger.warning(f"Could not submit to {service}. Falling back to next...")

    return {"solved": False, "service": None, "solution": None}

async def handle_yandex_checkbox_captcha(page) -> bool:
    """
    Detects and clicks the Yandex Checkbox / SmartCaptcha.
    Applies humanized delays and coordinate distortion if configured.
    """
    try:
        # Check if smartcaptcha iframe or checkbox exists
        checkbox = await page.query_selector("input.CheckboxCaptcha-Button, .CheckboxCaptcha-Anchor, #smartcaptcha-anchor")
        if checkbox:
            distort = get_setting("captcha_distort_coords", "1") == "1"
            box = await checkbox.bounding_box()
            if box:
                target_x = box["x"] + box["width"] / 2
                target_y = box["y"] + box["height"] / 2
                if distort:
                    target_x += random.uniform(-4, 4)
                    target_y += random.uniform(-4, 4)
                
                await page.mouse.move(target_x, target_y, steps=random.randint(7, 14))
                await asyncio.sleep(random.uniform(0.3, 0.7))
                await page.mouse.click(target_x, target_y)
                await asyncio.sleep(random.uniform(2.0, 3.5))
                return True
    except Exception as e:
        logger.warning(f"Checkbox captcha handling error: {e}")
    return False
