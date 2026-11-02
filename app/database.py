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
        "profile_group": "",

        # Warming
        "warming_ratio": "0.6",
        "target_profiles_count": "300",
        "profile_warming_cooldown_min": "15",
        "warming_cycles_to_ready": "2",
        "warming_sites_per_cycle_min": "3",
        "warming_sites_per_cycle_max": "7",
        "dwell_time_site_min": "30",
        "dwell_time_site_max": "90",
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
