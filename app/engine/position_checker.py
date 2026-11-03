import asyncio
import logging
import random
import re
import urllib.parse
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

from app.database import (
    get_db, get_setting, record_position
)
from app.engine.proxy import parse_proxy_line
from app.engine.captcha import handle_yandex_checkbox_captcha
from app.engine.human import human_scroll, human_mouse_move
from app.engine.logger import live_logger

logger = logging.getLogger("position_checker")

class PositionCheckerManager:
    def __init__(self):
        self.is_checking = False
        self.current_project_id = None
        self.progress = {
            "is_running": False,
            "project_id": None,
            "project_name": "",
            "current": 0,
            "total": 0,
            "last_log": "Проверка не запущена",
            "percent": 0
        }
        self._lock = asyncio.Lock()

    def get_progress(self):
        return self.progress

    async def check_single_keyword(self, keyword_row: dict, project_row: dict, region: str = None, device_type: str = "desktop", max_pages: int = 10) -> dict:
        """
        Parses Yandex SERP up to Top-100 to find the domain's organic position.
        """
        domain = project_row["domain"].lower().strip().replace("https://", "").replace("http://", "").split("/")[0]
        keyword = keyword_row["keyword"].strip()
        geo = region or keyword_row.get("geo_region") or project_row.get("default_region") or "213"
        
        # Pick proxy
        proxy_lines = [p.strip() for p in get_setting("proxy_list", "").split("\n") if p.strip()]
        proxy_config = None
        if proxy_lines:
            chosen = random.choice(proxy_lines)
            proxy_config, _ = parse_proxy_line(chosen, default_scheme=get_setting("proxy_type", "http"))

        async with async_playwright() as p:
            is_mobile = (device_type == "android")
            viewport = {"width": 390, "height": 844} if is_mobile else {"width": 1920, "height": 1080}
            ua = (
                "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36"
                if is_mobile else
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )

            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                viewport=viewport,
                user_agent=ua,
                is_mobile=is_mobile,
                has_touch=is_mobile,
                proxy=proxy_config,
                locale="ru-RU",
                timezone_id="Europe/Moscow"
            )
            page = await context.new_page()

            found_pos = 0
            found_url = ""
            current_organic_rank = 0

            try:
                for p_idx in range(max_pages):
                    encoded_q = urllib.parse.quote_plus(keyword)
                    url = f"https://ya.ru/search/?text={encoded_q}&lr={geo}&p={p_idx}"
                    
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(random.uniform(1.2, 2.0))
                    except Exception as ge:
                        logger.warning(f"Error loading SERP page {p_idx}: {ge}")
                        break

                    # Check SmartCaptcha
                    if "showcaptcha" in page.url or await page.query_selector(".CheckboxCaptcha, #smartcaptcha-anchor"):
                        solved = await handle_yandex_checkbox_captcha(page)
                        if not solved:
                            logger.warning("SmartCaptcha could not be solved during position check.")
                            break
                        await asyncio.sleep(2.0)

                    # Extract organic results
                    # Exclude ads and direct
                    items = await page.query_selector_all("li.serp-item, div.serp-item")
                    if not items:
                        # Fallback links
                        links = await page.query_selector_all("a.organic__url, a.Link_theme_normal")
                        for l in links:
                            current_organic_rank += 1
                            href = await l.get_attribute("href") or ""
                            if domain in href.lower():
                                found_pos = current_organic_rank
                                found_url = href
                                break
                    else:
                        for item in items:
                            # Check if item is advertisement
                            is_ad = await item.evaluate("""el => {
                                return el.classList.contains('serp-item_type_ad') || 
                                       el.querySelector('.Organic-DirectSnippet') !== null ||
                                       el.querySelector('.b-spec-adv') !== null ||
                                       el.hasAttribute('data-adv');
                            }""")
                            if is_ad:
                                continue

                            link = await item.query_selector("a.organic__url, a.Link_theme_normal, h2 a")
                            if not link:
                                continue

                            current_organic_rank += 1
                            href = await link.get_attribute("href") or ""
                            if domain in href.lower():
                                found_pos = current_organic_rank
                                found_url = href
                                break

                    if found_pos > 0:
                        break

                    # Scroll before next page
                    await human_scroll(page, random.randint(200, 400))
                    await asyncio.sleep(random.uniform(0.8, 1.5))

            finally:
                await browser.close()

            # Record into database
            record_position(
                keyword_id=keyword_row["id"],
                project_id=project_row["id"],
                keyword=keyword,
                domain=domain,
                region=geo,
                device_type=device_type,
                position=found_pos,
                target_url=found_url,
                source="checker"
            )

            return {
                "keyword_id": keyword_row["id"],
                "keyword": keyword,
                "position": found_pos,
                "target_url": found_url,
                "region": geo,
                "device_type": device_type
            }

    async def check_project_positions(self, project_id: int, region: str = None, device_type: str = "desktop", fresh: bool = True):
        """
        Runs check across all keywords in a project in the background.
        """
        async with self._lock:
            if self.is_checking:
                return {"status": "busy", "message": "Проверка уже выполняется"}

            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            proj = c.fetchone()
            if not proj:
                conn.close()
                return {"status": "error", "message": "Проект не найден"}
            proj_dict = dict(proj)

            c.execute("SELECT * FROM keywords WHERE project_id = ? AND is_active = 1", (project_id,))
            keywords = [dict(r) for r in c.fetchall()]
            conn.close()

            if not keywords:
                return {"status": "error", "message": "В проекте нет активных ключевых запросов"}

            self.is_checking = True
            self.current_project_id = project_id
            self.progress = {
                "is_running": True,
                "project_id": project_id,
                "project_name": proj_dict["name"],
                "current": 0,
                "total": len(keywords),
                "last_log": f"Старт съема позиций для проекта «{proj_dict['name']}»...",
                "percent": 0
            }

        live_logger.add("INFO", "checker", f"Запущен съем позиций для проекта «{proj_dict['name']}» ({len(keywords)} ключей, Топ-100)")

        try:
            for idx, kw in enumerate(keywords, 1):
                self.progress["current"] = idx
                self.progress["percent"] = round((idx / len(keywords)) * 100, 1)
                self.progress["last_log"] = f"Проверка [{idx}/{len(keywords)}]: «{kw['keyword']}»"
                
                try:
                    res = await self.check_single_keyword(
                        keyword_row=kw,
                        project_row=proj_dict,
                        region=region,
                        device_type=device_type
                    )
                    pos_label = f"#{res['position']}" if res["position"] > 0 else ">100"
                    live_logger.add("SUCCESS", "checker", f"Позиция определена: «{kw['keyword']}» -> {pos_label}")
                except Exception as e:
                    logger.error(f"Error checking position for '{kw['keyword']}': {e}")
                    live_logger.add("ERROR", "checker", f"Ошибка проверки «{kw['keyword']}»: {e}")

                await asyncio.sleep(random.uniform(2.0, 4.0))

            live_logger.add("SUCCESS", "checker", f"Съем позиций для проекта «{proj_dict['name']}» полностью завершен!")
            self.progress["last_log"] = "Проверка завершена успешно!"

        finally:
            self.is_checking = False
            self.progress["is_running"] = False

    async def daily_scheduler_loop(self):
        """
        Background scheduler that checks positions automatically once per day (default 06:00 MSK).
        """
        logger.info("Position Checker daily scheduler initialized.")
        while True:
            try:
                enabled = get_setting("position_check_enabled", "1") == "1"
                target_hour = int(get_setting("position_check_hour", "6"))
                
                # Check current MSK time
                now_msk = datetime.utcnow() + timedelta(hours=3)
                today_str = now_msk.strftime("%Y-%m-%d")
                last_checked_date = get_setting("position_last_auto_check_date", "")

                if enabled and now_msk.hour >= target_hour and last_checked_date != today_str:
                    logger.info(f"Triggering scheduled daily position check for all projects ({now_msk.strftime('%H:%M')} MSK)...")
                    live_logger.add("INFO", "checker", "Запуск планового утреннего съема позиций для всех проектов")
                    
                    conn = get_db()
                    c = conn.cursor()
                    c.execute("SELECT id FROM projects WHERE is_active = 1")
                    proj_ids = [r["id"] for r in c.fetchall()]
                    conn.close()

                    for pid in proj_ids:
                        await self.check_project_positions(pid)
                        await asyncio.sleep(10)

                    from app.database import set_setting
                    set_setting("position_last_auto_check_date", today_str)

            except Exception as e:
                logger.error(f"Error in position checker scheduler: {e}")

            await asyncio.sleep(60)

position_checker = PositionCheckerManager()
