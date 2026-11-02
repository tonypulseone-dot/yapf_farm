import asyncio
import random
import logging
from urllib.parse import quote_plus
from app.engine.human import human_mouse_move, human_scroll, human_type
from app.engine.logger import live_logger

logger = logging.getLogger("promo")

async def run_promotion_click(page, keyword_item, profile_name):
    """
    Executes a high-quality SEO behavioral factor click:
    1. Search for target keyword in Yandex with region parameter.
    2. Scan SERP (up to 5 pages) to locate target domain / URL.
    3. (Optional) Check 1 competitor site for 10-15s.
    4. Click target site.
    5. Spend 40-120 seconds on target site with internal page browsing.
    """
    keyword = keyword_item["keyword"]
    domain = keyword_item["domain"].lower().replace("www.", "")
    target_url = keyword_item.get("target_url") or ""
    region = keyword_item.get("geo_region") or "213"
    
    logger.info(f"[{profile_name}] PROMO START: key='{keyword}', target='{domain}', region={region}")
    live_logger.add("PROMO", profile_name, f"Старт ПФ: запрос «{keyword}» для домена {domain} (регион {region})")
    
    # 1. Search in Yandex with region (retry on transient proxy errors)
    search_url = f"https://ya.ru/search/?text={quote_plus(keyword)}&lr={region}"
    loaded = False
    for attempt in range(1, 3):
        try:
            attempt_str = f" (попытка {attempt}/2)" if attempt > 1 else ""
            live_logger.add("PROMO", profile_name, f"Открытие поисковой выдачи: {keyword}{attempt_str}")
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2.0, 4.0))
            loaded = True
            break
        except Exception as e:
            err_str = str(e)
            if attempt < 2 and any(k in err_str for k in ["ERR_TUNNEL", "ERR_PROXY", "Timeout", "ERR_CONNECTION", "reset"]):
                live_logger.add("WARN", profile_name, f"Временный сбой прокси при поиске: пауза 2.5с и повторное переподключение...")
                await asyncio.sleep(2.5)
            else:
                live_logger.add("ERROR", profile_name, f"Ошибка загрузки поисковой выдачи: {err_str[:90]}")
                return {"status": "error", "position": 0, "details": f"Search page error: {e}"}

    if not loaded:
        return {"status": "error", "position": 0, "details": "Search page failed to load"}

    # Check for captcha
    if "showcaptcha" in page.url or await page.query_selector("form.CheckboxCaptcha, input.CheckboxCaptcha-Button, #smartcaptcha-anchor"):
        logger.warning(f"[{profile_name}] Captcha detected on Yandex SERP!")
        live_logger.add("WARN", profile_name, "Обнаружена SmartCaptcha при поиске, попытка авто-клика...")
        from app.engine.captcha import handle_yandex_checkbox_captcha
        if await handle_yandex_checkbox_captcha(page):
            live_logger.add("SUCCESS", profile_name, "SmartCaptcha пройдена кликом, продолжаем сканирование!")
            await asyncio.sleep(2.5)
        else:
            live_logger.add("WARN", profile_name, "SmartCaptcha требует ручного решения или ключа сервиса")
            return {"status": "captcha", "position": 0, "details": "Captcha on search"}

    target_found = False
    position_found = 0

    # Scan up to 5 pages
    for page_num in range(1, 6):
        live_logger.add("PROMO", profile_name, f"Сканирование выдачи: страница {page_num}...")
        # Check organic items
        items = await page.query_selector_all("li.serp-item")
        
        for idx, item in enumerate(items):
            link = await item.query_selector("a.organic__url, a.Link_theme_normal")
            if not link:
                continue
                
            href = await link.get_attribute("href") or ""
            text = await link.inner_text()
            
            # Check if domain matches
            if domain in href.lower() or domain in text.lower():
                target_found = True
                position_found = (page_num - 1) * 10 + (idx + 1)
                logger.info(f"[{profile_name}] TARGET FOUND! Domain '{domain}' at position #{position_found} (Page {page_num})")
                live_logger.add("SUCCESS", profile_name, f"🎯 Целевой домен «{domain}» найден на позиции #{position_found} (Стр. {page_num})!")
                
                # Human mouse move to link and click
                box = await link.bounding_box()
                if box:
                    await human_mouse_move(page, random.randint(100, 300), random.randint(100, 300), 
                                           box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                
                live_logger.add("PROMO", profile_name, f"Клик по целевому сайту {domain} в выдаче")
                async with page.expect_popup(timeout=10000) as popup_info:
                    await link.click()
                site_page = await popup_info.value
                
                # Browse target site 40-90 seconds with internal clicks
                dwell_time = random.uniform(40.0, 90.0)
                live_logger.add("PROMO", profile_name, f"Серфинг по сайту {domain}: {int(dwell_time)}с (просмотр разделов, скроллинг)")
                start_dwell = asyncio.get_event_loop().time()
                
                while asyncio.get_event_loop().time() - start_dwell < dwell_time:
                    if site_page.is_closed():
                        break
                    await human_scroll(site_page, random.randint(300, 700))
                    await asyncio.sleep(random.uniform(3.0, 7.0))
                    
                    # 40% chance to click internal link
                    if random.random() < 0.4 and not site_page.is_closed():
                        int_links = await site_page.query_selector_all(f"a[href*='{domain}']")
                        if int_links:
                            rand_link = random.choice(int_links[:5])
                            try:
                                await rand_link.click(timeout=5000)
                                await site_page.wait_for_load_state("domcontentloaded", timeout=10000)
                                live_logger.add("PROMO", profile_name, f"Переход на внутреннюю страницу сайта {domain}")
                            except Exception:
                                pass
                                
                if not site_page.is_closed():
                    await site_page.close()
                live_logger.add("SUCCESS", profile_name, f"Сессия ПФ успешно завершена: {domain} #{position_found}")
                return {"status": "success", "position": position_found, "details": f"Page {page_num}, Pos {position_found}"}

        # Next page button
        next_button = await page.query_selector("a.Pager-Item_type_next, a.pager__item_kind_next")
        if next_button:
            await next_button.click()
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await asyncio.sleep(random.uniform(2.0, 4.0))
        else:
            break

    logger.warning(f"[{profile_name}] Domain '{domain}' not found in top 50")
    live_logger.add("WARN", profile_name, f"Домен «{domain}» не найден в ТОП-50 по запросу «{keyword}»")
    return {"status": "not_found", "position": 0, "details": "Not in Top 50"}
