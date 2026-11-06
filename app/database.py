import sqlite3
import json
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "yapf_farm.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=45.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=45000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Profiles table
    c.execute("""
    CREATE TABLE IF NOT EXISTS profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        user_data_dir TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new', -- new, warming, ready, cooldown
        device_type TEXT NOT NULL DEFAULT 'desktop', -- desktop, android
        cycles_completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_warmed_at TIMESTAMP,
        last_used_at TIMESTAMP,
        total_clicks INTEGER DEFAULT 0,
        proxy TEXT
    )
    """)
    
    # Projects table
    c.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Keywords table
    c.execute("""
    CREATE TABLE IF NOT EXISTS keywords (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        keyword TEXT NOT NULL,
        target_url TEXT,
        daily_goal INTEGER DEFAULT 10,
        clicks_today INTEGER DEFAULT 0,
        last_reset_date TEXT DEFAULT '',
        geo_region TEXT DEFAULT '213', -- 213 = Moscow
        search_engine TEXT DEFAULT 'yandex',
        last_click_at TIMESTAMP,
        is_active INTEGER DEFAULT 1,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """)
    
    # Click logs table
    c.execute("""
    CREATE TABLE IF NOT EXISTS click_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT NOT NULL,
        target_url TEXT NOT NULL,
        domain TEXT NOT NULL,
        profile_name TEXT,
        device TEXT,
        region TEXT,
        position_found INTEGER,
        status TEXT NOT NULL, -- success, not_found, captcha, error
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Settings table
    c.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    # Positions History table (Active & Organic Rank Tracking up to Top-100)
    c.execute("""
    CREATE TABLE IF NOT EXISTS positions_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword_id INTEGER,
        project_id INTEGER NOT NULL,
        keyword TEXT NOT NULL,
        domain TEXT NOT NULL,
        region TEXT DEFAULT '213',
        device_type TEXT DEFAULT 'desktop',
        position INTEGER DEFAULT 0,
        target_url TEXT DEFAULT '',
        checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source TEXT DEFAULT 'checker',
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_pos_proj ON positions_history(project_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pos_kw ON positions_history(keyword_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pos_time ON positions_history(checked_at)")

    # Additive migration: retain all existing logs and counters.
    c.execute('CREATE INDEX IF NOT EXISTS idx_click_profile_time ON click_logs(profile_name,timestamp)')
    profile_columns = {r['name'] for r in c.execute('PRAGMA table_info(profiles)')}
    if 'promo_available_at' not in profile_columns:
        c.execute('ALTER TABLE profiles ADD COLUMN promo_available_at TEXT')
    # Recover actual past usage, including failed attempts. Do not invent clicks.
    c.execute('''UPDATE profiles SET last_used_at=(
        SELECT MAX(timestamp) FROM click_logs WHERE profile_name=profiles.name)
        WHERE EXISTS (SELECT 1 FROM click_logs WHERE profile_name=profiles.name)
        AND (last_used_at IS NULL OR last_used_at < (
            SELECT MAX(timestamp) FROM click_logs WHERE profile_name=profiles.name))''')
    c.execute('''UPDATE profiles SET promo_available_at=datetime(last_used_at,'+20 minutes')
        WHERE promo_available_at IS NULL AND last_used_at IS NOT NULL''')
    log_columns = {r['name'] for r in c.execute('PRAGMA table_info(click_logs)')}
    for column, kind in [('keyword_id', 'INTEGER'), ('project_id', 'INTEGER'), ('attempt_id', 'TEXT')]:
        if column not in log_columns:
            c.execute(f'ALTER TABLE click_logs ADD COLUMN {column} {kind}')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_click_attempt ON click_logs(attempt_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_click_time ON click_logs(timestamp)')
    c.execute('''CREATE TABLE IF NOT EXISTS keyword_attempts (
        id TEXT PRIMARY KEY, keyword_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
        quota_date TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
        lease_expires_at TEXT NOT NULL, finished_at TEXT
    )''')
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_keyword_reserved ON keyword_attempts(keyword_id) WHERE status='reserved'")
    c.execute('CREATE INDEX IF NOT EXISTS idx_keyword_turn ON keyword_attempts(keyword_id,quota_date,started_at)')

    # Telegram users registry table (mapping usernames to chat_ids automatically)
    c.execute("""
    CREATE TABLE IF NOT EXISTS telegram_users (
        chat_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Auto-migrations for Projects & Keywords tables (brand tails, default regions)
    try:
        c.execute("ALTER TABLE projects ADD COLUMN default_region TEXT DEFAULT '213'")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE projects ADD COLUMN brand_tail TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE projects ADD COLUMN use_brand_tail INTEGER DEFAULT 1")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE keywords ADD COLUMN brand_tail TEXT DEFAULT ''")
    except Exception:
        pass
    
    # Insert default settings if not exist (Comprehensive Alpine-compatible schema)
    defaults = {
        # General & Speed
        "is_running": "0",
        "max_threads": "25",
        "thread_launch_interval_sec": "2.5",
        "thread_max_lifetime_min": "30",
        "thread_auto_restart": "1",
        "keyword_min_interval_sec": "1200",
        "profile_group": "",

        # Warming
        "warming_ratio": "0.6",
        "target_profiles_count": "300",
        "profile_warming_cooldown_min": "15",
        "warming_cycles_to_ready": "1",
        "warming_sites_per_cycle_min": "1",
        "warming_sites_per_cycle_max": "1",
        "dwell_time_site_min": "15",
        "dwell_time_site_max": "35",
        "min_ready_age_hours": "18",
        "add_warming_idle_hours": "48",
        "warming_entry_point": "random",

        # Promo & Search
        "search_depth": "50",
        "competitor_clicks_count": "1",
        "target_site_dwell_min": "60",
        "target_site_dwell_max": "180",
        "target_site_internal_clicks": "2",
        "mobile_share_percent": "50",

        # Position Checker (Top-100)
        "position_check_hour": "6",
        "position_check_enabled": "1",
        "position_check_device": "desktop",
        "position_check_depth": "100",

        # Proxy
        "proxy_list": "",
        "proxy_type": "http",
        "proxy_rotate_url": "",
        "proxy_rotate_delay_sec": "3",
        "proxy_bind_to_profile": "1",

        # Captcha priorities & individual keys
        "captcha_text_services": "capify,capguru,botlab,capsola,rucaptcha",
        "captcha_image_services": "capify,capguru,botlab,capsola,rucaptcha",
        "captcha_google_service": "capguru",
        "key_capify": "",
        "key_capguru": "",
        "key_botlab": "",
        "key_capsola": "",
        "key_rucaptcha": "",
        "key_captrix": "",
        "key_xevil_url": "http://127.0.0.1:80",
        "key_xevil_key": "",
        "captcha_max_consecutive_attempts": "3",
        "captcha_max_session_attempts": "10",
        "captcha_timeout_seconds": "60",
        "profile_min_interval_sec": "1200",
        "profile_captcha_cooldown_sec": "3600",
        "captcha_action": "abort",
        "captcha_distort_coords": "1",

        # Telegram
        "telegram_bot_token": "9065799178:AAFU7Tbi0QIo2BRa8wf4t77o2APD0AJ2_EA",
        "telegram_username": "",
        "telegram_chat_id": "",
        "telegram_notifications_enabled": "1",
        "telegram_notify_clicks": "1",
        "telegram_notify_errors": "1",
        "telegram_notify_captcha": "1",
        "telegram_daily_report_hour": "21"
    }
    
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Link old rows only when a domain/query pair identifies exactly one keyword.
    c.execute('''UPDATE click_logs SET keyword_id=(
        SELECT MIN(k.id) FROM keywords k JOIN projects p ON p.id=k.project_id
        WHERE p.domain=click_logs.domain AND k.keyword=click_logs.keyword
        HAVING COUNT(*)=1) WHERE keyword_id IS NULL''')
    c.execute('''UPDATE click_logs SET project_id=(SELECT project_id FROM keywords WHERE id=click_logs.keyword_id)
                 WHERE project_id IS NULL AND keyword_id IS NOT NULL''')
        
    conn.commit()
    conn.close()

def get_setting(key, default=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def get_all_settings():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings")
    rows = c.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

def register_telegram_user(chat_id: int, username: str, first_name: str):
    conn = get_db()
    c = conn.cursor()
    clean_user = (username or "").strip().lstrip("@").lower()
    c.execute("""
    INSERT INTO telegram_users (chat_id, username, first_name, last_interaction)
    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(chat_id) DO UPDATE SET
        username = excluded.username,
        first_name = excluded.first_name,
        last_interaction = CURRENT_TIMESTAMP
    """, (chat_id, clean_user, first_name or ""))
    conn.commit()
    conn.close()

def get_telegram_chat_id_by_username(username: str):
    if not username:
        return None
    clean_user = username.strip().lstrip("@").lower()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM telegram_users WHERE LOWER(username) = ? ORDER BY last_interaction DESC LIMIT 1", (clean_user,))
    row = c.fetchone()
    conn.close()
    return row["chat_id"] if row else None

def get_latest_telegram_user():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT chat_id, username, first_name FROM telegram_users ORDER BY last_interaction DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

# ==========================================
# POSITIONS & RANK TRACKING (TOP-100)
# ==========================================

def record_position(keyword_id, project_id, keyword, domain, region="213", device_type="desktop", position=0, target_url="", source="checker"):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO positions_history (keyword_id, project_id, keyword, domain, region, device_type, position, target_url, source, checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (keyword_id, project_id, keyword, domain, region, device_type, position, target_url, source))
    conn.commit()
    conn.close()

def get_positions_kpi(project_id=None):
    conn = get_db()
    c = conn.cursor()
    
    proj_filter = "WHERE project_id = ?" if project_id else ""
    params = (project_id,) if project_id else ()
    
    # Total distinct active domains
    if project_id:
        c.execute("SELECT COUNT(DISTINCT domain) as cnt FROM projects WHERE id = ?", (project_id,))
    else:
        c.execute("SELECT COUNT(DISTINCT domain) as cnt FROM projects WHERE is_active = 1")
    total_domains = c.fetchone()["cnt"]
    
    # Most recent check for each keyword
    subquery = f"""
        SELECT ph.keyword_id, ph.position, ph.checked_at
        FROM positions_history ph
        INNER JOIN (
            SELECT keyword_id, MAX(id) as max_id
            FROM positions_history
            {proj_filter}
            GROUP BY keyword_id
        ) latest ON ph.id = latest.max_id
    """
    c.execute(subquery, params)
    latest_rows = c.fetchall()
    
    top_1 = sum(1 for r in latest_rows if r["position"] == 1)
    top_2_10 = sum(1 for r in latest_rows if 2 <= r["position"] <= 10)
    top_11_30 = sum(1 for r in latest_rows if 11 <= r["position"] <= 30)
    top_31_50 = sum(1 for r in latest_rows if 31 <= r["position"] <= 50)
    top_51_plus = sum(1 for r in latest_rows if r["position"] > 50 or r["position"] == 0)
    
    top_3 = sum(1 for r in latest_rows if 1 <= r["position"] <= 3)
    top_10 = sum(1 for r in latest_rows if 1 <= r["position"] <= 10)
    top_50 = sum(1 for r in latest_rows if 1 <= r["position"] <= 50)
    top_100 = sum(1 for r in latest_rows if 1 <= r["position"] <= 100)
    out_of_top = sum(1 for r in latest_rows if r["position"] == 0 or r["position"] > 100)
    
    in_top_positions = [r["position"] for r in latest_rows if 1 <= r["position"] <= 100]
    avg_position = round(sum(in_top_positions) / len(in_top_positions), 1) if in_top_positions else 0.0
    
    # Last updated timestamp
    c.execute(f"SELECT MAX(checked_at) as last_updated FROM positions_history {proj_filter}", params)
    last_updated_row = c.fetchone()
    last_updated = last_updated_row["last_updated"] if last_updated_row and last_updated_row["last_updated"] else None
    
    conn.close()
    return {
        "total_domains": total_domains,
        "avg_position": avg_position,
        "top_1": top_1,
        "top_2_10": top_2_10,
        "top_11_30": top_11_30,
        "top_31_50": top_31_50,
        "top_51_plus": top_51_plus,
        "top_3": top_3,
        "top_10": top_10,
        "top_50": top_50,
        "top_100": top_100,
        "out_of_top": out_of_top,
        "tracked_queries": len(latest_rows),
        "last_updated": last_updated[:16].replace("-", ".") if last_updated else "Нет замеров"
    }

def get_project_positions(project_id=None, days=7, search="", sort_by="best"):
    conn = get_db()
    c = conn.cursor()
    
    # Get all active keywords for this project (or all projects if project_id is None)
    if project_id:
        c.execute("""
            SELECT k.id, k.keyword, k.target_url, k.geo_region, p.domain, p.name as project_name
            FROM keywords k
            JOIN projects p ON p.id = k.project_id
            WHERE k.project_id = ?
        """, (project_id,))
    else:
        c.execute("""
            SELECT k.id, k.keyword, k.target_url, k.geo_region, p.domain, p.name as project_name
            FROM keywords k
            JOIN projects p ON p.id = k.project_id
        """)
    kw_rows = c.fetchall()
    
    results = []
    for kw in kw_rows:
        kw_id = kw["id"]
        keyword_text = kw["keyword"]
        
        if search and (search.lower() not in keyword_text.lower()) and (search.lower() not in (kw["target_url"] or "").lower()):
            continue
            
        # Get last N days of position history for this keyword
        c.execute("""
            SELECT position, target_url, checked_at, device_type, region
            FROM positions_history
            WHERE keyword_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (kw_id, max(14, days * 2)))
        history_rows = c.fetchall()
        
        current_pos = history_rows[0]["position"] if history_rows else 0
        current_url = history_rows[0]["target_url"] if history_rows and history_rows[0]["target_url"] else (kw["target_url"] or "")
        last_checked = history_rows[0]["checked_at"][:16].replace("-", ".") if history_rows else "Не проверялся"
        device_type = history_rows[0]["device_type"] if history_rows else "desktop"
        latest_region = history_rows[0]["region"] if history_rows and history_rows[0]["region"] else (kw["geo_region"] or "213")
        
        # Calculate diff with previous check
        diff = 0
        if len(history_rows) >= 2:
            prev_pos = history_rows[1]["position"]
            if current_pos > 0 and prev_pos > 0:
                diff = prev_pos - current_pos  # positive if improved (e.g. 5 -> 2 = +3)
            elif current_pos > 0 and prev_pos == 0:
                diff = 100 - current_pos       # entered Top 100
            elif current_pos == 0 and prev_pos > 0:
                diff = -prev_pos               # dropped out of Top 100
                
        # Build history sparkline (last N records reversed to chronological order)
        sparkline = [{"pos": r["position"], "date": r["checked_at"][5:10]} for r in reversed(history_rows[:days])]
        
        results.append({
            "keyword_id": kw_id,
            "keyword": keyword_text,
            "domain": kw["domain"],
            "region": latest_region,
            "device_type": device_type,
            "target_url": current_url,
            "current_position": current_pos,
            "diff": diff,
            "last_checked": last_checked,
            "sparkline": sparkline
        })
        
    conn.close()
    
    # Sorting
    if sort_by == "best":
        results.sort(key=lambda x: (x["current_position"] == 0, x["current_position"] if x["current_position"] > 0 else 999, -x["diff"]))
    elif sort_by == "worst":
        results.sort(key=lambda x: (x["current_position"] == 0, -x["current_position"], x["diff"]))
    elif sort_by == "growth":
        results.sort(key=lambda x: -x["diff"])
    elif sort_by == "drop":
        results.sort(key=lambda x: x["diff"])
    elif sort_by == "alpha":
        results.sort(key=lambda x: x["keyword"].lower())
        
    return results
