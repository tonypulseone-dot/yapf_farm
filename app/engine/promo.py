import asyncio
import random
import logging
from urllib.parse import quote_plus, urlsplit
from app.engine.human import human_mouse_move, human_scroll, human_type
from app.engine.logger import live_logger

logger = logging.getLogger("promo")


def target_host_matches(url, domain):
    try:
        target = urlsplit(domain if '://' in domain else 'https://' + domain).hostname or ''
        observed = urlsplit(url).hostname or ''
        target = target.lower().removeprefix('www.').rstrip('.').encode('idna').decode('ascii')
        observed = observed.lower().removeprefix('www.').rstrip('.').encode('idna').decode('ascii')
        return bool(target) and observed == target
    except (ValueError, UnicodeError):
        return False

async def _search_and_click(page, search_query: str, region: str, domain: str, profile_name: str, max_pages: int = 5, is_tail: bool = False):
    """Executes search and scans SERP for target domain up to max_pages."""
    search_url = f"https://ya.ru/search/?text={quote_plus(search_query)}&lr={region}"
    label = " (с брендовым хвостом)" if is_tail else ""
    
    loaded = False
    for attempt in range(1, 3):
        try:
            attempt_str = f" (попытка {attempt}/2)" if attempt > 1 else ""
            live_logger.add("PROMO", profile_name, f"Открытие поисковой выдачи: «{search_query}»{label}{attempt_str}")
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

    # Check for SmartCaptcha
    if "showcaptcha" in page.url or await page.query_selector("form.CheckboxCaptcha, input.CheckboxCaptcha-Button, #smartcaptcha-anchor"):
        logger.warning(f"[{profile_name}] Captcha detected on Yandex SERP!")
        live_logger.add("WARN", profile_name, "Обнаружена SmartCaptcha при поиске, попытка авто-клика...")
        from app.engine.captcha import handle_yandex_checkbox_captcha
        if await handle_yandex_checkbox_captcha(page):
            await asyncio.sleep(2.5)
            if "showcaptcha" in page.url or await page.query_selector("form.CheckboxCaptcha, input.CheckboxCaptcha-Button, #smartcaptcha-anchor"):
                return {"status": "captcha", "position": 0, "details": "Search remains blocked by captcha"}
            live_logger.add("INFO", profile_name, "Проверка CAPTCHA завершена, проверяем выдачу")
        else:
            live_logger.add("WARN", profile_name, "SmartCaptcha требует ручного решения или ключа сервиса")
            return {"status": "captcha", "position": 0, "details": "Captcha on search"}

    # Scan pages
    for page_num in range(1, max_pages + 1):
        live_logger.add("PROMO", profile_name, f"Сканирование выдачи{label}: страница {page_num}...")
        items = await page.query_selector_all("li.serp-item")
        if not items:
            # An empty/challenge/unrecognized document is not evidence that
            # the target is absent from the requested search results.
            return {"status": "error", "position": 0,
                    "details": "Search results unavailable: no recognized result items"}
        
        for idx, item in enumerate(items):
            link = await item.query_selector("a.organic__url, a.Link_theme_normal")
            if not link:
                continue
                
            href = await link.get_attribute("href") or ""
            text = await link.inner_text()
            
            # Check if domain matches
            # A displayed target may use a redirect, but success is only recorded
            # after a verified navigation to the actual host below.
            if target_host_matches(href, domain) or target_host_matches('https://' + text.strip().split('/')[0], domain):
                position_found = (page_num - 1) * 10 + (idx + 1)
                tail_str = " (по брендовому хвосту)" if is_tail else ""
                logger.info(f"[{profile_name}] TARGET FOUND! Domain '{domain}' at position #{position_found} (Page {page_num}){tail_str}")
                live_logger.add("SUCCESS", profile_name, f"🎯 Домен «{domain}» найден на позиции #{position_found} (Стр. {page_num}){tail_str}!")
                
                # Human mouse move to link and click
                box = await link.bounding_box()
                if box:
                    await human_mouse_move(page, random.randint(100, 300), random.randint(100, 300), 
                                           box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                
                live_logger.add("PROMO", profile_name, f"Клик по целевому сайту {domain} в выдаче")
                click_completed = False
                site_page = page
                try:
                    async with page.expect_popup(timeout=10000) as popup_info:
                        await link.click()
                        click_completed = True
                    site_page = await popup_info.value
                except Exception as exc:
                    if not click_completed:
                        return {'status': 'error', 'position': position_found,
                                'details': 'Target link click failed: ' + type(exc).__name__}
                    # No popup is normal for same-tab navigation. Verify its URL.
                try:
                    await site_page.wait_for_url(lambda url: target_host_matches(str(url), domain), timeout=15000)
                    await site_page.wait_for_load_state('domcontentloaded', timeout=15000)
                except Exception as exc:
                    return {'status': 'error', 'position': position_found,
                            'details': 'Target navigation not confirmed: ' + type(exc).__name__}
                if not target_host_matches(site_page.url, domain):
                    return {'status': 'error', 'position': position_found,
                            'details': 'Navigation reached a different hostname'}
                verified_url = site_page.url
                
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
                                
                if site_page != page and not site_page.is_closed():
                    await site_page.close()

                live_logger.add("SUCCESS", profile_name, f"Сессия ПФ успешно завершена: {domain} #{position_found}{tail_str}")
                return {
                    "status": "success", 
                    "position": position_found, 
                    "navigation_verified": True,
                    "target_url": verified_url,
                    "details": f"Стр. {page_num}, Позиция #{position_found}{tail_str}"
                }

        # Next page button
        next_button = await page.query_selector("a.Pager-Item_type_next, a.pager__item_kind_next")
        if next_button:
            await next_button.click()
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await asyncio.sleep(random.uniform(2.0, 4.0))
        else:
            break

    return {"status": "not_found", "position": 0, "details": "Not found in searched pages"}

async def run_promotion_click(page, keyword_item, profile_name):
    """
    Executes a high-quality SEO behavioral factor click:
    1. Search for target keyword in Yandex with region parameter.
    2. Scan SERP (up to 5 pages) to locate target domain.
    3. If target domain is NOT found in top 50, and brand tail is configured:
       Automatically retry search with brand tail appended: «{keyword} {brand_tail}».
    4. Click target site, emulate human dwell time (40-90s) and internal clicks.
    """
    keyword = keyword_item["keyword"].strip()
    domain = keyword_item["domain"].lower().replace("www.", "")
    target_url = keyword_item.get("target_url") or ""
    region = str(keyword_item.get("geo_region") or "213")
    brand_tail = (keyword_item.get("brand_tail") or keyword_item.get("project_brand_tail") or "").strip()
    use_brand_tail = int(keyword_item.get("project_use_brand_tail", 1)) == 1

    logger.info(f"[{profile_name}] PROMO START: key='{keyword}', domain='{domain}', region={region}, brand_tail='{brand_tail}'")
    live_logger.add("PROMO", profile_name, f"Старт ПФ: запрос «{keyword}» для домена {domain} (регион LR: {region})")
    
    # 1. First attempt: search clean keyword in SERP (up to 5 pages = 50 results)
    res = await _search_and_click(page, search_query=keyword, region=region, domain=domain, profile_name=profile_name, max_pages=5, is_tail=False)
    
    if res["status"] == "success":
        return res
        
    if res["status"] in ["captcha", "error"]:
        return res
        
    # 2. If not found in Top 50, check if we have a brand tail configured
    if res["status"] == "not_found" and brand_tail and use_brand_tail:
        tail_query = f"{keyword} {brand_tail}"
        live_logger.add("WARN", profile_name, f"Сайт «{domain}» не в ТОП-50 по чистому запросу. Повторный поиск с брендовым хвостом: «{tail_query}»...")
        logger.info(f"[{profile_name}] Query '{keyword}' not in Top 50. Retrying with brand tail: '{tail_query}'")
        
        res_tail = await _search_and_click(page, search_query=tail_query, region=region, domain=domain, profile_name=profile_name, max_pages=3, is_tail=True)
        if res_tail["status"] == "success":
            res_tail["details"] = f"С брендовым хвостом «{brand_tail}» ({res_tail['details']})"
            return res_tail
        elif res_tail["status"] in ("captcha", "error"):
            # Preserve the actual failure: a blocked page is not a missing site.
            return res_tail
        else:
            live_logger.add("WARN", profile_name, f"Домен «{domain}» не найден даже с брендовым хвостом «{brand_tail}» в ТОП-30")
            return {"status": "not_found", "position": 0, "details": f"Не найден даже с хвостом '{brand_tail}'"}

    logger.warning(f"[{profile_name}] Domain '{domain}' not found in top 50 for '{keyword}'")
    live_logger.add("WARN", profile_name, f"Домен «{domain}» не найден в ТОП-50 по запросу «{keyword}»")
    return {"status": "not_found", "position": 0, "details": "Not in Top 50"}
