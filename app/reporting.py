"""Business-day counters. SQLite timestamps are UTC; the dashboard day is MSK."""
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def utc_now():
    return datetime.now(timezone.utc)


def utc_text(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def business_date(now=None):
    return (now or utc_now()).astimezone(MSK).date().isoformat()


def day_bounds(now=None):
    current = (now or utc_now()).astimezone(MSK)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return utc_text(start), utc_text(start + timedelta(days=1))


def daily_log_metrics(conn, now=None):
    start, end = day_bounds(now)
    rows = conn.execute(
        'SELECT status, COUNT(*) AS n FROM click_logs '
        'WHERE timestamp >= ? AND timestamp < ? GROUP BY status', (start, end)
    ).fetchall()
    counts = {r['status']: r['n'] for r in rows}
    return {
        'clicks_today': counts.get('success', 0),
        'attempts_today': sum(counts.values()),
        'not_found_today': counts.get('not_found', 0),
        'errors_today': counts.get('error', 0),
        'captcha_today': counts.get('captcha', 0),
        'report_timezone': 'Europe/Moscow',
    }
