import asyncio,json,sys
from pathlib import Path
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import database as db
from app.position_history import position_timeline
from app.engine import position_checker as pc
from app.main import app  # Collect application globals before asyncio.run closes test loops.

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',str(tmp_path/'positions.db'));db.init_db()

def seed(name='One',reg='213'):
    c=db.get_db();pid=c.execute('INSERT INTO projects(name,domain) VALUES(?,?)',(name,name.lower()+'.test')).lastrowid
    kid=c.execute('INSERT INTO keywords(project_id,keyword,geo_region) VALUES(?,?,?)',(pid,'query',reg)).lastrowid;c.commit();c.close();return pid,kid

def sample(pid,kid,position,stamp,region='213',device='desktop',source='checker'):
    c=db.get_db();c.execute('INSERT INTO positions_history(project_id,keyword_id,keyword,domain,region,device_type,position,checked_at,source) VALUES(?,?,?,?,?,?,?,?,?)',(pid,kid,'query','one.test',region,device,position,stamp,source));c.commit();c.close()

NOW=datetime(2026,11,6,12,tzinfo=timezone.utc)

def test_daily_latest_and_deltas_do_not_mix_regions_devices_or_promo():
    p,k=seed();sample(p,k,21,'2026-11-04 12:00:00');sample(p,k,28,'2026-11-05 10:00:00');sample(p,k,30,'2026-11-05 12:00:00');sample(p,k,27,'2026-11-06 10:00:00');sample(p,k,99,'2026-11-06 11:00:00',device='android');sample(p,k,1,'2026-11-06 11:00:00',source='promo');sample(p,k,7,'2026-11-06 11:00:00',region='2')
    t=position_timeline(p,7,now=NOW);r=next(r for r in t['rows'] if r['region']=='213')
    assert r['cells']['2026-11-06']['position']==27
    assert r['cells']['2026-11-06']['diff']==3
    assert r['cells']['2026-11-05']['position']==30
    assert r['cells']['2026-11-05']['diff']==-9
    assert '2026-11-03' not in r['cells']
    assert len(t['rows'])==2

def test_moscow_day_and_missing_day_do_not_fabricate_a_rank():
    p,k=seed();sample(p,k,0,'2026-11-04 21:01:00');sample(p,k,9,'2026-11-05 21:02:00')
    r=position_timeline(p,7,now=NOW)['rows'][0]
    assert r['cells']['2026-11-05']['position']==0
    assert r['cells']['2026-11-06']['direction']=='entered'
    assert r['cells']['2026-11-06']['diff'] is None

def test_two_projects_run_concurrently_and_same_project_cannot_start_twice():
    p1,_=seed();p2,_=seed('Two')
    async def run():
        m=pc.PositionCheckerManager();started=set();gate=asyncio.Event()
        async def check(kw,proj,*args,**kwargs):
            started.add(proj['id']);await gate.wait();return {'status':'ok','position':12}
        m.check_single_keyword=check
        a,b=await asyncio.gather(m.start_project_check(p1),m.start_project_check(p2))
        assert a['status']==b['status']=='ok'
        await asyncio.sleep(0)
        assert started=={p1,p2}
        assert (await m.start_project_check(p1))['status']=='busy'
        assert m.get_progress(p1)['project_id']==p1 and m.get_progress(p2)['project_id']==p2
        gate.set();await asyncio.gather(*m._tasks.values())
        assert all(j['current']==1 and not j['is_running'] for j in m.jobs.values())
    asyncio.run(run())

def test_browser_concurrency_is_bounded_for_manual_checks():
    async def run():
        m=pc.PositionCheckerManager();active=0;peak=0
        async def impl(*args):
            nonlocal active,peak
            active+=1;peak=max(peak,active);await asyncio.sleep(.01);active-=1;return {'status':'ok'}
        m._check_single_keyword_impl=impl
        await asyncio.gather(*(m.check_single_keyword({}, {}) for _ in range(6)))
        assert peak==2
    asyncio.run(run())

def test_duplicate_region_rows_do_not_multiply_project_plan():
    p,k=seed();c=db.get_db();c.execute("INSERT INTO keywords(project_id,keyword,geo_region) VALUES(?,'query','2')",(p,));c.commit();c.close()
    async def run():
        m=pc.PositionCheckerManager();m.check_single_keyword=AsyncMock(return_value={'status':'captcha','details':'blocked'})
        with patch.object(pc.asyncio,'sleep',new=AsyncMock()):
            await m.check_project_positions(p,regions=['213','2'])
        assert m.jobs[p]['total']==2 and m.jobs[p]['errors']==2
        assert m.jobs[p]['status']=='completed_with_errors'
        c=db.get_db();assert c.execute('SELECT COUNT(*) FROM positions_history').fetchone()[0]==0;c.close()
    asyncio.run(run())

@pytest.mark.parametrize('captcha',[False,True])
def test_failed_navigation_or_captcha_does_not_record_false_outside_top(captcha):
    async def run():
        page=SimpleNamespace(goto=AsyncMock(side_effect=None if captcha else TimeoutError()),url='https://ya.ru/showcaptcha' if captcha else 'https://ya.ru/search',query_selector=AsyncMock(return_value=True))
        browser=SimpleNamespace(new_context=AsyncMock(return_value=SimpleNamespace(new_page=AsyncMock(return_value=page))),close=AsyncMock())
        class PW:
            async def __aenter__(self):return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
            async def __aexit__(self,*args):pass
        with patch.object(pc,'async_playwright',return_value=PW()),patch.object(pc,'record_position') as record,patch.object(pc,'handle_yandex_checkbox_captcha',new=AsyncMock(return_value=True)),patch.object(pc.asyncio,'sleep',new=AsyncMock()):
            r=await pc.PositionCheckerManager().check_single_keyword({'id':1,'keyword':'test'},{'id':1,'domain':'one.test'})
            assert r['status']==('captcha' if captcha else 'error');record.assert_not_called();browser.close.assert_awaited_once()
    asyncio.run(run())


def test_timeline_api_and_csv_keep_dates_and_no_measurement_distinct():
    from fastapi.testclient import TestClient
    from app.main import AUTH_COOKIE,AUTH_SECRET
    p,k=seed();sample(p,k,9,'2026-11-06 11:00:00')
    client=TestClient(app);client.cookies.set(AUTH_COOKIE,AUTH_SECRET)
    from unittest.mock import patch
    with patch('app.position_history.utc_now',return_value=NOW):
        result=client.get(f'/api/positions/timeline?project_id={p}&days=7').json()
        assert len(result['dates'])==7
        assert result['rows'][0]['cells']['2026-11-06']['position']==9
        csv=client.get(f'/api/positions/timeline-export?project_id={p}&days=7')
        assert csv.status_code==200 and '06.11.2026' in csv.text and ';9;' in csv.text
        kpi=client.get(f'/api/positions/kpi?project_id={p}').json()
        assert kpi['top_10']==1 and kpi['out_of_top']==0


def test_unmeasured_query_is_not_counted_as_outside_top():
    from app.position_history import timeline_kpi
    p,k=seed();kpi=timeline_kpi(position_timeline(p,7,now=NOW))
    assert kpi['unmeasured']==1 and kpi['out_of_top']==0 and kpi['tracked_queries']==0


def test_url_search_uses_latest_measured_destination():
    p,k=seed();sample(p,k,9,'2026-11-06 11:00:00')
    c=db.get_db();c.execute("UPDATE positions_history SET target_url='https://one.test/current-page'");c.commit();c.close()
    result=position_timeline(p,7,search='current-page',now=NOW)
    assert len(result['rows'])==1
