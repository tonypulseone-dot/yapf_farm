"""Durable, fair keyword claims and idempotent result accounting."""
import uuid
from datetime import timedelta

from app.database import get_db, get_setting
from app.reporting import business_date, utc_now, utc_text


def _number(key, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(float(get_setting(key, str(default))))))
    except (TypeError, ValueError, OverflowError):
        return default


def _query(day, cutoff, now):
    # Fewest attempts first, shuffled within each round. Failures count as turns.
    return '''
        SELECT k.*, p.domain, p.name AS project_name,
               p.brand_tail AS project_brand_tail,
               p.use_brand_tail AS project_use_brand_tail,
               p.default_region AS project_default_region
        FROM keywords k JOIN projects p ON p.id = k.project_id
        WHERE k.is_active = 1 AND p.is_active = 1 AND k.daily_goal > 0
          AND (CASE WHEN k.last_reset_date = ? THEN k.clicks_today ELSE 0 END) < k.daily_goal
          AND NOT EXISTS (SELECT 1 FROM keyword_attempts a
                          WHERE a.keyword_id=k.id AND a.status='reserved'
                            AND a.lease_expires_at > ?)
          AND NOT EXISTS (SELECT 1 FROM keyword_attempts a
                          WHERE a.keyword_id=k.id AND a.started_at > ?)
          AND NOT EXISTS (SELECT 1 FROM click_logs l
                          WHERE l.keyword_id=k.id AND l.attempt_id IS NULL AND l.timestamp > ?)
        ORDER BY (SELECT COUNT(*) FROM keyword_attempts a
                  WHERE a.keyword_id=k.id AND a.quota_date=?) ASC, RANDOM()
        LIMIT 1
    ''', (day, utc_text(now), utc_text(cutoff), utc_text(cutoff), day)


def has_pending_keyword(now=None):
    now = now or utc_now()
    gap = _number('keyword_min_interval_sec', 1200, 0, 86400)
    sql, params = _query(business_date(now), now-timedelta(seconds=gap), now)
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchone() is not None
    finally:
        conn.close()


def reserve_keyword(now=None):
    now = now or utc_now()
    gap = _number('keyword_min_interval_sec', 1200, 0, 86400)
    lease_seconds = _number('thread_max_lifetime_min', 30, 1, 1440)*60 + 120
    day = business_date(now)
    conn = get_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE keyword_attempts SET status='expired', finished_at=? "
                     "WHERE status='reserved' AND lease_expires_at<=?", (utc_text(now), utc_text(now)))
        # Update daily quota counters inside the same transaction as the claim.
        conn.execute("UPDATE keywords SET clicks_today=0,last_reset_date=? "
                     "WHERE last_reset_date IS NULL OR last_reset_date<>?", (day, day))
        sql, params = _query(day, now-timedelta(seconds=gap), now)
        row = conn.execute(sql, params).fetchone()
        if row is None:
            conn.commit()
            return None
        task = dict(row)
        token = uuid.uuid4().hex
        conn.execute('''INSERT INTO keyword_attempts
            (id,keyword_id,project_id,quota_date,status,started_at,lease_expires_at)
            VALUES(?,?,?,?,'reserved',?,?)''',
            (token,task['id'],task['project_id'],day,utc_text(now),utc_text(now+timedelta(seconds=lease_seconds))))
        conn.commit()
        task['_attempt_id'] = token
        task['_started_at'] = utc_text(now)
        return task
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_attempt(task, profile, result, now=None):
    """Record one completion. A repeated finalization cannot add a second click."""
    now = now or utc_now()
    status = result.get('status', 'error')
    if status not in {'success', 'not_found', 'error', 'captcha', 'cancelled'}:
        status = 'error'
    if status == 'success' and not result.get('navigation_verified'):
        status = 'error'
        result = {**result, 'details': 'Target navigation was not verified'}
    conn = get_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        claimed = conn.execute('SELECT status FROM keyword_attempts WHERE id=?', (task['_attempt_id'],)).fetchone()
        if not claimed or claimed['status'] != 'reserved':
            conn.rollback()
            return False
        stamp = utc_text(now)
        conn.execute('UPDATE keyword_attempts SET status=?,finished_at=? WHERE id=?',
                     (status,stamp,task['_attempt_id']))
        conn.execute('''INSERT INTO click_logs
            (keyword,target_url,domain,profile_name,device,region,position_found,status,details,
             timestamp,keyword_id,project_id,attempt_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (task['keyword'],result.get('target_url') or task.get('target_url') or '',task['domain'],
             profile.get('name',''),profile.get('device_type',''),task.get('geo_region','213'),
             result.get('position',0),status,result.get('details',''),stamp,
             task['id'],task['project_id'],task['_attempt_id']))
        cooldown = _number('profile_min_interval_sec', 1200, 0, 86400)
        if status == 'captcha':
            cooldown = max(cooldown, _number('profile_captcha_cooldown_sec', 3600, 0, 86400))
        available = utc_text(now + timedelta(seconds=cooldown))
        if profile.get('id'):
            conn.execute('''UPDATE profiles SET last_used_at=?,
                promo_available_at=CASE WHEN promo_available_at > ? THEN promo_available_at ELSE ? END
                WHERE id=?''', (stamp,available,available,profile['id']))
        if status == 'success':
            today = business_date(now)
            conn.execute('''UPDATE keywords SET clicks_today=
                CASE WHEN last_reset_date=? THEN clicks_today+1 ELSE 1 END,
                last_reset_date=?,last_click_at=? WHERE id=?''', (today,today,stamp,task['id']))
            conn.execute("UPDATE profiles SET total_clicks=total_clicks+1,last_used_at=?,status='warming' WHERE id=?",
                         (stamp,profile['id']))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
