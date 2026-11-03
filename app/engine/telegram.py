import asyncio
import logging
import urllib.parse
import urllib.request
import json
import ssl
from datetime import datetime
from app.database import (
    get_setting, set_setting, get_db,
    register_telegram_user, get_telegram_chat_id_by_username, get_latest_telegram_user
)

logger = logging.getLogger("telegram_bot")
ssl_ctx = ssl._create_unverified_context()

DEFAULT_BOT_TOKEN = "9065799178:AAFU7Tbi0QIo2BRa8wf4t77o2APD0AJ2_EA"

def get_active_bot_token() -> str:
    token = get_setting("telegram_bot_token", "").strip()
    return token if token else DEFAULT_BOT_TOKEN

async def resolve_target_chat_id() -> str:
    """
    Resolves the recipient chat_id:
    1. Direct numeric chat_id if specified in settings.
    2. Lookup chat_id by telegram_username from telegram_users table.
    3. Fallback to latest interacted user in telegram_users table.
    """
    configured_id = get_setting("telegram_chat_id", "").strip()
    if configured_id and (configured_id.isdigit() or configured_id.startswith("-")):
        return configured_id

    username = get_setting("telegram_username", "").strip()
    if username:
        matched_id = get_telegram_chat_id_by_username(username)
        if matched_id:
            return str(matched_id)

    latest = get_latest_telegram_user()
    if latest and latest.get("chat_id"):
        return str(latest["chat_id"])

    return ""

async def send_telegram_alert(text: str, custom_chat_id: str = None, reply_markup: dict = None) -> dict:
    """
    Sends an alert message to configured Telegram bot chat or specified username.
    """
    notifications_enabled = str(get_setting("telegram_notifications_enabled", "1")).strip().lower()
    if notifications_enabled in ["0", "false"] and not custom_chat_id:
        return {"status": "disabled", "message": "Notifications disabled in settings"}

    token = get_active_bot_token()
    if not token:
        return {"status": "skipped", "message": "Telegram token not configured"}

    target_chat = custom_chat_id if custom_chat_id else await resolve_target_chat_id()
    if not target_chat:
        return {
            "status": "waiting_start",
            "message": "Пользователь не найден. Пожалуйста, откройте @YAPF_Farm_bot и нажмите /start"
        }

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10.0, context=ssl_ctx))
        res_body = response.read().decode("utf-8")
        res_json = json.loads(res_body)
        if res_json.get("ok"):
            return {"status": "success", "response": res_json}
        else:
            return {"status": "error", "error": res_json.get("description", "Unknown error")}
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
        return {"status": "error", "error": str(e)}

async def send_daily_summary(stats: dict = None, custom_chat_id: str = None):
    """
    Sends structured daily summary notification to Telegram with live thread & server telemetry.
    """
    if stats is None:
        stats = {}

    # Merge with live metrics from orchestrator if available
    try:
        from app.engine.orchestrator import orchestrator
        orch_stats = orchestrator.stats
    except Exception:
        orch_stats = {}

    try:
        import psutil
        cpu_load = psutil.cpu_percent(interval=None)
        ram_load = psutil.virtual_memory().percent
    except Exception:
        cpu_load = stats.get("cpu_load", 0)
        ram_load = stats.get("ram_load", 0)

    active_th = orch_stats.get("active_threads", stats.get("active_threads", 0))
    max_th = orch_stats.get("max_threads", int(get_setting("max_threads", "25")))
    warming_th = orch_stats.get("warming_active", stats.get("warming_active", 0))
    promo_th = orch_stats.get("promo_active", stats.get("promo_active", 0))

    # Profiles & Clicks from DB if not provided
    if "profiles_total" not in stats:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT status, COUNT(*) as cnt FROM profiles GROUP BY status")
        prof_rows = c.fetchall()
        prof_stats = {r["status"]: r["cnt"] for r in prof_rows}
        c.execute("SELECT COUNT(*) as cnt FROM click_logs WHERE DATE(timestamp) = DATE('now') AND status = 'success'")
        clicks_today = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) as cnt FROM projects WHERE is_active = 1")
        proj_cnt = c.fetchone()["cnt"]
        conn.close()

        profiles_total = sum(prof_stats.values())
        profiles_ready = prof_stats.get("ready", 0)
        profiles_warming = prof_stats.get("warming", 0)
        profiles_new = prof_stats.get("new", 0)
    else:
        clicks_today = stats.get("clicks_today", 0)
        profiles_total = stats.get("profiles_total", 0)
        profiles_ready = stats.get("profiles_ready", 0)
        profiles_warming = stats.get("profiles_warming", 0)
        profiles_new = stats.get("profiles_new", 0)
        proj_cnt = stats.get("projects_count", 0)

    is_run = (get_setting("is_running", "0") == "1")
    state_str = "🟢 В РАБОТЕ" if is_run else "⏸ ОСТАНОВЛЕН"

    msg = (
        f"📊 <b>YAPF_Farm: Суточный отчёт системы</b>\n\n"
        f"⚡ <b>Состояние фермы:</b> {state_str}\n"
        f"🔥 <b>Браузеров в работе:</b> <b>{active_th} / {max_th}</b>\n"
        f"   • Нагул профилей: <b>{warming_th}</b>\n"
        f"   • Продвижение (ПФ): <b>{promo_th}</b>\n\n"
        f"🎯 <b>Целевых кликов сегодня:</b> <b>{clicks_today}</b>\n\n"
        f"🤖 <b>База профилей ({profiles_total} шт):</b>\n"
        f"   • Готовые к поиску (Ready): <b>{profiles_ready}</b>\n"
        f"   • В нагуле (Warming): <b>{profiles_warming}</b>\n"
        f"   • Новые (New): <b>{profiles_new}</b>\n\n"
        f"📁 Активных проектов: <b>{proj_cnt}</b>\n"
        f"💻 Загрузка сервера: <b>CPU {cpu_load}% / RAM {ram_load}%</b>\n"
        f"🌐 Панель управления: <a href='http://pffarm.ru:8080'>http://pffarm.ru:8080</a>"
    )
    return await send_telegram_alert(msg, custom_chat_id=custom_chat_id)

async def notify_click_success(domain: str, keyword: str, position: int, profile_name: str, device: str):
    notify_clicks = str(get_setting("telegram_notify_clicks", "1")).lower()
    if notify_clicks in ["0", "false"]:
        return
    
    pos_text = f"#{position}" if position > 0 else "Прямой переход"
    msg = (
        f"🎯 <b>Целевой клик выполнен!</b>\n\n"
        f"🌐 Домен: <b>{domain}</b>\n"
        f"🔑 Ключ: <i>{keyword}</i>\n"
        f"📍 Позиция в выдаче: <b>{pos_text}</b>\n"
        f"👤 Профиль: <code>{profile_name}</code> ({device})\n"
        f"🕒 Время: {datetime.now().strftime('%H:%M:%S')}"
    )
    return await send_telegram_alert(msg)

async def notify_captcha(keyword: str, domain: str, solved: bool, service: str = ""):
    notify_c = str(get_setting("telegram_notify_captcha", "1")).lower()
    if notify_c in ["0", "false"]:
        return

    status_icon = f"✅ Решена успешно ({service})" if solved else "⚠️ Капча не решена / ротация IP"
    msg = (
        f"🛡 <b>Яндекс Капча</b>\n\n"
        f"🔑 Запрос: <i>{keyword}</i>\n"
        f"🌐 Домен: <b>{domain}</b>\n"
        f"Результат: <b>{status_icon}</b>"
    )
    return await send_telegram_alert(msg)

async def telegram_bot_polling_loop():
    """
    Background worker that receives commands and /start messages from users in @YAPF_Farm_bot.
    Automatically links usernames to chat_ids.
    """
    logger.info("Telegram Bot Poller started for @YAPF_Farm_bot")
    offset = 0

    while True:
        try:
            token = get_active_bot_token()
            if not token:
                await asyncio.sleep(5)
                continue

            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=15"
            req = urllib.request.Request(url)
            
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=20.0, context=ssl_ctx))
            data = json.loads(resp.read().decode("utf-8"))

            if data.get("ok"):
                for upd in data.get("result", []):
                    offset = max(offset, upd["update_id"] + 1)
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg or "from" not in msg:
                        continue

                    chat_id = msg["chat"]["id"]
                    user = msg["from"]
                    username = user.get("username", "")
                    first_name = user.get("first_name", "")
                    text = (msg.get("text") or "").strip()

                    # Save user in DB
                    register_telegram_user(chat_id, username, first_name)

                    # Auto-bind if user set their username in dashboard or if setting is empty
                    current_setting_user = get_setting("telegram_username", "").strip().lstrip("@").lower()
                    if not current_setting_user or (username and current_setting_user == username.lower()):
                        set_setting("telegram_chat_id", str(chat_id))
                        if username:
                            set_setting("telegram_username", username)

                    # Interactive command keyboard for Telegram
                    MAIN_REPLY_KEYBOARD = {
                        "keyboard": [
                            [{"text": "📊 Статус"}, {"text": "📈 Отчёт"}],
                            [{"text": "🟢 Запустить ферму"}, {"text": "⏸ Остановить ферму"}]
                        ],
                        "resize_keyboard": True
                    }

                    cmd = text.strip()
                    cmd_lower = cmd.lower()

                    # 1. Start Farm Command (priority over /start)
                    if cmd_lower in [
                        "/start_farm", "/startfarm", "старт фарм", "старт ферму", 
                        "🟢 запустить ферму", "запустить ферму", "запуск"
                    ]:
                        set_setting("is_running", "1")
                        max_th = get_setting("max_threads", "25")
                        live_logger.add("SUCCESS", "orchestrator", f"Служба запущена пользователем через Telegram (Потоков: {max_th})")
                        await send_telegram_alert(
                            f"🟢 <b>Служба YAPF_Farm запущена!</b>\n\n"
                            f"⚡ Браузерных потоков: <b>{max_th}</b>\n"
                            f"🤖 Профилей в базе: <b>4000+</b>\n"
                            f"🌐 Панель управления: <a href='http://pffarm.ru:8080'>http://pffarm.ru:8080</a>",
                            custom_chat_id=str(chat_id),
                            reply_markup=MAIN_REPLY_KEYBOARD
                        )

                    # 2. Stop Farm Command
                    elif cmd_lower in [
                        "/stop_farm", "/stopfarm", "стоп фарм", "стоп ферму", 
                        "⏸ остановить ферму", "остановить ферму", "стоп", "пауза"
                    ]:
                        set_setting("is_running", "0")
                        live_logger.add("WARN", "orchestrator", "Служба остановлена пользователем через Telegram")
                        await send_telegram_alert(
                            "⏸ <b>Служба YAPF_Farm остановлена</b>\n\n"
                            "Все активные потоки плавно завершают текущие сессии.",
                            custom_chat_id=str(chat_id),
                            reply_markup=MAIN_REPLY_KEYBOARD
                        )

                    # 3. Status Command
                    elif cmd_lower in ["/status", "статус", "📊 статус"]:
                        is_run = (get_setting("is_running", "0") == "1")
                        state_str = "🟢 В РАБОТЕ" if is_run else "⏸ ОСТАНОВЛЕН"

                        try:
                            from app.engine.orchestrator import orchestrator
                            orch = orchestrator.stats
                        except Exception:
                            orch = {}

                        active_th = orch.get("active_threads", 0)
                        max_th = orch.get("max_threads", int(get_setting("max_threads", "25")))
                        warming_th = orch.get("warming_active", 0)
                        promo_th = orch.get("promo_active", 0)

                        try:
                            import psutil
                            cpu = psutil.cpu_percent(interval=None)
                            ram = psutil.virtual_memory().percent
                        except Exception:
                            cpu, ram = 0, 0

                        conn = get_db()
                        c = conn.cursor()
                        c.execute("SELECT COUNT(*) as cnt FROM profiles")
                        tot_prof = c.fetchone()["cnt"]
                        c.execute("SELECT COUNT(*) as cnt FROM click_logs WHERE DATE(timestamp) = DATE('now') AND status = 'success'")
                        clicks_tod = c.fetchone()["cnt"]
                        conn.close()

                        status_msg = (
                            f"ℹ️ <b>Статус YAPF_Farm: {state_str}</b>\n\n"
                            f"⚡ <b>Браузерные потоки:</b> <b>{active_th} / {max_th}</b>\n"
                            f"   • Нагул профилей: <b>{warming_th}</b>\n"
                            f"   • Продвижение (ПФ): <b>{promo_th}</b>\n\n"
                            f"🤖 Всего профилей в базе: <b>{tot_prof}</b>\n"
                            f"🎯 Целевых кликов сегодня: <b>{clicks_tod}</b>\n"
                            f"💻 Сервер: <b>CPU {cpu}% | RAM {ram}%</b>\n"
                            f"🌐 Панель: <a href='http://pffarm.ru:8080'>http://pffarm.ru:8080</a>"
                        )
                        await send_telegram_alert(status_msg, custom_chat_id=str(chat_id), reply_markup=MAIN_REPLY_KEYBOARD)

                    # 4. Report Command
                    elif cmd_lower in ["/report", "отчет", "отчёт", "📈 отчет", "📈 отчёт", "📈 суточный отчёт"]:
                        await send_daily_summary(custom_chat_id=str(chat_id))

                    # 5. Welcome Command (ONLY exact /start or with parameters)
                    elif cmd == "/start" or cmd.startswith("/start "):
                        user_display = f"@{username}" if username else first_name
                        welcome_text = (
                            f"👋 <b>Добро пожаловать в бота управления YAPF_Farm!</b>\n\n"
                            f"Ваш Telegram логин: <b>{user_display}</b>\n"
                            f"Ваш Chat ID: <code>{chat_id}</code>\n\n"
                            f"✅ <b>Аккаунт успешно привязан!</b> Сюда приходят алерты и суточные отчёты.\n\n"
                            f"Вы можете управлять фермой с помощью кнопок клавиатуры ниже:"
                        )
                        await send_telegram_alert(welcome_text, custom_chat_id=str(chat_id), reply_markup=MAIN_REPLY_KEYBOARD)

            # Automated scheduled daily report at telegram_daily_report_hour
            now = datetime.now()
            try:
                target_hour = int(get_setting("telegram_daily_report_hour", "21"))
                today_str = now.strftime("%Y-%m-%d")
                last_sent_date = get_setting("telegram_last_report_date", "")
                if now.hour >= target_hour and last_sent_date != today_str:
                    set_setting("telegram_last_report_date", today_str)
                    await send_daily_summary()
            except Exception as sch_err:
                logger.debug(f"Daily report schedule check: {sch_err}")

            await asyncio.sleep(1.0)
        except Exception as e:
            logger.warning(f"Error in telegram poller: {e}")
            await asyncio.sleep(4.0)
