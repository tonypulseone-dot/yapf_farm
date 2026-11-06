"""Evidence-based farm advice; never changes settings or starts tasks."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import re
from .database import get_db
from .reporting import utc_now,utc_text

CAPTCHA_API_CONNECTED = False  # Current promotion path only handles the checkbox.


def initialize_health_schema(conn, log_path=None, now=None):
    now=now or utc_now()
    columns={r['name'] for r in conn.execute('PRAGMA table_info(profiles)')}
    if 'last_warm_success_at' not in columns:
        conn.execute('ALTER TABLE profiles ADD COLUMN last_warm_success_at TEXT')
    conn.execute('''CREATE TABLE IF NOT EXISTS warming_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL,
        finished_at TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'live',
        UNIQUE(profile_id,finished_at,status))''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_warming_time_status ON warming_events(finished_at,status)')
    conn.execute('DELETE FROM warming_events WHERE finished_at<?',(utc_text(now-timedelta(days=30)),))
    marker=conn.execute("SELECT value FROM settings WHERE key='health_log_backfill_completed'").fetchone()
    if marker:return
    profiles={r['name']:r['id'] for r in conn.execute('SELECT id,name FROM profiles')}
    path=Path(log_path) if log_path else Path(__file__).resolve().parents[1]/'logs/service.log'
    latest={};events=[]
    pattern=re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*\[INFO\] warmer: \[(prof_\d+_\d+)\].*cycle completed successfully!')
    if path.exists():
        with path.open(encoding='utf8',errors='replace') as f:
            for line in f:
                match=pattern.search(line)
                if not match or match[2] not in profiles:continue
                # The existing Python log formatter uses the host's local time.
                stamp=datetime.fromisoformat(match[1]).astimezone(timezone.utc)
                if stamp>now+timedelta(minutes=5):continue
                pid=profiles[match[2]];text=utc_text(stamp)
                latest[pid]=max(latest.get(pid,text),text)
                if stamp>=now-timedelta(hours=24):events.append((pid,text,'success','log_import'))
    conn.executemany('UPDATE profiles SET last_warm_success_at=? WHERE id=? AND (last_warm_success_at IS NULL OR last_warm_success_at<?)',[(stamp,pid,stamp) for pid,stamp in latest.items()])
    conn.executemany('INSERT OR IGNORE INTO warming_events(profile_id,finished_at,status,source) VALUES(?,?,?,?)',events)
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('health_log_backfill_completed',?)",(utc_text(now),))


def record_warm_event(conn, profile_id, success, now=None, status=None):
    stamp=utc_text(now or utc_now());state=status or ('success' if success else 'error')
    conn.execute('INSERT OR IGNORE INTO warming_events(profile_id,finished_at,status,source) VALUES(?,?,?,?)',(profile_id,stamp,state,'live'))
    if success:conn.execute('UPDATE profiles SET last_warm_success_at=? WHERE id=?',(stamp,profile_id))


def farm_health(now=None, disk_free_gb=None):
    now=now or utc_now();hour=utc_text(now-timedelta(hours=1));day=utc_text(now-timedelta(hours=24))
    stale=utc_text(now-timedelta(hours=48));conn=get_db()
    try:
        settings=dict(conn.execute('SELECT key,value FROM settings'))
        rows=conn.execute('''SELECT count(*) total,
            sum(status='ready') ready, sum(cycles_completed=0) never_warmed,
            sum(last_warm_success_at IS NULL) unknown_freshness,
            sum(last_warm_success_at IS NOT NULL AND last_warm_success_at<?) stale,
            max(last_warm_success_at) latest_success FROM profiles''',(stale,)).fetchone()
        pool={k:rows[k] or 0 for k in rows.keys()}
        pool['latest_success']=rows['latest_success']
        counts=dict(conn.execute('SELECT status,count(*) FROM click_logs WHERE timestamp>=? GROUP BY status',(hour,)))
        proxy_errors=conn.execute('''SELECT count(*) FROM click_logs WHERE timestamp>=? AND status='error'
            AND (upper(details) LIKE '%ERR_PROXY%' OR upper(details) LIKE '%ERR_TUNNEL%'
             OR upper(details) LIKE '%ERR_CONNECTION%' OR upper(details) LIKE '%ERR_EMPTY_RESPONSE%' OR upper(details) LIKE '%TIMEOUT%')''',(hour,)).fetchone()[0]
        warm=conn.execute("SELECT count(*),count(distinct profile_id) FROM warming_events WHERE finished_at>=? AND status='success'",(day,)).fetchone()
        live=dict(conn.execute("SELECT status,count(*) FROM warming_events WHERE finished_at>=? AND source='live' GROUP BY status",(hour,)))
        active=conn.execute('SELECT count(*) FROM projects WHERE is_active=1').fetchone()[0]
    finally:conn.close()
    attempts=sum(counts.values());captcha=counts.get('captcha',0);successes=counts.get('success',0)
    is_running=settings.get('is_running')=='1';proxies=len([l for l in settings.get('proxy_list','').splitlines() if l.strip()])
    advice=[]
    def tip(id,severity,title,detail,action):advice.append(dict(id=id,severity=severity,title=title,detail=detail,action=action))
    if not pool['total']:
        tip('empty','warning','Пул профилей пуст','Проверьте создание профилей и настройки нагула.','warming')
    elif pool['ready'] < pool['total'] * .2:
        tip('low_ready','warning',f"Мало готовых профилей: {pool['ready']} из {pool['total']}",'Меньше 20% пула готово к использованию; проверьте состояние нагула.','warming')
    if pool['stale']:
        tip('stale','warning',f"Догреть профили: {pool['stale']}",'Последний подтверждённый успешный нагул был более 48 часов назад.','warming')
    if pool['never_warmed']:
        tip('first_warm','warning',f"Нужен первый нагул: {pool['never_warmed']}",'Эти профили ещё не завершили ни одного успешного цикла.','warming')
    if not is_running:
        tip('paused','info','Ферма остановлена','Новые сессии нагула и ПФ сейчас не выдаются. Проверьте, запланирована ли эта пауза.','engine')
    if not proxies:
        tip('no_proxy','warning','Прокси не заданы','Браузеры могут работать напрямую. Проверьте, выбран ли нужный режим подключения.','proxy')
    if attempts>=10 and captcha/attempts>=.2:
        detail=f'За час: {captcha} из {attempts} попыток.'
        if not CAPTCHA_API_CONNECTED:detail+=' Полное решение через API пока не подключено; сохранённого ключа недостаточно.'
        tip('captcha','warning','Много CAPTCHA',detail,'captcha')
    if attempts>=10 and proxy_errors>=5 and proxy_errors/attempts>=.2:
        tip('network','warning','Проверить прокси и соединение',f'За час: {proxy_errors} сетевых сбоев из {attempts} попыток. Причина может быть в прокси или доступности сайтов.','proxy')
    live_attempts=sum(n for status,n in live.items() if status!='cancelled')
    if live_attempts>=10 and live.get('error',0)/live_attempts>=.25:
        tip('warming_errors','warning','Нагул часто не завершается',f"Ошибок за час: {live.get('error',0)} из {live_attempts} новых циклов. Проверьте журнал.",'logs')
    if active and attempts>=10 and not successes and not any(t['id'] in ('captcha','network') for t in advice):
        tip('no_clicks','warning','Нет успешных переходов',f'За час выполнено {attempts} попыток, но целевой переход не подтверждён.','logs')
    if not active and is_running:
        tip('promo_paused','info','ПФ на паузе, нагул может продолжаться','Проекты не продвигаются. Профили поддерживаются, пока включена сама ферма.','projects')
    if pool['unknown_freshness']:
        tip('unknown','info',f"Нет даты успешного нагула: {pool['unknown_freshness']}",'Актуальность не подтверждена. Данные появятся после успешных циклов.','warming')
    if disk_free_gb is None:
        import shutil
        disk_free_gb=shutil.disk_usage(Path(__file__).resolve().parents[1]).free/1024**3
    if disk_free_gb<5:tip('disk','critical','Мало места на диске',f'Свободно {disk_free_gb:.1f} ГБ. Это может мешать сохранению профилей и базы.','logs')
    if not advice:tip('healthy','ok','Критичных сигналов не найдено','Профили регулярно обновляются; продолжайте наблюдать за результатами и ошибками.','logs')
    priority={'critical':0,'warning':1,'info':2,'ok':3}
    focus={'disk':0,'no_proxy':1,'network':2,'captcha':3,'stale':4,'first_warm':5,'low_ready':6}
    advice.sort(key=lambda t:(priority[t['severity']],focus.get(t['id'],10)))
    state='critical' if any(t['severity']=='critical' for t in advice) else 'warning' if any(t['severity']=='warning' for t in advice) else 'ok'
    return {'state':state,'checked_at':utc_text(now),'pool':pool,
            'warming_24h':{'successful_cycles':warm[0],'unique_profiles':warm[1]},
            'last_hour':{'attempts':attempts,'successes':successes,'captcha':captcha,'network_errors':proxy_errors},
            'proxy_lines':proxies,'active_projects':active,'is_running':is_running,
            'advice':advice[:3],'advice_total':len(advice),'rules':{'stale_hours':48,'minimum_attempts':10,'high_error_rate':.2}}
