import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import database as db
from app.dispatch import reserve_keyword, finish_attempt, has_pending_keyword
from app.reporting import business_date, day_bounds, daily_log_metrics, utc_text, utc_now
from app.engine import promo

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
PROFILE = {'id': 1, 'name': 'test-profile', 'device_type': 'android'}


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path/'test.db'))
    db.init_db()
    db.set_setting('telegram_notifications_enabled', '0')
    db.set_setting('telegram_bot_token', 'not-a-real-token')


def seed(n=50, goal=15):
    c=db.get_db()
    p=c.execute("INSERT INTO projects(domain,name,is_active) VALUES('example.test','Test',1)").lastrowid
    for i in range(n):
        c.execute('INSERT INTO keywords(project_id,keyword,daily_goal) VALUES(?,?,?)', (p,f'query-{i}',goal))
    c.commit();c.close();return p


def finish(task, status='not_found', now=NOW):
    return finish_attempt(task,PROFILE,{'status':status,'position':1 if status=='success' else 0,
                          'navigation_verified':status=='success','details':'test'},now)


def test_fifty_keys_are_all_claimed_before_any_repeat():
    seed()
    db.set_setting('keyword_min_interval_sec',0)
    first=[]
    for _ in range(50):
        t=reserve_keyword(NOW);first.append(t['id']);finish(t)
    assert len(set(first))==50
    second=[]
    for _ in range(50):
        t=reserve_keyword(NOW);second.append(t['id']);finish(t)
    assert set(first)==set(second)
    assert first != sorted(first)
    assert first != second


def test_concurrent_workers_cannot_claim_same_key():
    seed(50)
    with ThreadPoolExecutor(max_workers=25) as pool:
        tasks=list(pool.map(lambda _:reserve_keyword(NOW), range(25)))
    assert len({t['id'] for t in tasks})==25
    with ThreadPoolExecutor(max_workers=25) as pool:
        tasks+=list(pool.map(lambda _:reserve_keyword(NOW), range(25)))
    assert len({t['id'] for t in tasks})==50
    assert reserve_keyword(NOW) is None


def test_failure_moves_to_next_key_and_obeys_twenty_minute_gap():
    seed(2)
    first=reserve_keyword(NOW);finish(first,'error')
    second=reserve_keyword(NOW);finish(second,'not_found')
    assert first['id']!=second['id']
    assert not has_pending_keyword(NOW+timedelta(minutes=19))
    assert reserve_keyword(NOW+timedelta(minutes=19)) is None
    assert reserve_keyword(NOW+timedelta(minutes=20)) is not None


def test_daily_goal_one_and_idempotent_finalization():
    seed(1,1)
    t=reserve_keyword(NOW)
    assert reserve_keyword(NOW) is None
    assert finish(t,'success')
    assert not finish(t,'success')
    assert reserve_keyword(NOW+timedelta(minutes=40)) is None
    c=db.get_db()
    assert c.execute('SELECT COUNT(*) FROM click_logs').fetchone()[0]==1
    assert c.execute('SELECT clicks_today FROM keywords').fetchone()[0]==1
    c.close()


def test_durable_lease_and_new_day():
    seed(1,1)
    t=reserve_keyword(NOW)
    # A new caller/process sees the same DB reservation, not an empty Python set.
    assert reserve_keyword(NOW+timedelta(minutes=20)) is None
    replacement=reserve_keyword(NOW+timedelta(minutes=33))
    assert replacement and replacement['_attempt_id']!=t['_attempt_id']
    assert not finish(t,'success',NOW+timedelta(minutes=34))
    assert finish(replacement,'success',NOW+timedelta(minutes=34))
    assert reserve_keyword(NOW+timedelta(days=1)) is not None


def test_paused_project_not_selected():
    p=seed(3)
    c=db.get_db();c.execute('UPDATE projects SET is_active=0 WHERE id=?',(p,));c.commit();c.close()
    assert not has_pending_keyword(NOW)
    assert reserve_keyword(NOW) is None


def test_unverified_success_is_an_error():
    seed(1)
    t=reserve_keyword(NOW)
    assert finish_attempt(t,PROFILE,{'status':'success','position':1},NOW)
    c=db.get_db()
    assert c.execute('SELECT status FROM click_logs').fetchone()[0]=='error'
    assert c.execute('SELECT clicks_today FROM keywords').fetchone()[0]==0
    c.close()


def test_moscow_day_boundary_and_totals():
    now=datetime(2026,11,5,21,5,tzinfo=timezone.utc)
    assert business_date(now)=='2026-11-06'
    assert day_bounds(now)==('2026-11-05 21:00:00','2026-11-06 21:00:00')
    c=db.get_db()
    for stamp,status in [('2026-11-05 20:59:59','success'),('2026-11-05 21:00:00','success'),
                         ('2026-11-05 21:01:00','not_found'),('2026-11-05 21:02:00','error')]:
        c.execute("INSERT INTO click_logs(keyword,target_url,domain,status,timestamp) VALUES('q','','example.test',?,?)",(status,stamp))
    c.commit();m=daily_log_metrics(c,now);c.close()
    assert m['clicks_today']==1
    assert m['attempts_today']==3
    assert m['not_found_today']==1
    assert m['errors_today']==1


def test_api_reflects_success_and_failures_without_reloading_process():
    from app.main import app,AUTH_COOKIE,AUTH_SECRET
    from fastapi.testclient import TestClient
    p=seed(2)
    c=TestClient(app)
    c.cookies.set(AUTH_COOKIE,AUTH_SECRET)
    before=c.get('/api/status').json()
    assert before['clicks_today']==0
    now=utc_now()
    finish(reserve_keyword(now),'success',now)
    finish(reserve_keyword(now),'not_found',now)
    after=c.get('/api/status').json()
    assert after['clicks_today']==1 and after['attempts_today']==2
    assert after['not_found_today']==1
    rows=c.get('/api/projects').json()[0]['keywords']
    assert sum(k['clicks_today'] for k in rows)==1
    assert sum(k['attempts_today'] for k in rows)==2
    assert sum(k['not_found_today'] for k in rows)==1
    logs=c.get('/api/logs').json()
    assert {r['status'] for r in logs}=={'success','not_found'}


def test_migration_is_idempotent_and_preserves_history():
    seed(1)
    c=db.get_db()
    c.execute("INSERT INTO click_logs(keyword,target_url,domain,status) VALUES('query-0','','example.test','not_found')")
    c.commit();c.close()
    db.init_db();db.init_db()
    c=db.get_db()
    assert c.execute('SELECT COUNT(*) FROM click_logs').fetchone()[0]==1
    assert c.execute('SELECT keyword_id FROM click_logs').fetchone()[0] is not None
    assert c.execute('SELECT COUNT(*) FROM keywords').fetchone()[0]==1
    c.close()


def test_settings_gap_validation():
    from app.main import app,AUTH_COOKIE,AUTH_SECRET
    from fastapi.testclient import TestClient
    c=TestClient(app);c.cookies.set(AUTH_COOKIE,AUTH_SECRET)
    assert c.post('/api/settings',json={'keyword_min_interval_sec':'bad'}).status_code==422
    assert c.post('/api/settings',json={'keyword_min_interval_sec':-1}).status_code==422
    assert c.post('/api/settings',json={'keyword_min_interval_sec':1200}).status_code==200


def test_recent_legacy_attempt_keeps_cooldown_after_upgrade():
    seed(1)
    c=db.get_db()
    k=c.execute('SELECT id FROM keywords').fetchone()[0]
    c.execute("INSERT INTO click_logs(keyword,target_url,domain,status,timestamp,keyword_id) VALUES('query-0','','example.test','not_found',?,?)",(utc_text(NOW),k))
    c.commit();c.close()
    assert reserve_keyword(NOW+timedelta(minutes=19)) is None
    assert reserve_keyword(NOW+timedelta(minutes=20)) is not None


@pytest.mark.parametrize('url,expected',[
    ('https://example.test/page',True),('https://www.example.test',True),
    ('https://evil.test/?q=example.test',False),('https://example.test.evil.test',False),
    ('https://notexample.test',False),('about:blank',False)])
def test_exact_target_host(url,expected):
    assert promo.target_host_matches(url,'example.test') is expected


def page_fixture(fail_click=False,popup=False):
    target=SimpleNamespace(url='https://example.test/item',is_closed=lambda:False,
                           wait_for_url=AsyncMock(),wait_for_load_state=AsyncMock(),close=AsyncMock())
    link=SimpleNamespace(get_attribute=AsyncMock(return_value='https://example.test/item'),
                         inner_text=AsyncMock(return_value='Example'),bounding_box=AsyncMock(return_value=None),
                         click=AsyncMock(side_effect=TimeoutError() if fail_click else None))
    item=SimpleNamespace(query_selector=AsyncMock(return_value=link))
    class Popup:
        async def __aenter__(self):
            async def ready():return target
            return SimpleNamespace(value=ready()) if popup else None
        async def __aexit__(self,exc_type,*args):
            if not popup and exc_type is None:raise TimeoutError('no popup')
            return False
    page=SimpleNamespace(url='https://example.test/item',goto=AsyncMock(),query_selector=AsyncMock(return_value=None),
                         query_selector_all=AsyncMock(return_value=[item]),expect_popup=lambda **kw:Popup(),
                         wait_for_url=AsyncMock(),wait_for_load_state=AsyncMock(),is_closed=lambda:False)
    return page


@pytest.mark.parametrize('popup',[False,True])
def test_verified_same_tab_and_popup_success(popup):
    async def run():
        page=page_fixture(popup=popup)
        with patch.object(asyncio,'sleep',new=AsyncMock()),patch.object(promo.random,'uniform',return_value=0):
            r=await promo._search_and_click(page,'query','213','example.test','test')
        assert r['status']=='success' and r['navigation_verified']
    asyncio.run(run())


def test_click_exception_is_not_a_success():
    async def run():
        page=page_fixture(fail_click=True)
        with patch.object(asyncio,'sleep',new=AsyncMock()),patch.object(promo.random,'uniform',return_value=0):
            r=await promo._search_and_click(page,'query','213','example.test','test')
        assert r['status']=='error'
    asyncio.run(run())


def test_unconfirmed_navigation_is_not_a_success():
    async def run():
        page=page_fixture();page.url='https://ya.ru/search/'
        page.wait_for_url=AsyncMock(side_effect=TimeoutError())
        with patch.object(asyncio,'sleep',new=AsyncMock()),patch.object(promo.random,'uniform',return_value=0):
            r=await promo._search_and_click(page,'query','213','example.test','test')
        assert r['status']=='error'
    asyncio.run(run())


def test_no_ready_profile_does_not_consume_keyword_turn():
    from app.engine.orchestrator import TaskOrchestrator
    seed(2);db.set_setting('is_running',1)
    async def run():
        o=TaskOrchestrator()
        with patch.object(o,'get_or_create_profile',new=AsyncMock(return_value=None)),patch.object(asyncio,'sleep',new=AsyncMock()):
            await o.execute_worker_task()
    asyncio.run(run())
    c=db.get_db();assert c.execute('SELECT COUNT(*) FROM keyword_attempts').fetchone()[0]==0;c.close()


def test_worker_failure_releases_claim_and_records_error():
    from app.engine import orchestrator as orch
    seed(1);db.set_setting('is_running',1)
    profile={**PROFILE,'status':'ready','fingerprint':json.dumps({'viewport':{'width':390,'height':844},'user_agent':'test'})}
    async def run():
        o=orch.TaskOrchestrator()
        with patch.object(o,'get_or_create_profile',new=AsyncMock(return_value=profile)),patch.object(orch,'async_playwright',side_effect=RuntimeError('offline fixture')):
            await o.execute_worker_task()
        assert not o.busy_profiles and o.stats['active_threads']==0
    asyncio.run(run())
    c=db.get_db()
    assert c.execute("SELECT COUNT(*) FROM keyword_attempts WHERE status='reserved'").fetchone()[0]==0
    assert c.execute('SELECT status FROM click_logs').fetchone()[0]=='error'
    c.close()


def test_pause_keeps_running_profile_locked():
    from app.engine.orchestrator import TaskOrchestrator
    async def run():
        o=TaskOrchestrator();o.busy_profiles={7};o.stats['active_threads']=1
        task=asyncio.create_task(asyncio.Event().wait());o.active_tasks={task}
        async def stop(*a):raise asyncio.CancelledError()
        with patch.object(asyncio,'sleep',side_effect=stop):
            try:await o.main_loop()
            except asyncio.CancelledError:pass
        assert o.busy_profiles=={7} and o.stats['active_threads']==1
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
    asyncio.run(run())


@pytest.mark.parametrize('status', ['captcha', 'error'])
def test_brand_retry_preserves_failure_status(status):
    async def run():
        failure={'status':status,'position':0,'details':'blocked or disconnected'}
        with patch.object(promo,'_search_and_click',new=AsyncMock(side_effect=[{'status':'not_found'},failure])):
            result=await promo.run_promotion_click(None,{'keyword':'test','domain':'example.test','brand_tail':'brand'},'test')
        assert result == failure
    asyncio.run(run())


def test_browser_launch_interval_applies_across_replacements():
    from app.engine import orchestrator as orch
    seed(3);db.set_setting('is_running',1);db.set_setting('thread_launch_interval_sec','0.04')
    profile={**PROFILE,'status':'ready','fingerprint':json.dumps({'viewport':{'width':390,'height':844},'user_agent':'test'})}
    async def run():
        o=orch.TaskOrchestrator();starts=[]
        def launch():
            starts.append(asyncio.get_running_loop().time())
            raise RuntimeError('offline launch fixture')
        with patch.object(o,'get_or_create_profile',new=AsyncMock(return_value=profile)),patch.object(orch,'async_playwright',side_effect=launch):
            await asyncio.gather(o.execute_worker_task(),o.execute_worker_task())
            await o.execute_worker_task()
        assert len(starts)==3
        assert all(b-a >= .035 for a,b in zip(starts,starts[1:]))
    asyncio.run(run())


def test_empty_search_document_is_not_domain_absence():
    async def run():
        page=page_fixture();page.query_selector_all=AsyncMock(return_value=[])
        with patch.object(asyncio,'sleep',new=AsyncMock()):
            result=await promo._search_and_click(page,'query','213','example.test','test')
        assert result['status']=='error'
    asyncio.run(run())


def test_checkbox_click_does_not_prove_captcha_was_resolved():
    async def run():
        page=page_fixture();page.url='https://ya.ru/showcaptcha'
        with patch.object(asyncio,'sleep',new=AsyncMock()),patch('app.engine.captcha.handle_yandex_checkbox_captcha',new=AsyncMock(return_value=True)):
            result=await promo._search_and_click(page,'query','213','example.test','test')
        assert result['status']=='captcha'
        page.query_selector_all.assert_not_awaited()
    asyncio.run(run())


def seed_ready_profiles(n=3):
    c=db.get_db()
    for i in range(n):
        c.execute("INSERT INTO profiles(name,user_data_dir,fingerprint,status,device_type) VALUES(?,?,?,'ready','android')",(f'p{i}',f'offline-{i}','{}'))
    c.commit();c.close()


def test_ready_profile_claims_rotate_without_success_and_respect_cooldown():
    from app.engine.orchestrator import TaskOrchestrator
    seed_ready_profiles(20)
    async def run():
        o=TaskOrchestrator()
        profiles=await asyncio.gather(*(o.get_or_create_profile('ready') for _ in range(20)))
        assert len({p['id'] for p in profiles})==20
        assert await o.get_or_create_profile('ready') is None
        c=db.get_db()
        assert c.execute('SELECT count(*) FROM profiles WHERE last_used_at IS NOT NULL AND promo_available_at > CURRENT_TIMESTAMP').fetchone()[0]==20
        c.close()
    asyncio.run(run())


@pytest.mark.parametrize('status',['error','not_found','captcha','cancelled','success'])
def test_every_attempt_updates_profile_use_and_cooldown(status):
    seed(1);seed_ready_profiles(1)
    task=reserve_keyword(NOW)
    finish(task,status,NOW)
    c=db.get_db();row=c.execute('select * from profiles').fetchone();c.close()
    assert row['last_used_at']==utc_text(NOW)
    delay=3600 if status=='captcha' else 1200
    assert row['promo_available_at']==utc_text(NOW+timedelta(seconds=delay))
    assert row['total_clicks']==(1 if status=='success' else 0)


def test_history_migration_recovers_failed_profile_use_without_clicks():
    seed_ready_profiles(1)
    c=db.get_db()
    c.execute("INSERT INTO click_logs(keyword,target_url,domain,status,profile_name,timestamp) VALUES('q','','example.test','error','p0',?)",(utc_text(NOW),))
    c.commit();c.close();db.init_db();db.init_db()
    c=db.get_db();row=c.execute('select * from profiles').fetchone()
    assert row['last_used_at']==utc_text(NOW)
    assert row['promo_available_at']==utc_text(NOW+timedelta(minutes=20))
    assert row['total_clicks']==0
    assert c.execute('select count(*) from click_logs').fetchone()[0]==1
    c.close()


def test_warming_does_not_reopen_profile_during_promo_cooldown():
    from app.engine.orchestrator import TaskOrchestrator
    seed_ready_profiles(1);db.set_setting('target_profiles_count',1)
    c=db.get_db();c.execute("UPDATE profiles SET promo_available_at=datetime('now','+1 hour')");c.commit();c.close()
    async def run():
        assert await TaskOrchestrator().get_or_create_profile('warming') is None
    asyncio.run(run())
