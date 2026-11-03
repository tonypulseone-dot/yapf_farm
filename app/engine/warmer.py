import asyncio
import random
import logging
from datetime import datetime
from app.engine.human import human_mouse_move, human_scroll, human_type
from app.engine.logger import live_logger

logger = logging.getLogger("warmer")

INFORMATIONAL_QUERIES = [
    "как приготовить пышную шарлотку с яблоками в духовке",
    "прогноз погоды на выходные в москве и области",
    "какие новинки фильмов вышли в кинотеатрах в этом месяце",
    "почему желтеют кончики листьев у комнатных растений что делать",
    "как выбрать надежные беспроводные наушники для смартфона",
    "простые и вкусные рецепты быстрых завтраков на каждый день",
    "расписание пригородных электричек на сегодня онлайн",
    "что подарить коллеге на день рождения оригинальные идеи",
    "советы врачей как улучшить качество и глубину сна взрослому",
    "как правильно ухаживать за кожаной и замшевой обувью",
    "лучшие интересные места для отдыха с детьми на выходных",
    "как быстро и безопасно очистить чайник от накипи лимонной кислотой",
    "сколько минут варить куриные яйца вкрутую и всмятку",
    "эффективные упражнения для снятия напряжения со спины и шеи",
    "какие книги стоит прочитать для саморазвития список",
    "как правильно выбрать моторное масло для автомобиля советы",
    "почему стиральная машина сильно вибрирует при отжиме причины",
    "рецепт классического домашнего борща с говядиной пошагово",
    "как научиться быстрее засыпать без снотворного методы",
    "подборка лучших сериалов с высоким рейтингом за последние годы",
    "как убрать следы скотча с пластиковых окон и мебели дома",
    "нормы артериального давления по возрастам таблица",
    "какой робот пылесос лучше выбрать для квартиры с коврами",
    "как правильно пересаживать орхидею в домашних условиях",
    "советы как экономить заряд батареи на телефоне андроид",
    "какие витамины лучше принимать весной для иммунитета",
    "как приготовить нежные сырники из творога на сковороде",
    "сколько шагов в день нужно проходить для поддержания здоровья",
    "как выбрать качественный матрас для сна при болях в спине",
    "интересные факты о космосе и солнечной системе для детей"
]

POPULAR_METRIKA_SITES = [
    "https://ria.ru",
    "https://rbc.ru",
    "https://lenta.ru",
    "https://habr.com/ru/all/",
    "https://www.kinopoisk.ru",
    "https://drom.ru",
    "https://championat.com",
    "https://www.sports.ru",
    "https://auto.ru",
    "https://www.banki.ru",
    "https://vc.ru",
    "https://ru.wikipedia.org/wiki/Заглавная_страница",
    "https://otzovik.com",
    "https://irecommend.ru"
]

MAPS_QUERIES = [
    "аптека круглосуточная рядом",
    "кофейня поблизости",
    "продуктовый супермаркет",
    "автосервис шиномонтаж",
    "пункт выдачи заказов озон вайлдберриз"
]

async def _warm_step_yandex_search(page, profile_name: str, query: str) -> bool:
    """One search session on ya.ru with SERP scroll and organic click."""
    try:
        live_logger.add("WARM", profile_name, f"Поиск ya.ru: ввод запроса «{query}»")
        
        # 1. Open ya.ru
        try:
            await page.goto("https://ya.ru", wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(random.uniform(1.2, 2.5))
        except Exception as e:
            logger.warning(f"[{profile_name}] ya.ru load warning: {e}")
            return False

        # 2. Check for captcha
        from app.engine.captcha import handle_yandex_checkbox_captcha
        if "showcaptcha" in page.url or await page.query_selector(".CheckboxCaptcha, #smartcaptcha-anchor"):
            live_logger.add("WARN", profile_name, "SmartCaptcha на ya.ru: автоматический клик подтверждения...")
            await handle_yandex_checkbox_captcha(page)
            await asyncio.sleep(1.5)

        # 3. Type query
        search_input = "input[name='text'], textarea[name='text']"
        try:
            await page.wait_for_selector(search_input, timeout=7000)
            await human_type(page, search_input, query)
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            # Fallback retry after captcha check
            if await handle_yandex_checkbox_captcha(page):
                await page.wait_for_selector(search_input, timeout=5000)
                await human_type(page, search_input, query)
                await page.keyboard.press("Enter")
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            else:
                return False

        # 4. SERP browsing
        serp_time = random.uniform(6.0, 10.0)
        live_logger.add("WARM", profile_name, f"SERP выдача: изучение сниппетов ({int(serp_time)} сек)")
        start_t = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_t < serp_time:
            await human_scroll(page, random.randint(150, 450))
            await asyncio.sleep(random.uniform(0.8, 2.0))

        # 5. Click organic link
        links = await page.query_selector_all("li.serp-item a.organic__url, a.Link_theme_normal")
        if not links:
            links = await page.query_selector_all("a[href^='http']")

        if links:
            target_link = random.choice(links[:min(5, len(links))])
            try:
                box = await target_link.bounding_box()
                if box:
                    await human_mouse_move(page, random.randint(50, 200), random.randint(50, 200),
                                           box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                
                live_logger.add("WARM", profile_name, "Клик по сайту из поисковой выдачи...")
                async with page.expect_popup(timeout=7000) as popup_info:
                    await target_link.click()
                site_page = await popup_info.value
            except Exception:
                site_page = page

            # 6. Browse site
            try:
                if not site_page.is_closed():
                    await site_page.wait_for_load_state("domcontentloaded", timeout=15000)
                    browse_time = random.uniform(15.0, 30.0)
                    start_browse = asyncio.get_event_loop().time()
                    while asyncio.get_event_loop().time() - start_browse < browse_time:
                        if site_page.is_closed():
                            break
                        await human_scroll(site_page, random.randint(180, 500))
                        await asyncio.sleep(random.uniform(1.5, 4.0))

                    if site_page != page and not site_page.is_closed():
                        await site_page.close()
            except Exception:
                pass

        return True
    except Exception as e:
        logger.debug(f"[{profile_name}] Search step error: {e}")
        return False

async def _warm_step_direct_site(page, profile_name: str, site_url: str) -> bool:
    """Direct visit to high-traffic Russian site with Yandex Metrika."""
    try:
        domain = site_url.split("//")[-1].split("/")[0]
        live_logger.add("WARM", profile_name, f"Прямой визит на {domain} (нагул cookie-истории Метрики)")
        await page.goto(site_url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(random.uniform(1.5, 3.0))

        browse_time = random.uniform(12.0, 25.0)
        start_browse = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_browse < browse_time:
            if page.is_closed():
                break
            await human_scroll(page, random.randint(200, 550))
            await asyncio.sleep(random.uniform(1.8, 3.5))

        return True
    except Exception as e:
        logger.debug(f"[{profile_name}] Direct site visit error: {e}")
        return False

async def _warm_step_yandex_dzen(page, profile_name: str) -> bool:
    """Browsing Yandex Dzen news and feed."""
    try:
        live_logger.add("WARM", profile_name, "Нагул по Яндекс Дзену (dzen.ru): лента статей и рекомендаций")
        await page.goto("https://dzen.ru", wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(random.uniform(2.0, 4.0))

        # Scroll feed
        for _ in range(random.randint(3, 5)):
            if page.is_closed():
                break
            await human_scroll(page, random.randint(250, 600))
            await asyncio.sleep(random.uniform(1.5, 3.0))

        return True
    except Exception as e:
        logger.debug(f"[{profile_name}] Dzen visit error: {e}")
        return False

async def _warm_step_yandex_maps(page, profile_name: str) -> bool:
    """Interacting with Yandex Maps."""
    try:
        query = random.choice(MAPS_QUERIES)
        live_logger.add("WARM", profile_name, f"Нагул по Яндекс Картам: поиск организаций «{query}»")
        await page.goto("https://yandex.ru/maps", wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(random.uniform(2.0, 4.0))

        search_inp = "input.input__control"
        try:
            await page.wait_for_selector(search_inp, timeout=6000)
            await human_type(page, search_inp, query)
            await page.keyboard.press("Enter")
            await asyncio.sleep(random.uniform(3.0, 6.0))
            # Scroll results panel
            await human_scroll(page, random.randint(200, 400))
        except Exception:
            pass

        return True
    except Exception as e:
        logger.debug(f"[{profile_name}] Maps visit error: {e}")
        return False

async def run_warming_cycle(page, profile_id: int, profile_name: str) -> bool:
    """
    Executes a comprehensive, multi-step warming cycle matching AlpineSoft:
    - 3 to 5 search queries in Yandex with organic SERP clicks
    - 3 to 5 direct visits to popular Russian sites (generating Metrika/Google cookies)
    - Random visits to Yandex Dzen / Yandex Maps
    """
    num_queries = random.randint(3, 5)
    num_direct = random.randint(3, 5)

    live_logger.add("WARM", profile_name, f"Старт расширенного цикла нагула (Alpine-style: {num_queries} запросов + {num_direct} сайтов)")
    
    # 1. Execute search queries
    selected_queries = random.sample(INFORMATIONAL_QUERIES, min(num_queries, len(INFORMATIONAL_QUERIES)))
    for idx, q in enumerate(selected_queries, 1):
        live_logger.add("WARM", profile_name, f"Поисковая сессия {idx}/{num_queries}")
        await _warm_step_yandex_search(page, profile_name, q)
        await asyncio.sleep(random.uniform(2.0, 4.5))

    # 2. Random chance (35%) to visit Yandex Dzen or Maps
    service_roll = random.random()
    if service_roll < 0.20:
        await _warm_step_yandex_dzen(page, profile_name)
        await asyncio.sleep(random.uniform(2.0, 4.0))
    elif service_roll < 0.35:
        await _warm_step_yandex_maps(page, profile_name)
        await asyncio.sleep(random.uniform(2.0, 4.0))

    # 3. Execute direct site visits
    selected_sites = random.sample(POPULAR_METRIKA_SITES, min(num_direct, len(POPULAR_METRIKA_SITES)))
    for idx, site in enumerate(selected_sites, 1):
        live_logger.add("WARM", profile_name, f"Прямой визит {idx}/{num_direct}")
        await _warm_step_direct_site(page, profile_name, site)
        await asyncio.sleep(random.uniform(2.0, 4.0))

    live_logger.add("SUCCESS", profile_name, f"Расширенный цикл нагула завершен ({num_queries} запросов, {num_direct} сайтов). Куки сохранены!")
    logger.info(f"[{profile_name}] Multi-step warming cycle completed successfully!")
    return True
