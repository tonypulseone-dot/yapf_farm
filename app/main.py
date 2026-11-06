import os
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"C:\Users\Administrator\AppData\Local\ms-playwright"
import asyncio
import os
import psutil
import time
import subprocess
import shutil
from datetime import datetime, timedelta
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
from app.reporting import daily_log_metrics, day_bounds
from app.dispatch import has_pending_keyword
from app.regions import parse_regions
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
    from app.engine.position_checker import position_checker
    asyncio.create_task(position_checker.daily_scheduler_loop())

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
    daily_metrics = daily_log_metrics(conn)
    
    # Total projects
    c.execute("SELECT COUNT(*) as cnt FROM projects WHERE is_active = 1")
    projects_cnt = c.fetchone()["cnt"]
    active_keys = c.execute('SELECT COUNT(*) FROM keywords k JOIN projects p ON p.id=k.project_id '
                            'WHERE p.is_active=1 AND k.is_active=1 AND k.daily_goal>0').fetchone()[0]
    if not projects_cnt:
        promotion_state = 'Все проекты на паузе'
    elif not active_keys:
        promotion_state = 'Нет активных ключевых запросов'
    elif not has_pending_keyword():
        promotion_state = 'Ожидание паузы, завершения попыток или новой суточной нормы'
    else:
        promotion_state = 'Запросы доступны; ожидаем свободный Ready-профиль'

    # Ready ETA Calculation
    c.execute("SELECT MIN(created_at) as earliest_warming FROM profiles WHERE status = 'warming'")
    row_earliest = c.fetchone()
    earliest_warming = row_earliest["earliest_warming"] if row_earliest else None

    ready_eta_hours = 0.0
    ready_eta_time_str = "Все профили готовы"
    target_pool = int(get_setting("target_profiles_count", "4000"))
    min_age_h = float(get_setting("min_ready_age_hours", "18"))

    if earliest_warming:
        try:
            created_dt = datetime.strptime(earliest_warming[:19], "%Y-%m-%d %H:%M:%S")
            ready_dt_utc = created_dt + timedelta(hours=min_age_h)
            now_utc = datetime.utcnow()
            diff_sec = (ready_dt_utc - now_utc).total_seconds()
            if diff_sec > 0:
                ready_eta_hours = round(diff_sec / 3600.0, 1)
                ready_dt_msk = ready_dt_utc + timedelta(hours=3)
                ready_eta_time_str = f"Сегодня в {ready_dt_msk.strftime('%H:%M')} MSK (~{ready_eta_hours} ч)"
            else:
                ready_eta_time_str = "Первая волна созрела (переходит в Ready)"
        except Exception:
            ready_eta_time_str = "Расчёт времени..."
    elif prof_stats.get("warming", 0) == 0 and prof_stats.get("new", 0) == 0:
        ready_eta_time_str = "База полностью готова"

    ready_count = prof_stats.get("ready", 0)
    ready_percent = round((ready_count / max(1, target_pool)) * 100, 1)
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
        **daily_metrics,
        "promotion_state": promotion_state,
        "projects_count": projects_cnt,
        "profiles": {
            "total": sum(prof_stats.values()),
            "new": prof_stats.get("new", 0),
            "warming": prof_stats.get("warming", 0),
            "ready": prof_stats.get("ready", 0)
        },
        "ready_eta": {
            "hours_left": ready_eta_hours,
            "eta_time_msk": ready_eta_time_str,
            "target_pool": target_pool,
            "ready_count": ready_count,
            "warming_count": prof_stats.get("warming", 0),
            "new_count": prof_stats.get("new", 0),
            "ready_percent": ready_percent,
            "standard_name": "Alpine «Буст и мало капчи»",
            "session_format": "1 запрос ya.ru + 1 сайт из выдачи",
            "cooldown": "3–8 часов (180–480 мин)",
            "idle_re_warm": "Донагул через 48 часов простоя",
            "ready_threshold": f"1 цикл + {int(min_age_h)}ч отлёжки"
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
    start, end = day_bounds()
    rows = c.execute('''SELECT keyword_id,COUNT(*) AS attempts,
        SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS successes,
        SUM(CASE WHEN status='not_found' THEN 1 ELSE 0 END) AS missing,
        SUM(CASE WHEN status IN ('error','captcha') THEN 1 ELSE 0 END) AS errors
        FROM click_logs WHERE timestamp>=? AND timestamp<? AND keyword_id IS NOT NULL
        GROUP BY keyword_id''', (start,end)).fetchall()
    metrics = {r['keyword_id']: dict(r) for r in rows}
    
    for p in projects:
        c.execute("SELECT * FROM keywords WHERE project_id = ? ORDER BY id DESC", (p["id"],))
        p["keywords"] = [dict(k) for k in c.fetchall()]
        for keyword in p['keywords']:
            counts = metrics.get(keyword['id'], {})
            keyword.update(clicks_today=counts.get('successes',0),
                           attempts_today=counts.get('attempts',0),
                           not_found_today=counts.get('missing',0),
                           errors_today=counts.get('errors',0))
    conn.close()
    return projects

@app.post("/api/projects")
async def create_project(
    domain: str = Form(...), 
    name: str = Form(...),
    default_region: str = Form("213"),
    brand_tail: str = Form(""),
    use_brand_tail: int = Form(1),
    keywords_text: str = Form(""),
    daily_goal: int = Form(10),
    geo_regions: str = Form(None)
):
    domain_clean = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
    regions = parse_regions(geo_regions, default_region)
    region_clean = regions[0]
    brand_clean = brand_tail.strip()
    
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO projects (domain, name, default_region, brand_tail, use_brand_tail, is_active) 
            VALUES (?, ?, ?, ?, ?, 0)
        """, (domain_clean, name.strip(), region_clean, brand_clean, int(use_brand_tail)))
        project_id = c.lastrowid

        # Bulk insert keywords if provided
        if keywords_text and keywords_text.strip():
            raw_lines = [l.strip() for l in keywords_text.splitlines() if l.strip()]
            for line in raw_lines:
                # Support format: "ключевое слово | 15" or simply "ключевое слово"
                goal = daily_goal
                key_str = line
                if "|" in line:
                    parts = line.split("|", 1)
                    key_str = parts[0].strip()
                    try:
                        goal = int(parts[1].strip())
                    except ValueError:
                        goal = daily_goal
                
                if key_str:
                    for region in regions:
                        c.execute("""
                            INSERT INTO keywords (project_id, keyword, daily_goal, geo_region, brand_tail)
                            VALUES (?, ?, ?, ?, ?)
                        """, (project_id, key_str, goal, region, brand_clean))

        conn.commit()
    except Exception as e:
        conn.close()
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    conn.close()
    return {"status": "ok", "project_id": project_id}

@app.put("/api/projects/{project_id}")
async def update_project(
    project_id: int,
    name: str = Form(None),
    default_region: str = Form(None),
    brand_tail: str = Form(None),
    use_brand_tail: int = Form(None),
    apply_region_to_all: int = Form(0)
):
    conn = get_db()
    c = conn.cursor()
    try:
        if name is not None:
            c.execute("UPDATE projects SET name = ? WHERE id = ?", (name.strip(), project_id))
        if default_region is not None:
            reg = default_region.strip() or "213"
            c.execute("UPDATE projects SET default_region = ? WHERE id = ?", (reg, project_id))
            if apply_region_to_all:
                c.execute("UPDATE keywords SET geo_region = ? WHERE project_id = ?", (reg, project_id))
        if brand_tail is not None:
            c.execute("UPDATE projects SET brand_tail = ? WHERE id = ?", (brand_tail.strip(), project_id))
        if use_brand_tail is not None:
            c.execute("UPDATE projects SET use_brand_tail = ? WHERE id = ?", (int(use_brand_tail), project_id))
        conn.commit()
    except Exception as e:
        conn.close()
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    conn.close()
    return {"status": "ok"}

@app.post("/api/keywords")
async def add_keywords(
    project_id: int = Form(...),
    keyword: str = Form(None),
    keywords_text: str = Form(None),
    daily_goal: int = Form(10),
    geo_region: str = Form(None),
    brand_tail: str = Form(""),
    geo_regions: str = Form(None)
):
    # Validate before opening a connection or inserting any rows.
    regions = parse_regions(geo_regions, geo_region) if geo_regions is not None or geo_region is not None else None
    conn = get_db()
    c = conn.cursor()
    project = c.execute("SELECT brand_tail,default_region FROM projects WHERE id=?",(project_id,)).fetchone()
    if not project:
        conn.close()
        return JSONResponse({"detail":"Проект не найден"},status_code=404)
    regions = regions or parse_regions(None, project['default_region'])
    brand_clean = brand_tail.strip() or project['brand_tail'] or ''

    items_to_add = []
    if keywords_text and keywords_text.strip():
        for l in keywords_text.splitlines():
            l = l.strip()
            if not l:
                continue
            goal = daily_goal
            k_name = l
            if "|" in l:
                parts = l.split("|", 1)
                k_name = parts[0].strip()
                try:
                    goal = int(parts[1].strip())
                except ValueError:
                    goal = daily_goal
            if k_name:
                items_to_add.append((k_name, goal))
    elif keyword and keyword.strip():
        items_to_add.append((keyword.strip(), daily_goal))

    for k_name, goal in items_to_add:
        for region in regions:
            c.execute("""
                INSERT INTO keywords (project_id, keyword, daily_goal, geo_region, brand_tail)
                VALUES (?, ?, ?, ?, ?)
            """, (project_id, k_name, goal, region, brand_clean))

    conn.commit()
    conn.close()
    return {"status": "ok", "added_count": len(items_to_add) * len(regions), "regions": regions}

@app.post("/api/projects/{project_id}/toggle-promo")
async def toggle_project_promo_api(project_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, name, is_active FROM projects WHERE id = ?", (project_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return JSONResponse({"status": "error", "message": "Проект не найден"}, status_code=404)
    
    current_active = row["is_active"]
    new_state = 0 if current_active == 1 else 1
    c.execute("UPDATE projects SET is_active = ? WHERE id = ?", (new_state, project_id))
    conn.commit()
    conn.close()
    
    state_str = "запущено" if new_state == 1 else "поставлено на паузу"
    live_logger.add("INFO", "orchestrator", f"Продвижение проекта «{row['name']}» {state_str}")
    return {"status": "ok", "is_active": new_state}

@app.delete("/api/keywords/{keyword_id}")
async def delete_keyword(keyword_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM positions_history WHERE keyword_id = ?", (keyword_id,))
    c.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM positions_history WHERE project_id = ?", (project_id,))
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
    if 'keyword_min_interval_sec' in data:
        try:
            gap = int(data['keyword_min_interval_sec'])
            if not 0 <= gap <= 86400:
                raise ValueError()
        except (TypeError, ValueError):
            return JSONResponse({'status':'error','message':'Пауза запроса должна быть целым числом от 0 до 86400 секунд'}, status_code=422)
    for k, v in data.items():
        if isinstance(v, bool):
            val_str = "1" if v else "0"
        elif str(v).lower() == "true":
            val_str = "1"
        elif str(v).lower() == "false":
            val_str = "0"
        else:
            val_str = str(v)
        set_setting(k, val_str)
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

# ==========================================
# POSITIONS & RANK TRACKER API (TOP-100)
# ==========================================

@app.get("/api/positions/kpi")
async def get_positions_kpi_api(project_id: int = None, device_type: str = "desktop"):
    from app.position_history import position_timeline, timeline_kpi
    return timeline_kpi(position_timeline(project_id,31,device_type=device_type))

@app.get("/api/positions/data")
async def get_positions_data_api(project_id: int = None, days: int = 7, search: str = "", sort: str = "best"):
    from app.database import get_project_positions
    return get_project_positions(project_id=project_id, days=days, search=search, sort_by=sort)

@app.get("/api/positions/timeline")
async def get_positions_timeline_api(project_id: int = None, days: int = 7, search: str = "", device_type: str = "desktop", sort: str = "alpha"):
    from app.position_history import position_timeline
    if device_type not in ('desktop','android'):
        return JSONResponse({'detail':'Неизвестное устройство'},status_code=422)
    return position_timeline(project_id,days,search,device_type,sort)

@app.get("/api/positions/progress")
async def get_positions_progress_api(project_id: int = None):
    from app.engine.position_checker import position_checker
    return position_checker.get_progress(project_id)

@app.post("/api/positions/check")
async def run_positions_check_api(data: dict):
    from app.engine.position_checker import position_checker
    try:
        project_id = int(data.get("project_id", 0))
    except (ValueError,TypeError):
        return JSONResponse({'message':'Некорректный проект'},status_code=422)
    regions = data.get("regions", [])
    region = str(data.get("region") or "").strip() or None
    device_type = data.get("device_type", "desktop")
    fresh = bool(data.get("fresh", True))
    
    if not project_id:
        return {"status": "error", "message": "Выберите проект для проверки"}
        
    return await position_checker.start_project_check(
        project_id=project_id, region=region, regions=regions,
        device_type=device_type, fresh=fresh)

@app.post("/api/positions/check-keyword/{keyword_id}")
async def check_single_keyword_api(keyword_id: int, data: dict = None):
    from app.engine.position_checker import position_checker
    from app.database import get_db
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM keywords WHERE id = ?", (keyword_id,))
    kw = c.fetchone()
    if not kw:
        conn.close()
        return {"status": "error", "message": "Ключ не найден"}
    kw_dict = dict(kw)
    c.execute("SELECT * FROM projects WHERE id = ?", (kw_dict["project_id"],))
    proj = c.fetchone()
    conn.close()
    if not proj:
        return {"status": "error", "message": "Проект не найден"}
        
    data=data or {}
    geo=parse_regions(None,str(data.get('region') or kw_dict.get('geo_region') or '213'))[0]
    device=data.get('device_type','desktop')
    if device not in ('desktop','android'):
        return JSONResponse({'detail':'Неизвестное устройство'},status_code=422)
    res = await position_checker.check_single_keyword(
        keyword_row=kw_dict, project_row=dict(proj), region=geo, device_type=device)
    return {"status": "ok", "data": res}

@app.get("/api/positions/timeline-export")
async def export_position_timeline(project_id: int = None, days: int = 7, device_type: str = 'desktop', search: str = ''):
    from app.position_history import position_timeline
    from fastapi.responses import StreamingResponse
    import csv,io
    data=position_timeline(project_id,days,search,device_type)
    def safe(value):
        value=str(value)
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    stream=io.StringIO();writer=csv.writer(stream,delimiter=';')
    writer.writerow(['Запрос','Домен','Регион','Устройство']+[d['label'] for d in data['dates']])
    for row in data['rows']:
        writer.writerow([safe(row['keyword']),safe(row['domain']),row['region'],row['device_type']]+[
            ('>100' if row['cells'][d['date']]['position']==0 else row['cells'][d['date']]['position']) if d['date'] in row['cells'] else '' for d in data['dates']])
    return StreamingResponse(iter(['\ufeff'+stream.getvalue()]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="positions_daily.csv"'})

@app.get("/api/positions/export")
async def export_positions_csv_api(project_id: int = None, days: int = 7):
    from app.database import get_project_positions
    import io, csv
    from fastapi.responses import StreamingResponse
    
    data = get_project_positions(project_id=project_id, days=days)
    
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID", "Запрос", "Домен", "Регион", "Устройство", "Позиция ТОП-100", "Динамика", "Релевантный URL", "Дата замера"])
    
    for r in data:
        pos_str = str(r["current_position"]) if r["current_position"] > 0 else ">100"
        diff_str = f"+{r['diff']}" if r["diff"] > 0 else (str(r["diff"]) if r["diff"] < 0 else "0")
        writer.writerow([r["keyword_id"], r["keyword"], r["domain"], r["region"], r["device_type"], pos_str, diff_str, r["target_url"], r["last_checked"]])
        
    output.seek(0)
    pid_tag = f"project_{project_id}" if project_id else "all_projects"
    filename = f"positions_{pid_tag}_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/system/version")
async def get_system_version():
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    git_bin = shutil.which("git") or (r"C:\Program Files\Git\cmd\git.exe" if os.path.exists(r"C:\Program Files\Git\cmd\git.exe") else None)
    commit = "release"
    if git_bin and os.path.exists(os.path.join(app_dir, ".git")):
        try:
            commit = subprocess.check_output([git_bin, "-C", app_dir, "rev-parse", "--short", "HEAD"], text=True, timeout=3).strip()
        except Exception:
            pass
    return {"commit": commit, "repo": "https://github.com/tonypulseone-dot/yapf_farm"}

@app.post("/api/system/update")
async def system_update():
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    git_bin = shutil.which("git") or (r"C:\Program Files\Git\cmd\git.exe" if os.path.exists(r"C:\Program Files\Git\cmd\git.exe") else None)
    if not git_bin:
        return {"status": "error", "message": "Git не найден в системе"}

    try:
        subprocess.run([git_bin, "-C", app_dir, "fetch", "origin", "main"], check=True, capture_output=True, text=True, timeout=10)
        local_h = subprocess.check_output([git_bin, "-C", app_dir, "rev-parse", "HEAD"], text=True, timeout=5).strip()
        remote_h = subprocess.check_output([git_bin, "-C", app_dir, "rev-parse", "origin/main"], text=True, timeout=5).strip()

        if local_h == remote_h:
            return {"status": "up_to_date", "commit": local_h[:7], "message": f"Установлена актуальная версия ({local_h[:7]}). Обновлений нет."}

        pull_res = subprocess.run([git_bin, "-C", app_dir, "pull", "--ff-only", "origin", "main"], capture_output=True, text=True, timeout=15)
        if pull_res.returncode == 0:
            return {"status": "updated", "from": local_h[:7], "to": remote_h[:7], "message": f"Система успешно обновлена с GitHub ({local_h[:7]} ➔ {remote_h[:7]})!"}
        else:
            return {"status": "error", "message": f"Ошибка git pull: {pull_res.stderr}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
