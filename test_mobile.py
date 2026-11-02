import asyncio
import os
from playwright.async_api import async_playwright

async def run_mobile_tests():
    os.makedirs("C:/YAPF_Farm/screenshots", exist_ok=True)
    async with async_playwright() as p:
        print("[TEST] Launching Chromium in mobile emulation mode (iPhone 14)...")
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 393, "height": 852},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
        )
        page = await context.new_page()

        # 1. Login Page test
        print("[TEST] 1. Opening login page...")
        await page.goto("http://127.0.0.1:8080/login", wait_until="networkidle")
        await page.screenshot(path="C:/YAPF_Farm/screenshots/01_mobile_login.png")
        
        login_overflow = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"[TEST] Login page horizontal overflow: {login_overflow}")
        assert not login_overflow, "Horizontal overflow detected on login page!"

        # 2. Perform Login
        print("[TEST] 2. Logging in...")
        await page.fill("input[name='username']", "admin")
        await page.fill("input[name='password']", "pre12345")
        await page.click("button[type='submit']")
        await page.wait_for_load_state("networkidle")
        print(f"[TEST] Current URL after login: {page.url}")
        assert page.url.endswith("/"), f"Expected redirect to dashboard root, got {page.url}"

        # 3. Overview Tab
        print("[TEST] 3. Verifying Overview tab...")
        await page.wait_for_timeout(1000)
        await page.screenshot(path="C:/YAPF_Farm/screenshots/02_mobile_overview.png")
        overview_overflow = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"[TEST] Overview tab horizontal overflow: {overview_overflow}")
        assert not overview_overflow, "Horizontal overflow detected on Overview tab!"

        # 4. Settings & Telegram Tab
        print("[TEST] 4. Switching to Settings & Telegram tab...")
        await page.click("button:has-text('Настройки')")
        await page.wait_for_timeout(500)
        await page.screenshot(path="C:/YAPF_Farm/screenshots/03_mobile_settings.png")
        
        # Test Telegram button presence and click
        tg_btn = page.locator("button:has-text('Проверить отправку в Telegram')")
        assert await tg_btn.count() > 0, "Telegram test button not found!"
        await tg_btn.click()
        await page.wait_for_timeout(1000)
        status_text = await page.locator("text=Укажите Bot Token и Chat ID").count()
        print(f"[TEST] Telegram validation response rendered correctly: {status_text > 0}")

        # 5. Knowledge Base & Manual Tab
        print("[TEST] 5. Switching to Knowledge Base tab...")
        await page.click("button:has-text('База знаний')")
        await page.wait_for_timeout(1000)
        await page.screenshot(path="C:/YAPF_Farm/screenshots/04_mobile_manual.png")
        
        # Test KB filter pills
        print("[TEST] 6. Testing fast section navigation...")
        kb_btn = page.locator("button:has-text('pffarm.ru')")
        print(f"Found pffarm.ru button count: {await kb_btn.count()}")
        if await kb_btn.count() > 0:
            await kb_btn.first.click()
            await page.wait_for_timeout(500)
        
        elem_count = await page.locator("text=65.109.26.20").count()
        print(f"65.109.26.20 count in DOM: {elem_count}")
        first_vis = await page.locator("text=65.109.26.20").first.is_visible()
        print(f"65.109.26.20 first visible: {first_vis}")

        await page.click("button:has-text('Калькулятор потоков')")
        await page.wait_for_timeout(300)
        assert await page.locator("text=AMD Ryzen 7 3700X").first.is_visible(), "Hardware specs not visible!"

        await page.screenshot(path="C:/YAPF_Farm/screenshots/05_mobile_hardware.png")

        # 6. Android Viewport Test (412x915)
        print("[TEST] 7. Testing Android viewport (412x915)...")
        await page.set_viewport_size({"width": 412, "height": 915})
        await page.wait_for_timeout(500)
        android_overflow = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"[TEST] Android viewport horizontal overflow: {android_overflow}")
        assert not android_overflow, "Horizontal overflow detected on Android viewport!"

        await browser.close()
        print("[TEST] ALL MOBILE TESTS PASSED SUCCESSFULLY! 100% OK")

if __name__ == "__main__":
    asyncio.run(run_mobile_tests())
