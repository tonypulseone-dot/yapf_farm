import asyncio
import os
from playwright.async_api import async_playwright

async def run_iphone15_promax_tests():
    screenshots_dir = "C:/YAPF_Farm/screenshots/iphone15_promax"
    os.makedirs(screenshots_dir, exist_ok=True)
    
    # iPhone 15 Pro Max specs: 430 x 932 pt, 3x DPR
    IPHONE_15_PRO_MAX = {
        "viewport": {"width": 430, "height": 932},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    }

    async with async_playwright() as p:
        print("[TEST] Launching Chromium in iPhone 15 Pro Max emulation (430x932)...")
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**IPHONE_15_PRO_MAX)
        page = await context.new_page()

        # 1. Login Page
        print("[TEST 1] Opening login page on iPhone 15 Pro Max...")
        await page.goto("http://127.0.0.1:8080/login", wait_until="networkidle")
        await page.screenshot(path=f"{screenshots_dir}/01_login.png")
        
        overflow = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"[TEST 1] Login horizontal overflow: {overflow}")
        assert not overflow, "Login page has horizontal overflow on iPhone 15 Pro Max!"

        # 2. Login Submit
        print("[TEST 2] Logging in...")
        await page.fill("input[name='username']", "admin")
        await page.fill("input[name='password']", "pre12345")
        await page.click("button[type='submit']")
        await page.wait_for_load_state("networkidle")
        assert page.url.endswith("/"), f"Expected dashboard root, got {page.url}"

        # 3. Overview Tab
        print("[TEST 3] Testing Overview Tab on iPhone 15 Pro Max...")
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{screenshots_dir}/02_overview.png")
        overflow = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"[TEST 3] Overview horizontal overflow: {overflow}")
        assert not overflow, "Overview tab has horizontal overflow on iPhone 15 Pro Max!"

        # 4. Projects Tab
        print("[TEST 4] Testing Projects & Keywords Tab...")
        await page.click("button:has-text('Проекты & Ключи')")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"{screenshots_dir}/03_projects.png")
        overflow = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"[TEST 4] Projects tab horizontal overflow: {overflow}")
        assert not overflow, "Projects tab has horizontal overflow on iPhone 15 Pro Max!"

        # 5. Logs Tab
        print("[TEST 5] Testing Click Logs Tab...")
        await page.click("button:has-text('История кликов')")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"{screenshots_dir}/04_logs.png")
        overflow = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"[TEST 5] Logs tab horizontal overflow: {overflow}")
        assert not overflow, "Logs tab has horizontal overflow on iPhone 15 Pro Max!"

        # 6. Settings & Telegram Tab
        print("[TEST 6] Testing Settings & Telegram Tab...")
        await page.click("button:has-text('Настройки & Telegram')")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"{screenshots_dir}/05_settings.png")
        overflow = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"[TEST 6] Settings tab horizontal overflow: {overflow}")
        assert not overflow, "Settings tab has horizontal overflow on iPhone 15 Pro Max!"

        # 7. Knowledge Base Tab: Full View
        print("[TEST 7] Testing Knowledge Base Tab...")
        await page.click("button:has-text('База знаний')")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"{screenshots_dir}/06_kb_overview.png")
        overflow = await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        print(f"[TEST 7] KB horizontal overflow: {overflow}")
        assert not overflow, "Knowledge base tab has horizontal overflow on iPhone 15 Pro Max!"

        # 8. Knowledge Base: Search for "max_threads"
        print("[TEST 8] Testing KB Live Search for 'max_threads'...")
        search_input = page.locator("input[placeholder*='Поиск по параметрам']")
        await search_input.fill("max_threads")
        await page.wait_for_timeout(400)
        await page.screenshot(path=f"{screenshots_dir}/07_kb_search_param.png")
        
        # Verify that Parameter Reference table is visible
        assert await page.locator("text=Полный справочник всех параметров YAPF_Farm").is_visible(), "Parameter table section should be visible!"
        assert await page.locator("td:has-text('max_threads')").first.is_visible(), "Parameter row max_threads should be visible!"

        # 9. Knowledge Base: Search for "капча"
        print("[TEST 9] Testing KB Live Search for 'капча'...")
        await search_input.fill("капча")
        await page.wait_for_timeout(400)
        await page.screenshot(path=f"{screenshots_dir}/08_kb_search_captcha.png")
        assert await page.locator("text=Антикапча, защита от фильтров").is_visible(), "Troubleshooting section should be visible for 'капча'!"

        # 10. Knowledge Base: Clear Search & Test Category Pills
        print("[TEST 10] Testing Clear Search & Category Pill...")
        await search_input.fill("")
        await page.evaluate("() => { const d = Alpine.$data(document.querySelector('body')); if (d) d.kbSearch = ''; }")
        await page.wait_for_timeout(300)
        
        # Click "Справочник всех параметров"
        await page.click("button:has-text('Справочник всех параметров')")
        await page.wait_for_timeout(500)
        
        debug_info = await page.evaluate("() => { const d = Alpine.$data(document.querySelector('body')); return { kbSearch: d ? d.kbSearch : 'no-d', kbSection: d ? d.kbSection : 'no-d' }; }")
        print(f"[TEST 10] DEBUG STATE: {debug_info}")
        
        elem_count = await page.locator("text=min_ready_age_hours").count()
        print(f"[TEST 10] min_ready_age_hours count in DOM: {elem_count}")
        
        await page.screenshot(path=f"{screenshots_dir}/09_kb_param_table.png")
        table_param = page.locator("td:has-text('min_ready_age_hours')")
        print(f"[TEST 10] Table param td count: {await table_param.count()}")
        assert await table_param.is_visible(), "Parameter min_ready_age_hours in table should be visible!"

        # Click "Таблица регионов (LR)"
        await page.click("button:has-text('Таблица регионов (LR)')")
        await page.wait_for_timeout(300)
        city_input = page.locator("input[placeholder='Поиск города...']")
        await city_input.fill("Казань")
        await page.wait_for_timeout(300)
        await page.screenshot(path=f"{screenshots_dir}/10_kb_regions_kazan.png")
        assert await page.locator("span:has-text('43')").first.is_visible(), "Kazan LR code 43 should be visible!"

        await browser.close()
        print("\n=======================================================")
        print("SUCCESS: ALL IPHONE 15 PRO MAX TESTS PASSED PERFECTLY (10/10)!")
        print("=======================================================\n")

if __name__ == "__main__":
    asyncio.run(run_iphone15_promax_tests())
