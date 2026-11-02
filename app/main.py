import os
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"C:\Users\Administrator\AppData\Local\ms-playwright"
import asyncio
import os
import psutil
import time
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import (
    init_db, get_db, get_setting, set_setting, get_all_settings
)
from app.engine.orchestrator import orchestrator
from app.engine.logger import live_logger
from app.engine.telegram import (
    send_telegram_alert, send_daily_summary, telegram_bot_polling_loop, resolve_target_chat_id
)

app = FastAPI(title="YAPF_Farm - SEO Automation Platform")
app.add_middleware(GZipMiddleware, minimum_size=500)

ADMIN_USER = "admin"
ADMIN_PASS = "pre12345"
AUTH_COOKIE = "yapf_auth_token"
AUTH_SECRET = "yapf_session_pre12345_ok"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in ["/login", "/logout", "/favicon.ico"] or path.startswith("/static"):
        return await call_next(request)
        
    token = request.cookies.get(AUTH_COOKIE)
    if token != AUTH_SECRET:
        if path.startswith("/api"):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return RedirectResponse(url="/login", status_code=303)
        
    return await call_next(request)

import mimetypes

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("image/svg+xml", ".svg")

@app.middleware("http")
async def static_cache_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path == "/favicon.ico":
        response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
        response.headers["Access-Control-Allow-Origin"] = "*"
        if path.endswith(".woff2"):
            response.headers["Content-Type"] = "font/woff2"
        elif path.endswith(".woff"):
            response.headers["Content-Type"] = "font/woff"
        elif path.endswith(".ttf"):
            response.headers["Content-Type"] = "font/ttf"
    return response

@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def get_favicon():
    ico_path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(ico_path):
        return FileResponse(ico_path, media_type="image/x-icon")
    svg_path = os.path.join(STATIC_DIR, "favicon.svg")
    return FileResponse(svg_path, media_type="image/svg+xml")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error})

@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if username.strip() == ADMIN_USER and password == ADMIN_PASS:
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(key=AUTH_COOKIE, value=AUTH_SECRET, max_age=2592000, httponly=True)
        return resp
    return templates.TemplateResponse(request=request, name="login.html", context={"error": "Неверный логин или пароль"}, status_code=400)

@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(key=AUTH_COOKIE)
    return resp

@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(orchestrator.main_loop())
    asyncio.create_task(telegram_bot_polling_loop())

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/status")
async def get_status():
    conn = get_db()
    c = conn.cursor()
    
    # Profile stats
    c.execute("SELECT status, COUNT(*) as cnt FROM profiles GROUP BY status")
    prof_rows = c.fetchall()
    prof_stats = {r["status"]: r["cnt"] for r in prof_rows}
    
    # Clicks today
    c.execute("SELECT COUNT(*) as cnt FROM click_logs WHERE DATE(timestamp) = DATE('now') AND status = 'success'")
    clicks_today = c.fetchone()["cnt"]
    
    # Total projects
    c.execute("SELECT COUNT(*) as cnt FROM projects WHERE is_active = 1")
    projects_cnt = c.fetchone()["cnt"]
    conn.close()

    cpu_usage = psutil.cpu_percent(interval=None)
    ram_usage = psutil.virtual_memory().percent

    return {
        "is_running": (get_setting("is_running", "0") == "1"),
        "max_threads": int(get_setting("max_threads", "25")),
        "active_threads": orchestrator.stats["active_threads"],
        "warming_active": orchestrator.stats["warming_active"],
        "promo_active": orchestrator.stats["promo_active"],
        "last_log": orchestrator.stats["last_log"],
        "cpu_load": cpu_usage,
        "ram_load": ram_usage,
        "clicks_today": clicks_today,
        "projects_count": projects_cnt,
        "profiles": {
            "total": sum(prof_stats.values()),
            "new": prof_stats.get("new", 0),
            "warming": prof_stats.get("warming", 0),
            "ready": prof_stats.get("ready", 0)
        }
    }

@app.post("/api/toggle")
async def toggle_service():
    curr = get_setting("is_running", "0")
    new_val = "0" if curr == "1" else "1"
    set_setting("is_running", new_val)
    
    # Telegram alert on state change
    max_th = get_setting("max_threads", "25")
    if new_val == "1":
        live_logger.add("SUCCESS", "orchestrator", f"Служба запущена пользователем (Потоков: {max_th})")
        asyncio.create_task(send_telegram_alert(
            f"🟢 <b>YAPF_Farm: Служба запущена</b>\n"
            f"⚡ Потоков: <b>{max_th}</b>\n"
            f"🌐 Домен: <b>pffarm.ru</b>"
        ))
    else:
        live_logger.add("WARN", "orchestrator", "Служба остановлена пользователем")
        asyncio.create_task(send_telegram_alert(
            "⏸ <b>YAPF_Farm: Служба остановлена пользователем</b>\n"
            "Все активные сессии завершаются."
        ))
        
    return {"is_running": (new_val == "1")}

@app.post("/api/telegram/test")
async def test_telegram_alert():
    status = await send_telegram_alert(
        "🚀 <b>YAPF_Farm: Тестовое оповещение</b>\n\n"
        "Связь с Telegram-ботом настроена успешно!\n"
        "Сюда будут приходить сводки о кликах, смене IP, капчах и работе фермы."
    )
    return status

@app.get("/api/projects")
async def list_projects():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM projects ORDER BY created_at DESC")
    projects = [dict(r) for r in c.fetchall()]
    
    for p in projects:
        c.execute("SELECT * FROM keywords WHERE project_id = ? ORDER BY id DESC", (p["id"],))
        p["keywords"] = [dict(k) for k in c.fetchall()]
    conn.close()
    return projects

@app.post("/api/projects")
async def create_project(domain: str = Form(...), name: str = Form(...)):
    domain_clean = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO projects (domain, name) VALUES (?, ?)", (domain_clean, name.strip()))
        conn.commit()
    except Exception as e:
        conn.close()
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    conn.close()
    return {"status": "ok"}

@app.post("/api/keywords")
async def add_keyword(
    project_id: int = Form(...),
    keyword: str = Form(...),
    daily_goal: int = Form(10),
    geo_region: str = Form("213")
):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO keywords (project_id, keyword, daily_goal, geo_region)
        VALUES (?, ?, ?, ?)
    """, (project_id, keyword.strip(), daily_goal, geo_region.strip()))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM keywords WHERE project_id = ?", (project_id,))
    c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/api/live-logs")
async def get_live_logs_api(since_id: int = 0, limit: int = 150, level: str = "ALL"):
    return live_logger.get_since(since_id=since_id, limit=limit, filter_level=level)

@app.post("/api/live-logs/clear")
async def clear_live_logs_api():
    live_logger.clear()
    return {"status": "ok"}

@app.get("/api/logs")
async def get_logs(limit: int = 50):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM click_logs ORDER BY id DESC LIMIT ?", (limit,))
    logs = [dict(r) for r in c.fetchall()]
    conn.close()
    return logs

@app.get("/api/settings")
async def get_settings_api():
    return get_all_settings()

@app.post("/api/settings")
async def update_settings_api(data: dict):
    for k, v in data.items():
        set_setting(k, str(v))
    return {"status": "ok"}

@app.get("/api/telegram/status")
async def get_telegram_status_api():
    uname = get_setting("telegram_username", "").strip()
    cid = await resolve_target_chat_id()
    return {
        "bot_username": "YAPF_Farm_bot",
        "bot_url": "https://t.me/YAPF_Farm_bot",
        "username": uname,
        "is_linked": bool(cid),
        "chat_id": cid
    }

@app.post("/api/telegram/test")
async def test_telegram_alert_api():
    cid = await resolve_target_chat_id()
    if not cid:
        return {
            "status": "error",
            "message": "Чат не привязан! Откройте бота 👉 t.me/YAPF_Farm_bot и нажмите /start для привязки вашего логина."
        }
    uname = get_setting("telegram_username", "").strip()
    user_str = f"@{uname}" if uname else f"ID {cid}"
    res = await send_telegram_alert(
        f"🔔 <b>YAPF_Farm: Тестовое уведомление</b>\n\n"
        f"Связка с аккаунтом <b>{user_str}</b> подтверждена и работает отлично!\n"
        f"Суточные отчёты и алерты фермы будут приходить сюда.",
        custom_chat_id=cid
    )
    return res
