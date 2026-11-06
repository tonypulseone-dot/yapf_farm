import json,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import database as db
from app.farm_health import farm_health,record_warm_event,initialize_health_schema
NOW=datetime(2026,11,6,12,tzinfo=timezone.utc)

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',str(tmp_path/'health.db'));db.init_db()
    db.set_setting('is_running','1');db.set_setting('proxy_list','host:1234:user:private-value')

def profile(success=NOW,cycles=1):
    c=db.get_db();pid=c.execute("INSERT INTO profiles(name,user_data_dir,fingerprint,status,cycles_completed,last_warm_success_at,last_warmed_at) VALUES('prof_1_1234','offline','{}','ready',?,?,?)",(cycles,success.strftime('%Y-%m-%d %H:%M:%S') if success else None,NOW.strftime('%Y-%m-%d %H:%M:%S'))).lastrowid;c.commit();c.close();return pid

def test_stale_success_is_not_hidden_by_a_recent_failed_attempt():
    pid=profile(NOW-timedelta(hours=72));c=db.get_db();record_warm_event(c,pid,False,NOW);c.commit();c.close()
    h=farm_health(NOW,100)
    assert h['pool']['stale']==1
    assert any(t['id']=='stale' for t in h['advice'])
    assert h['warming_24h']['successful_cycles']==0

def test_low_volume_does_not_trigger_proxy_or_captcha_alarm():
    profile();c=db.get_db()
    for status in ['error','captcha']:
        c.execute("INSERT INTO click_logs(keyword,target_url,domain,status,details,timestamp) VALUES('q','','test',?,'ERR_PROXY_CONNECTION_FAILED',?)",(status,NOW.strftime('%Y-%m-%d %H:%M:%S')))
    c.commit();c.close();h=farm_health(NOW,100)
    assert not any(t['id'] in ('network','captcha') for t in h['advice'])

def test_error_rates_produce_actionable_advice_without_exposing_keys():
    profile();db.set_setting('key_capify','never-expose-key');c=db.get_db()
    for i in range(10):
        c.execute("INSERT INTO click_logs(keyword,target_url,domain,status,details,timestamp) VALUES('q','','test',?,'ERR_TUNNEL_CONNECTION_FAILED',?)",('error' if i<5 else 'captcha',NOW.strftime('%Y-%m-%d %H:%M:%S')))
    c.commit();c.close();h=farm_health(NOW,100)
    assert {t['id'] for t in h['advice']} >= {'network','captcha'}
    assert h['last_hour']['network_errors']==5
    assert 'private-value' not in json.dumps(h) and 'never-expose-key' not in json.dumps(h)

def test_backfill_imports_only_confirmed_success_and_is_idempotent(tmp_path):
    pid=profile(None);path=tmp_path/'service.log';local=NOW.astimezone().strftime('%Y-%m-%d %H:%M:%S')
    path.write_text(local+",123 [INFO] warmer: [prof_1_1234] Standard 'Boost & Low Captcha' cycle completed successfully!\n"+local+",123 [WARNING] warmer: [prof_1_1234] cycle failed\n")
    c=db.get_db();c.execute("DELETE FROM settings WHERE key='health_log_backfill_completed'")
    initialize_health_schema(c,path,NOW);initialize_health_schema(c,path,NOW);c.commit()
    assert c.execute('SELECT count(*) FROM warming_events').fetchone()[0]==1
    assert c.execute('SELECT last_warm_success_at FROM profiles').fetchone()[0]=='2026-11-06 12:00:00';c.close()
    assert farm_health(NOW,100)['warming_24h']['successful_cycles']==1

def test_disk_warning_has_priority_and_paused_farm_is_not_starting_itself():
    profile();db.set_setting('is_running','0');h=farm_health(NOW,3)
    assert h['state']=='critical' and h['advice'][0]['id']=='disk'
    assert any(t['id']=='paused' for t in h['advice'])
    assert db.get_setting('is_running')=='0'

def test_success_event_updates_real_freshness_once():
    pid=profile(None);c=db.get_db();record_warm_event(c,pid,True,NOW);record_warm_event(c,pid,True,NOW);c.commit();c.close()
    h=farm_health(NOW,100)
    assert h['pool']['unknown_freshness']==0 and h['warming_24h']['successful_cycles']==1


@pytest.mark.parametrize('outcome',[True,False,'exception'])
def test_worker_records_success_failure_and_unexpected_failure(outcome):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock,patch
    from app.engine import orchestrator as orch
    pid=profile(None);db.set_setting('thread_launch_interval_sec','0')
    value={'id':pid,'name':'prof_1_1234','status':'ready','user_data_dir':'offline','device_type':'android','cycles_completed':1,'fingerprint':json.dumps({'viewport':{'width':390,'height':844},'user_agent':'test'})}
    class PW:
        async def __aenter__(self):return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=AsyncMock(return_value=SimpleNamespace(pages=[None],close=AsyncMock()))))
        async def __aexit__(self,*args):pass
    async def run():
        o=orch.TaskOrchestrator()
        warmer=AsyncMock(side_effect=RuntimeError('offline failure')) if outcome=='exception' else AsyncMock(return_value=outcome)
        with patch.object(o,'get_or_create_profile',new=AsyncMock(return_value=value)),patch.object(orch,'has_pending_keyword',return_value=False),patch.object(orch,'async_playwright',return_value=PW()),patch.object(orch.stealth_runner,'apply_stealth_async',new=AsyncMock()),patch.object(orch,'run_warming_cycle',new=warmer):
            await o.execute_worker_task()
        assert not o.busy_profiles and o.stats['active_threads']==0
    asyncio.run(run())
    c=db.get_db();events=c.execute('SELECT status FROM warming_events').fetchall();last=c.execute('SELECT last_warm_success_at FROM profiles').fetchone()[0];c.close()
    assert len(events)==1 and events[0][0]==('success' if outcome is True else 'error')
    assert bool(last)==(outcome is True)
