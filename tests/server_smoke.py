"""Offline smoke test on the deployment's Python; never starts the application."""
import ast
import asyncio
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.dont_write_bytecode=True

from app import database as db
from app.reporting import utc_now


def main():
    for p in (ROOT/'app').rglob('*.py'):
        ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
    with tempfile.TemporaryDirectory(prefix='yapf-offline-smoke-') as temp:
        db.DB_PATH=str(Path(temp)/'test.db')
        db.init_db();db.init_db()
        db.set_setting('telegram_notifications_enabled',0)
        db.set_setting('telegram_bot_token','offline-test')
        conn=db.get_db()
        pid=conn.execute("INSERT INTO projects(domain,name,is_active) VALUES('example.test','Offline test',1)").lastrowid
        for i in range(50):
            conn.execute('INSERT INTO keywords(project_id,keyword,daily_goal) VALUES(?,?,1)',(pid,'query-'+str(i)))
        conn.commit();conn.close()
        from app.dispatch import reserve_keyword,finish_attempt
        now=utc_now();earlier=now-timedelta(minutes=21)
        first=[]
        for _ in range(50):
            t=reserve_keyword(earlier);assert t;first.append(t['id'])
            assert finish_attempt(t,{'id':1,'name':'offline','device_type':'android'},
                                  {'status':'not_found','position':0},earlier)
        assert len(set(first))==50
        assert reserve_keyword(earlier) is None
        with ThreadPoolExecutor(max_workers=10) as pool:
            claims=list(pool.map(lambda _:reserve_keyword(now),range(50)))
        assert all(claims) and len({t['id'] for t in claims})==50
        assert reserve_keyword(now) is None
        for t in claims:
            assert finish_attempt(t,{'id':1,'name':'offline','device_type':'android'},
                                  {'status':'success','position':1,'navigation_verified':True},now)
        assert reserve_keyword(now) is None
        assert not finish_attempt(claims[0],{'id':1},{'status':'success','navigation_verified':True},now)
        from app.main import get_status,list_projects
        status=asyncio.run(get_status())
        projects=asyncio.run(list_projects())
        assert status['clicks_today']==50
        assert sum(k['clicks_today'] for k in projects[0]['keywords'])==50
        assert status['report_timezone']=='Europe/Moscow'
        from app.main import add_keywords
        added=asyncio.run(add_keywords(project_id=pid,keyword=None,keywords_text='alpha\nbeta',
            daily_goal=3,geo_region=None,brand_tail='',geo_regions='["213","2","43","54","65"]'))
        assert added['added_count']==10
        conn=db.get_db()
        assert conn.execute("SELECT COUNT(DISTINCT geo_region) FROM keywords WHERE keyword='alpha'").fetchone()[0]==5
        conn.close()
        from app.engine.position_checker import PositionCheckerManager
        from unittest.mock import AsyncMock
        conn=db.get_db()
        ids=[]
        for n in (1,2):
            project=conn.execute('INSERT INTO projects(name,domain) VALUES(?,?)',('smoke'+str(n),'smoke'+str(n)+'.test')).lastrowid
            conn.execute('INSERT INTO keywords(project_id,keyword) VALUES(?,?)',(project,'only-query'));ids.append(project)
        conn.commit();conn.close()
        async def parallel_checks():
            manager=PositionCheckerManager();started=set();gate=asyncio.Event()
            async def fake(kw,project,*args,**kwargs):
                started.add(project['id']);await gate.wait();return {'status':'ok','position':12}
            manager.check_single_keyword=fake
            await asyncio.gather(*(manager.start_project_check(pid) for pid in ids))
            await asyncio.sleep(0)
            assert started==set(ids)
            assert (await manager.start_project_check(ids[0]))['status']=='busy'
            gate.set();await asyncio.gather(*manager._tasks.values())
            assert all(not j['is_running'] and j['current']==1 for j in manager.jobs.values())
        asyncio.run(parallel_checks())
        from app.main import get_farm_health_api
        health=asyncio.run(get_farm_health_api())
        assert 'advice' in health and 'warming_24h' in health
        print(json.dumps({'status':'PASS','round_unique_keys':50,'concurrent_unique_claims':50,
                          'successes_visible_in_api':50,'multi_region_rows':10,'parallel_position_projects':2,'farm_health':True,'production_database_used':False}))


if __name__=='__main__':main()
