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

async def send_telegram_alert(text: str, custom_chat_id: str = None) -> dict:
    """
    Sends an alert message to configured Telegram bot chat or specified username.
    """
    notifications_enabled = get_setting("telegram_notifications_enabled", "1").strip()
    if notifications_enabled == "0" and not custom_chat_id:
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

async def send_daily_summary(stats: dict):
    """
    Sends structured daily summary notification to Telegram.
    """
    msg = (
        "📊 <b>YAPF_Farm: Суточный отчёт системы</b>\n\n"
        f"🎯 Целевых кликов сегодня: <b>{stats.get('clicks_today', 0)}</b>\n"
        f"🤖 Профилей в базе: <b>{stats.get('profiles_total', 0)}</b>\n"
        f"   • Готовые к поиску (Ready): <b>{stats.get('profiles_ready', 0)}</b>\n"
        f"   • На нагуле (Warming): <b>{stats.get('profiles_warming', 0)}</b>\n"
        f"   • Новые (New): <b>{stats.get('profiles_new', 0)}</b>\n\n"
        f"📁 Активных проектов: <b>{stats.get('projects_count', 0)}</b>\n"
        f"⚡ Загрузка сервера: <b>CPU {stats.get('cpu_load', 0)}% / RAM {stats.get('ram_load', 0)}%</b>\n"
        f"🌐 Панель управления: <a href='http://pffarm.ru'>http://pffarm.ru</a>"
    )
    return await send_telegram_alert(msg)

async def notify_click_success(domain: str, keyword: str, position: int, profile_name: str, device: str):
    notify_clicks = get_setting("telegram_notify_clicks", "1")
    if notify_clicks == "0":
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
    notify_c = get_setting("telegram_notify_captcha", "1")
    if notify_c == "0":
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

                    # Command handler
                    if text.startswith("/start"):
                        user_display = f"@{username}" if username else first_name
                        welcome_text = (
                            f"👋 <b>Добро пожаловать в бота управления YAPF_Farm!</b>\n\n"
                            f"Ваш Telegram логин: <b>{user_display}</b>\n"
                            f"Ваш Chat ID: <code>{chat_id}</code>\n\n"
                            f"✅ <b>Аккаунт успешно привязан!</b> Сюда будут приходить суточные отчёты и алерты фермы.\n\n"
                            f"<b>Доступные команды:</b>\n"
                            f"📊 /status — Текущее состояние серверов и потоков\n"
                            f"📈 /report — Запросить суточный отчёт сейчас\n"
                            f"🟢 /start_farm — Запустить службу фермы\n"
                            f"⏸ /stop_farm — Приостановить службу фермы"
                        )
                        await send_telegram_alert(welcome_text, custom_chat_id=str(chat_id))

                    elif text == "/status":
                        is_run = (get_setting("is_running", "0") == "1")
                        state_str = "🟢 В РАБОТЕ" if is_run else "⏸ ОСТАНОВЛЕН"
                        max_th = get_setting("max_threads", "25")
                        status_msg = (
                            f"ℹ️ <b>Статус YAPF_Farm</b>: {state_str}\n"
                            f"⚡ Потоков: <b>{max_th}</b>\n"
                            f"🌐 Домен: <b>pffarm.ru</b>"
                        )
                        await send_telegram_alert(status_msg, custom_chat_id=str(chat_id))

                    elif text == "/report":
                        # Fetch current counts
                        conn = get_db()
                        c = conn.cursor()
                        c.execute("SELECT COUNT(*) as cnt FROM click_logs WHERE DATE(timestamp) = DATE('now') AND status = 'success'")
                        clicks = c.fetchone()["cnt"]
                        c.execute("SELECT status, COUNT(*) as cnt FROM profiles GROUP BY status")
                        prof_rows = c.fetchall()
                        prof_stats = {r["status"]: r["cnt"] for r in prof_rows}
                        c.execute("SELECT COUNT(*) as cnt FROM projects WHERE is_active = 1")
                        proj_cnt = c.fetchone()["cnt"]
                        conn.close()

                        report_data = {
                            "clicks_today": clicks,
                            "profiles_total": sum(prof_stats.values()),
                            "profiles_ready": prof_stats.get("ready", 0),
                            "profiles_warming": prof_stats.get("warming", 0),
                            "profiles_new": prof_stats.get("new", 0),
                            "projects_count": proj_cnt,
                            "cpu_load": 0,
                            "ram_load": 0
                        }
                        await send_daily_summary(report_data)

                    elif text == "/start_farm":
                        set_setting("is_running", "1")
                        await send_telegram_alert("🟢 <b>Служба YAPF_Farm запущена через Telegram</b>", custom_chat_id=str(chat_id))

                    elif text == "/stop_farm":
                        set_setting("is_running", "0")
                        await send_telegram_alert("⏸ <b>Служба YAPF_Farm остановлена через Telegram</b>", custom_chat_id=str(chat_id))

            await asyncio.sleep(1.0)
        except Exception as e:
            logger.warning(f"Error in telegram poller: {e}")
            await asyncio.sleep(4.0)
