import asyncio
import logging
import random
import re
import uuid
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
from app.engine.promo import target_host_matches
from app.regions import parse_regions
import json

logger = logging.getLogger("position_checker")

class PositionCheckerManager:
    def __init__(self):
        self.jobs = {}
        self._tasks = {}
        self._lock = None
        self._browser_slots = None

    def _project_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _slots(self):
        if self._browser_slots is None:
            self._browser_slots = asyncio.Semaphore(2)
        return self._browser_slots

    @property
    def is_checking(self):
        return any(j['is_running'] for j in self.jobs.values())

    def get_progress(self, project_id=None):
        jobs = [dict(j) for j in self.jobs.values()]
        job = self.jobs.get(project_id) if project_id else next((j for j in jobs if j['is_running']), None)
        result = dict(job) if job else {'is_running': False, 'project_id': project_id,
            'current': 0, 'total': 0, 'percent': 0, 'errors': 0, 'last_log': 'Проверка не запущена'}
        return {**result, 'jobs': jobs, 'active_jobs': sum(j['is_running'] for j in jobs), 'browser_limit': 2}

    async def check_single_keyword(self, keyword_row, project_row, region=None, device_type='desktop', max_pages=10, fresh=True):
        async with self._slots():
            return await asyncio.wait_for(self._check_single_keyword_impl(
                keyword_row, project_row, region, device_type, max_pages, fresh), timeout=300)

    async def _check_single_keyword_impl(self, keyword_row: dict, project_row: dict, region: str = None, device_type: str = "desktop", max_pages: int = 10, fresh: bool = True) -> dict:
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
                timezone_id="Europe/Moscow",
                extra_http_headers={"Cache-Control":"no-cache", "Pragma":"no-cache"} if fresh else {}
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
                        return {"status":"error", "position":None, "details":"Ошибка загрузки поисковой выдачи"}

                    # Check SmartCaptcha
                    if "showcaptcha" in page.url or await page.query_selector(".CheckboxCaptcha, #smartcaptcha-anchor"):
                        solved = await handle_yandex_checkbox_captcha(page)
                        if not solved:
                            logger.warning("SmartCaptcha could not be solved during position check.")
                            return {"status":"captcha", "position":None, "details":"Проверка остановлена CAPTCHA"}
                        await asyncio.sleep(2.0)
                        if "showcaptcha" in page.url or await page.query_selector(".CheckboxCaptcha, #smartcaptcha-anchor"):
                            return {"status":"captcha", "position":None, "details":"CAPTCHA не пройдена"}

                    # Extract organic results
                    # Exclude ads and direct
                    items = await page.query_selector_all("li.serp-item, div.serp-item")
                    if not items:
                        return {"status":"error", "position":None, "details":"Нет распознанных результатов выдачи"}
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
                            if target_host_matches(href, domain):
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
                "status": "ok",
                "keyword_id": keyword_row["id"],
                "keyword": keyword,
                "position": found_pos,
                "target_url": found_url,
                "region": geo,
                "device_type": device_type
            }

    async def start_project_check(self, project_id, region=None, regions=None, device_type='desktop', fresh=True):
        if device_type not in ('desktop', 'android'):
            return {'status':'error','message':'Неизвестный тип устройства'}
        async with self._project_lock():
            if self.jobs.get(project_id, {}).get('is_running'):
                return {'status':'busy','message':'Этот проект уже проверяется','project_id':project_id}
            if sum(j['is_running'] for j in self.jobs.values()) >= 20:
                return {'status':'busy','message':'Уже запущено 20 заданий'}
            conn=get_db()
            try:
                row=conn.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
                if not row:
                    return {'status':'error','message':'Проект не найден'}
                project=dict(row)
                keywords=[dict(r) for r in conn.execute('SELECT * FROM keywords WHERE project_id=? AND is_active=1 ORDER BY id',(project_id,))]
            finally:
                conn.close()
            if not keywords:
                return {'status':'error','message':'В проекте нет активных запросов'}
            selected = regions or (str(region).split(',') if region else sorted({k.get('geo_region') or project.get('default_region') or '213' for k in keywords}))
            targets=parse_regions(json.dumps(selected))
            # Duplicate query rows from multi-region imports must not multiply checks.
            grouped={}
            for kw in keywords:
                grouped.setdefault(kw['keyword'].strip(), []).append(kw)
            plan=[]
            for reg in targets:
                for candidates in grouped.values():
                    kw=next((k for k in candidates if str(k.get('geo_region'))==reg), candidates[0])
                    plan.append((kw,reg))
            job={'run_id':uuid.uuid4().hex,'project_id':project_id,'project_name':project['name'],
                 'is_running':True,'status':'queued','current':0,'total':len(plan),'percent':0,
                 'errors':0,'regions':targets,'device_type':device_type,
                 'last_log':'Задание создано','started_at':datetime.utcnow().isoformat(), 'finished_at':None}
            self.jobs[project_id]=job
            task=asyncio.create_task(self._run_project(job,project,plan,device_type,fresh))
            self._tasks[project_id]=task
            # Keep completed progress, but bound the in-memory history.
            for pid in list(self.jobs):
                if len(self.jobs)<=50: break
                if not self.jobs[pid]['is_running'] and pid!=project_id:
                    self.jobs.pop(pid);self._tasks.pop(pid,None)
            return {'status':'ok','message':'Проверка добавлена','project_id':project_id,'run_id':job['run_id']}

    async def _run_project(self, job, project, plan, device_type, fresh):
        live_logger.add('INFO','checker',f"Начат съём проекта «{project['name']}»: {len(plan)} замеров")
        try:
            job['status']='running'
            for kw,reg in plan:
                job['last_log']=f"LR {reg}: {kw['keyword']}"
                try:
                    result=await self.check_single_keyword(kw,project,reg,device_type,fresh=fresh)
                    if result.get('status','ok')!='ok':
                        job['errors']+=1
                        job['last_log']=result.get('details','Проверка не выполнена')
                        live_logger.add('WARN','checker',job['last_log'])
                except Exception as error:
                    job['errors']+=1
                    job['last_log']='Ошибка замера: '+type(error).__name__
                    logger.warning('Position check failed for project %s: %s',project['id'],type(error).__name__)
                job['current']+=1
                job['percent']=round(job['current']/job['total']*100,1)
                if job['current']<job['total']:
                    await asyncio.sleep(random.uniform(2,4))
            job['status']='completed_with_errors' if job['errors'] else 'completed'
            job['last_log']=f"Завершено: {job['current']}, ошибок: {job['errors']}"
        except asyncio.CancelledError:
            job['status']='interrupted';job['last_log']='Проверка прервана'
            raise
        finally:
            job['is_running']=False
            job['finished_at']=datetime.utcnow().isoformat()

    async def check_project_positions(self, project_id, region=None, regions=None, device_type='desktop', fresh=True):
        result=await self.start_project_check(project_id,region,regions,device_type,fresh)
        if result['status']=='ok':
            await self._tasks[project_id]
        return result

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

                    await asyncio.gather(*(self.check_project_positions(pid) for pid in proj_ids))

                    from app.database import set_setting
                    set_setting("position_last_auto_check_date", today_str)

            except Exception as e:
                logger.error(f"Error in position checker scheduler: {e}")

            await asyncio.sleep(60)

position_checker = PositionCheckerManager()
