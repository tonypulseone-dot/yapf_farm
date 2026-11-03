import os
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"C:\Users\Administrator\AppData\Local\ms-playwright"

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

stealth_runner = Stealth()

from app.database import get_db, get_setting, set_setting
from app.engine.fingerprints import generate_fingerprint
from app.engine.proxy import parse_proxy_line, trigger_proxy_rotation
from app.engine.warmer import run_warming_cycle
from app.engine.promo import run_promotion_click
from app.engine.telegram import notify_click_success, notify_captcha, send_telegram_alert
from app.engine.logger import live_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("orchestrator")

BASE_PROFILES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "profiles_data")

class TaskOrchestrator:
    def __init__(self):
        self.is_running = False
        self.active_tasks = set()
        self.busy_profiles = set()  # Active profile IDs currently locked by worker threads
        self.stats = {
            "active_threads": 0,
            "warming_active": 0,
            "promo_active": 0,
            "clicks_today": 0,
            "last_log": "Сервис готов к запуску"
        }
        os.makedirs(BASE_PROFILES_DIR, exist_ok=True)

    def _create_new_profile_sync(self, conn, total_profiles, target_count):
        """Helper to create and persist a new profile with fingerprint and proxy."""
        c = conn.cursor()
        prof_name = f"prof_{total_profiles + 1}_{random.randint(1000, 9999)}"
        prof_dir = os.path.join(BASE_PROFILES_DIR, prof_name)
        os.makedirs(prof_dir, exist_ok=True)
        
        # 100% Android generation to build full mobile pool (up to 4000)
        dtype = "android"
        fp = generate_fingerprint(device_type=dtype)
        
        # Distribute evenly across 300 proxy ports
        proxy_lines = [p.strip() for p in get_setting("proxy_list", "").split("\n") if p.strip()]
        chosen_proxy = proxy_lines[total_profiles % len(proxy_lines)] if proxy_lines else None
        
        c.execute("""
            INSERT INTO profiles (name, user_data_dir, fingerprint, status, device_type, proxy, created_at)
            VALUES (?, ?, ?, 'new', ?, ?, CURRENT_TIMESTAMP)
        """, (prof_name, prof_dir, json.dumps(fp), dtype, chosen_proxy))
        conn.commit()
        
        c.execute("SELECT * FROM profiles WHERE name = ?", (prof_name,))
        new_prof = dict(c.fetchone())
        conn.close()
        
        port_label = chosen_proxy.split(':')[1] if chosen_proxy and ':' in chosen_proxy else 'N/A'
        live_logger.add("INFO", "orchestrator", f"База расширена: создан профиль [{prof_name}] (#{total_profiles + 1}/{target_count}, порт :{port_label})")
        return new_prof

    async def get_or_create_profile(self, target_status="ready"):
        """
        Intelligently selects a profile or expands the pool:
        1. Checks profiles ready for graduation to 'ready' (native SQLite datetime).
        2. If promo needed, returns oldest ready profile.
        3. If pool size is below target_profiles_count, smoothly introduces new profiles (~30% of worker tasks).
        4. For warming: enforces realistic human resting interval (cooldown) between warming cycles.
        5. If all current profiles are in cooldown and target pool size is not reached, creates a NEW profile.
        6. Automatically binds each profile to its own unique proxy from the pool.
        """
        conn = get_db()
        c = conn.cursor()
        
        # 1. Check profiles ready for graduation: Warming with age >= 18h and cycles >= 2
        min_age_h = str(get_setting("min_ready_age_hours", "18"))
        min_cycles = int(get_setting("warming_cycles_to_ready", "2"))
        c.execute("""
            UPDATE profiles 
            SET status = 'ready' 
            WHERE status = 'warming' AND cycles_completed >= ? AND created_at <= datetime('now', '-' || ? || ' hours')
        """, (min_cycles, min_age_h))
        conn.commit()

        # Build exclusion tuple for busy profiles
        busy_ids = list(self.busy_profiles)
        placeholders = ",".join("?" * len(busy_ids)) if busy_ids else "0"

        # 2. If we need Ready profile for promotion (prefer 80% Android / 20% Desktop)
        if target_status == "ready":
            preferred_dtype = "android" if random.random() < 0.80 else "desktop"
            query_preferred = f"""
                SELECT * FROM profiles 
                WHERE status = 'ready' AND device_type = ? AND id NOT IN ({placeholders})
                ORDER BY last_used_at ASC NULLS FIRST 
                LIMIT 1
            """
            c.execute(query_preferred, [preferred_dtype] + (busy_ids if busy_ids else []))
            row = c.fetchone()
            if not row:
                query_fallback = f"""
                    SELECT * FROM profiles 
                    WHERE status = 'ready' AND id NOT IN ({placeholders})
                    ORDER BY last_used_at ASC NULLS FIRST 
                    LIMIT 1
                """
                c.execute(query_fallback, busy_ids if busy_ids else ())
                row = c.fetchone()
            if row:
                conn.close()
                return dict(row)

        # 3. Target profile pool limit and resting cooldown (Alpine: 3-8 hours = 180-480 min random)
        c.execute("SELECT COUNT(*) as cnt FROM profiles")
        total_profiles = c.fetchone()["cnt"]
        target_count = int(get_setting("target_profiles_count", "4000"))
        
        # Случайная отлежка между циклами (3–8 часов, как в AlpineSoft)
        cooldown_min = str(random.randint(180, 480))

        # 4. If pool size is below target_count, smoothly introduce new profiles (30% of worker tasks)
        if total_profiles < target_count and random.random() < 0.30:
            return self._create_new_profile_sync(conn, total_profiles, target_count)

        # 5. Check for an unready profile whose cooldown has passed (status IN ('new', 'warming'))
        query = f"""
            SELECT * FROM profiles 
            WHERE status IN ('new', 'warming') 
              AND id NOT IN ({placeholders})
              AND (last_warmed_at IS NULL OR last_warmed_at <= datetime('now', '-' || ? || ' minutes'))
            ORDER BY cycles_completed ASC, last_warmed_at ASC NULLS FIRST 
            LIMIT 1
        """
        params = list(busy_ids) + [cooldown_min] if busy_ids else [cooldown_min]
        c.execute(query, params)
        row = c.fetchone()
        if row:
            conn.close()
            return dict(row)

        # 6. If no cooled-down unready profile exists AND total_profiles < target_count:
        if total_profiles < target_count:
            return self._create_new_profile_sync(conn, total_profiles, target_count)

        # 7. ПРИОРИТЕТНЫЙ ДОНАГУЛ 48 ЧАСОВ (Правило Alpine):
        # Если готовый Ready-профиль не использовался для продвижения более 48 часов,
        # он получает высший приоритет на догрев для сохранения свежести кук
        query_idle_48 = f"""
            SELECT * FROM profiles 
            WHERE status = 'ready' 
              AND id NOT IN ({placeholders})
              AND (
                  (last_used_at IS NOT NULL AND last_used_at <= datetime('now', '-48 hours'))
                  OR (last_used_at IS NULL AND last_warmed_at <= datetime('now', '-48 hours'))
              )
              AND (last_warmed_at IS NULL OR last_warmed_at <= datetime('now', '-' || ? || ' minutes'))
            ORDER BY last_warmed_at ASC NULLS FIRST
            LIMIT 1
        """
        c.execute(query_idle_48, params)
        row = c.fetchone()
        if row:
            conn.close()
            return dict(row)

        # 8. Maintenance Warming (Обычный догрев для готовой базы):
        query = f"""
            SELECT * FROM profiles 
            WHERE id NOT IN ({placeholders})
              AND (last_warmed_at IS NULL OR last_warmed_at <= datetime('now', '-' || ? || ' minutes'))
            ORDER BY last_warmed_at ASC NULLS FIRST, cycles_completed ASC
            LIMIT 1
        """
        c.execute(query, params)
        row = c.fetchone()
        if row:
            conn.close()
            return dict(row)

        # 9. Fallback: если все профили на отлежке, берем профиль с самой давней сессией нагула
        query = f"""
            SELECT * FROM profiles 
            WHERE id NOT IN ({placeholders})
            ORDER BY last_warmed_at ASC NULLS FIRST 
            LIMIT 1
        """
        c.execute(query, busy_ids if busy_ids else ())
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_pending_keyword(self):
        """Fetch next keyword that needs clicks today."""
        conn = get_db()
        c = conn.cursor()
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        c.execute("""
            UPDATE keywords 
            SET clicks_today = 0, last_reset_date = ? 
            WHERE last_reset_date != ?
        """, (today_str, today_str))
        conn.commit()
        
        c.execute("""
            SELECT k.*, p.domain, p.name as project_name,
                   p.brand_tail as project_brand_tail,
                   p.use_brand_tail as project_use_brand_tail,
                   p.default_region as project_default_region
            FROM keywords k
            JOIN projects p ON k.project_id = p.id
            WHERE k.is_active = 1 AND p.is_active = 1 AND k.clicks_today < k.daily_goal
            ORDER BY k.last_click_at ASC NULLS FIRST
            LIMIT 1
        """)
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None

    async def execute_worker_task(self, stagger_delay: float = 0.0):
        if stagger_delay > 0:
            await asyncio.sleep(stagger_delay)

        profile_id = None
        thread_counted = False
        try:
            if get_setting("is_running", "0") != "1":
                return

            # 1. Decide task: Promotion or Warming?
            keyword_task = self.get_pending_keyword()
            profile = None
            task_type = "warming"
            
            if keyword_task:
                # Try to get a free Ready profile
                profile = await self.get_or_create_profile(target_status="ready")
                if profile and profile["status"] == "ready":
                    task_type = "promo"
                else:
                    profile = await self.get_or_create_profile(target_status="warming")
                    task_type = "warming"
            else:
                profile = await self.get_or_create_profile(target_status="warming")
                task_type = "warming"

            if not profile:
                await asyncio.sleep(2)
                return

            profile_id = profile["id"]
            self.busy_profiles.add(profile_id)
            self.stats["active_threads"] = self.stats.get("active_threads", 0) + 1
            thread_counted = True

            # 2. Setup browser with fingerprint & proxy
            fp = json.loads(profile["fingerprint"])
            proxy_lines = [p.strip() for p in get_setting("proxy_list", "").split("\n") if p.strip()]
            proxy_config = None
            rotate_url = None
            proxy_scheme = get_setting("proxy_type", "http")
            bind_to_profile = str(get_setting("proxy_bind_to_profile", "1")).lower() in ["1", "true"]
            
            chosen_proxy_label = "Direct"
            if proxy_lines:
                chosen_line = None
                if bind_to_profile and profile.get("proxy") and profile["proxy"] in proxy_lines:
                    chosen_line = profile["proxy"]
                if not chosen_line:
                    # Назначаем прокси из активного пула равномерно (round-robin по ID профиля)
                    chosen_line = proxy_lines[profile["id"] % len(proxy_lines)]
                    if bind_to_profile and profile.get("id"):
                        try:
                            conn = get_db()
                            conn.execute("UPDATE profiles SET proxy = ? WHERE id = ?", (chosen_line, profile["id"]))
                            conn.commit()
                            conn.close()
                        except Exception as pe:
                            logger.warning(f"Failed to bind proxy: {pe}")
                            
                proxy_config, rotate_url = parse_proxy_line(chosen_line, default_scheme=proxy_scheme)
                chosen_proxy_label = f"{chosen_line.split(':')[0]}:{chosen_line.split(':')[1]}"
                if rotate_url:
                    await trigger_proxy_rotation(rotate_url)

            live_logger.add("INFO", profile["name"], f"Выделен поток ({task_type.upper()}), прокси: {chosen_proxy_label}")

            # 3. Launch isolated browser instance
            async with async_playwright() as p:
                launch_args = [
                    "--disable-blink-features=AutomationControlled",
                    f"--window-size={fp['viewport']['width']},{fp['viewport']['height']}",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--mute-audio"
                ]
                
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=profile["user_data_dir"],
                    headless=True,
                    args=launch_args,
                    viewport=fp["viewport"],
                    user_agent=fp["user_agent"],
                    proxy=proxy_config,
                    locale="ru-RU",
                    timezone_id="Europe/Moscow"
                )
                
                page = context.pages[0] if context.pages else await context.new_page()
                await stealth_runner.apply_stealth_async(page)
                
                if task_type == "promo":
                    self.stats["promo_active"] += 1
                    try:
                        self.stats["last_log"] = f"[{profile['name']}] Клики ПФ: {keyword_task['domain']} -> '{keyword_task['keyword']}'"
                        res = await run_promotion_click(page, keyword_task, profile["name"])
                        
                        conn = get_db()
                        c = conn.cursor()
                        c.execute("""
                            INSERT INTO click_logs (keyword, target_url, domain, profile_name, device, region, position_found, status, details)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (keyword_task["keyword"], keyword_task["target_url"] or "", keyword_task["domain"],
                              profile["name"], profile["device_type"], keyword_task.get("geo_region", "213"),
                              res["position"], res["status"], res["details"]))
                        
                        if res["status"] == "success":
                            c.execute("UPDATE keywords SET clicks_today = clicks_today + 1, last_click_at = CURRENT_TIMESTAMP WHERE id = ?", (keyword_task["id"],))
                            c.execute("UPDATE profiles SET total_clicks = total_clicks + 1, last_used_at = CURRENT_TIMESTAMP, status = 'warming' WHERE id = ?", (profile["id"],))
                            asyncio.create_task(notify_click_success(
                                domain=keyword_task["domain"],
                                keyword=keyword_task["keyword"],
                                position=res.get("position", 0),
                                profile_name=profile["name"],
                                device=profile.get("device_type", "desktop")
                            ))
                        elif res["status"] == "captcha":
                            asyncio.create_task(notify_captcha(
                                keyword=keyword_task["keyword"],
                                domain=keyword_task["domain"],
                                solved=False
                            ))
                        conn.commit()
                        conn.close()
                    finally:
                        self.stats["promo_active"] = max(0, self.stats["promo_active"] - 1)

                else:
                    self.stats["warming_active"] += 1
                    try:
                        self.stats["last_log"] = f"[{profile['name']}] Нагул профиля (цикл #{profile['cycles_completed'] + 1})"
                        ok = await run_warming_cycle(page, profile["id"], profile["name"])
                        
                        conn = get_db()
                        c = conn.cursor()
                        # Сохраняем статус 'ready' для готовых профилей при догреве
                        new_status = 'ready' if profile.get('status') == 'ready' else 'warming'
                        if ok:
                            c.execute("""
                                UPDATE profiles 
                                SET cycles_completed = cycles_completed + 1, 
                                    last_warmed_at = CURRENT_TIMESTAMP, 
                                    status = ?
                                WHERE id = ?
                            """, (new_status, profile["id"]))
                        else:
                            c.execute("""
                                UPDATE profiles 
                                SET last_warmed_at = CURRENT_TIMESTAMP, 
                                    status = ?
                                WHERE id = ?
                            """, (new_status, profile["id"]))
                        conn.commit()
                        conn.close()
                    finally:
                        self.stats["warming_active"] = max(0, self.stats["warming_active"] - 1)

                try:
                    await context.close()
                except Exception:
                    pass
                live_logger.add("INFO", profile["name"], f"Браузерная сессия закрыта, профиль сохранен (цикл завершен)")

        except Exception as e:
            logger.error(f"Worker task error: {e}")
            err_str = str(e)[:120]
            self.stats["last_log"] = f"Ошибка потока: {err_str}"
            live_logger.add("ERROR", profile["name"] if profile else "worker", f"Ошибка потока: {err_str}")
        finally:
            if profile_id is not None:
                self.busy_profiles.discard(profile_id)
            if thread_counted:
                self.stats["active_threads"] = max(0, self.stats.get("active_threads", 1) - 1)

    async def _safe_worker_wrapper(self, stagger_delay: float, timeout_sec: float):
        try:
            await asyncio.wait_for(self.execute_worker_task(stagger_delay=stagger_delay), timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.warning(f"Worker task timed out after {int(timeout_sec)}s, thread released")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Worker task uncaught error: {e}")

    async def main_loop(self):
        """Continuous orchestrator loop with responsive worker launches."""
        logger.info("YAPF_Farm Orchestrator loop started.")
        live_logger.add("INFO", "orchestrator", "Диспетчер потоков инициализирован и готов к распределению задач")
        while True:
            try:
                is_run = (get_setting("is_running", "0") == "1")
                current_max = int(get_setting("max_threads", "10"))
                self.stats["max_threads"] = current_max
                
                if not is_run:
                    self.stats["active_threads"] = 0
                    self.stats["warming_active"] = 0
                    self.stats["promo_active"] = 0
                    self.busy_profiles.clear()
                    await asyncio.sleep(2)
                    continue

                # Clean completed tasks
                self.active_tasks = {t for t in self.active_tasks if not t.done()}

                slots = current_max - len(self.active_tasks)
                if slots > 0:
                    launch_interval = float(get_setting("thread_launch_interval_sec", "1.5"))
                    timeout_sec = float(get_setting("thread_max_lifetime_min", "20")) * 60.0
                    for i in range(slots):
                        stagger = i * launch_interval
                        t = asyncio.create_task(self._safe_worker_wrapper(stagger_delay=stagger, timeout_sec=timeout_sec))
                        self.active_tasks.add(t)

                await asyncio.sleep(1.0)

            except Exception as e:
                logger.error(f"Main loop exception: {e}")
                await asyncio.sleep(3)

orchestrator = TaskOrchestrator()
