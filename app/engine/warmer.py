import asyncio
import random
import logging
from datetime import datetime, timedelta
from app.engine.human import human_mouse_move, human_scroll, human_type
from app.engine.logger import live_logger

logger = logging.getLogger("warmer")

INFORMATIONAL_QUERIES = [
    "как приготовить шарлотку с яблоками в духовке",
    "погода на неделю в москве и области",
    "какие фильмы выйдут в кинотеатрах в этом месяце",
    "почему желтеют листья у комнатных растений",
    "как выбрать беспроводные наушники для бега",
    "простые рецепты завтраков на каждый день",
    "расписание электричек и пригородных поездов",
    "что подарить коллеге на день рождения идеи",
    "советы как улучшить качество сна взрослому",
    "как ухаживать за кожаной обувью зимой",
    "лучшие места для отдыха с детьми на выходных",
    "как быстро очистить чайник от накипи дома",
    "сколько варить яйца вкрутую по времени",
    "упражнения для растяжки спины дома"
]

async def run_warming_cycle(page, profile_id, profile_name):
    """
    Executes exactly 1 standard warming cycle:
    1. Go to Yandex search (ya.ru).
    2. Enter an informational query with human typing.
    3. Spend 8-12 seconds in SERP browsing snippets.
    4. Click on 1 organic site from results.
    5. Spend 20-40 seconds on the site scrolling and reading.
    """
    query = random.choice(INFORMATIONAL_QUERIES)
    logger.info(f"[{profile_name}] Starting warming cycle with query: '{query}'")
    live_logger.add("WARM", profile_name, f"Старт цикла нагула: запрос «{query}»")
    
    # 1. Open Yandex with proxy resilience (retry on transient tunnel blips)
    loaded = False
    for attempt in range(1, 3):
        try:
            attempt_str = f" (попытка {attempt}/2)" if attempt > 1 else ""
            live_logger.add("WARM", profile_name, f"Открытие поисковой системы https://ya.ru...{attempt_str}")
            await page.goto("https://ya.ru", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(1.5, 3.0))
            loaded = True
            break
        except Exception as e:
            err_str = str(e)
            if attempt < 2 and any(k in err_str for k in ["ERR_TUNNEL", "ERR_PROXY", "Timeout", "ERR_CONNECTION", "reset"]):
                live_logger.add("WARN", profile_name, f"Временный сбой прокси ya.ru: пауза 2.5с и повторное переподключение...")
                await asyncio.sleep(2.5)
            else:
                logger.warning(f"[{profile_name}] Failed to load ya.ru: {e}")
                live_logger.add("ERROR", profile_name, f"Ошибка загрузки ya.ru: {err_str[:90]}")
                return False

    if not loaded:
        return False
    
    # 2. Check for SmartCaptcha or find search input
    search_input = "input[name='text'], textarea[name='text']"
    try:
        # Check if Yandex showed SmartCaptcha right away
        if "showcaptcha" in page.url or await page.query_selector(".CheckboxCaptcha, #smartcaptcha-anchor"):
            live_logger.add("WARN", profile_name, "Обнаружена SmartCaptcha, попытка автоматического подтверждения...")
            from app.engine.captcha import handle_yandex_checkbox_captcha
            if await handle_yandex_checkbox_captcha(page):
                live_logger.add("SUCCESS", profile_name, "SmartCaptcha успешно пройдена кликом!")
                await asyncio.sleep(2.0)

        await page.wait_for_selector(search_input, timeout=10000)
        live_logger.add("WARM", profile_name, f"Ввод запроса «{query}» (эмуляция набора человека с задержками)")
        await human_type(page, search_input, query)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception as e:
        # Fallback check for captcha if input was blocked
        from app.engine.captcha import handle_yandex_checkbox_captcha
        if await handle_yandex_checkbox_captcha(page):
            live_logger.add("SUCCESS", profile_name, "SmartCaptcha пройдена после ожидания формы ввода!")
            try:
                await page.wait_for_selector(search_input, timeout=8000)
                await human_type(page, search_input, query)
                await page.keyboard.press("Enter")
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                return False
        else:
            logger.warning(f"[{profile_name}] Search input failed: {e}")
            live_logger.add("WARN", profile_name, f"Не удалось ввести запрос: {str(e)[:100]}")
            return False
        
    # 3. Spend 8-12 seconds in SERP
    serp_time = random.uniform(8.0, 12.0)
    live_logger.add("WARM", profile_name, f"Просмотр поисковой выдачи SERP (скроллинг {int(serp_time)} сек)")
    start_t = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_t < serp_time:
        await human_scroll(page, random.randint(150, 450))
        await asyncio.sleep(random.uniform(1.0, 2.5))
        
    # 4. Find organic search results (skip direct ads if possible)
    links = await page.query_selector_all("li.serp-item a.organic__url, a.Link_theme_normal")
    if not links:
        links = await page.query_selector_all("a[href^='http']")
        
    if not links:
        logger.warning(f"[{profile_name}] No search result links found on SERP")
        live_logger.add("WARN", profile_name, "Органические ссылки на странице не найдены (куки зафиксированы)")
        return True # still counts as query presence
        
    # Pick a random result between 1 and 5
    target_link = random.choice(links[:min(5, len(links))])
    
    try:
        # Move mouse realistically to the link and click
        box = await target_link.bounding_box()
        if box:
            await human_mouse_move(page, random.randint(100, 300), random.randint(100, 300), 
                                   box["x"] + box["width"]/2, box["y"] + box["height"]/2)
            
        live_logger.add("WARM", profile_name, "Клик по сайту из органической выдачи -> переход")
        async with page.expect_popup(timeout=8000) as popup_info:
            await target_link.click()
        site_page = await popup_info.value
    except Exception:
        # If opened in same tab or popup timed out
        site_page = page

    # 5. Browse the target site for 20-40 seconds
    try:
        if site_page.is_closed():
            site_page = page

        if not site_page.is_closed():
            await site_page.wait_for_load_state("domcontentloaded", timeout=15000)
            browse_time = random.uniform(20.0, 40.0)
            start_browse = asyncio.get_event_loop().time()
            logger.info(f"[{profile_name}] Browsing warmed site for {int(browse_time)}s...")
            live_logger.add("WARM", profile_name, f"Серфинг по сайту: чтение, скроллинг {int(browse_time)}с (нагул поведенческого фактора)")
            
            while asyncio.get_event_loop().time() - start_browse < browse_time:
                if site_page.is_closed():
                    break
                await human_scroll(site_page, random.randint(200, 600))
                await asyncio.sleep(random.uniform(2.0, 5.0))
                
            if site_page != page and not site_page.is_closed():
                await site_page.close()
    except Exception as e:
        logger.debug(f"[{profile_name}] Site browsing interrupted: {e}")

    logger.info(f"[{profile_name}] Warming cycle successfully completed!")
    live_logger.add("SUCCESS", profile_name, "Цикл нагула успешно завершен, сессионные куки зафиксированы!")
    return True
