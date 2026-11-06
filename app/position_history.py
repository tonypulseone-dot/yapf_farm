"""Daily rank snapshots, separated by project, region and device (Moscow days)."""
from datetime import timedelta
from .database import get_db
from .reporting import utc_now, business_date, day_bounds, utc_text


def position_timeline(project_id=None, days=7, search='', device_type='desktop', sort='alpha', now=None):
    days=max(1,min(31,int(days)))
    now=now or utc_now()
    from datetime import date
    today=date.fromisoformat(business_date(now))
    dates=[(today-timedelta(days=i)).isoformat() for i in range(days)]
    start=utc_text(now-timedelta(days=days+31))
    conn=get_db()
    try:
        where='WHERE k.project_id=?' if project_id else ''
        params=(project_id,) if project_id else ()
        keywords=[dict(r) for r in conn.execute('SELECT k.*,p.domain,p.name project_name FROM keywords k JOIN projects p ON p.id=k.project_id '+where,params)]
        by_id={k['id']:k for k in keywords}
        histories=conn.execute('''SELECT ph.* FROM positions_history ph
            JOIN keywords k ON k.id=ph.keyword_id
            WHERE ph.checked_at>=? AND ph.source='checker' AND ph.device_type=?'''+
            (' AND ph.project_id=?' if project_id else '')+' ORDER BY ph.checked_at,ph.id',
            (start,device_type,*params)).fetchall()
    finally:
        conn.close()
    groups={}
    def key(k,reg):return (k['project_id'],k['keyword'].strip(),str(reg),device_type)
    def row(k,reg):return {'row_id':f"{k['project_id']}:{k['keyword'].strip()}:{reg}:{device_type}",
        'keyword_id':k['id'],'project_id':k['project_id'],'project_name':k['project_name'],
        'keyword':k['keyword'],'domain':k['domain'],'region':str(reg),'device_type':device_type,
        'target_url':k.get('target_url') or '', 'samples':{}}
    for k in keywords:
        groups.setdefault(key(k,k['geo_region'] or '213'),row(k,k['geo_region'] or '213'))
    for h in histories:
        k=by_id.get(h['keyword_id'])
        if not k:continue
        reg=h['region'] or k['geo_region'] or '213'
        r=groups.setdefault(key(k,reg),row(k,reg))
        from datetime import datetime
        stamp=datetime.fromisoformat(h['checked_at'])
        day=(stamp+timedelta(hours=3)).date().isoformat()
        if day>dates[0]:continue
        r['samples'][day]={'position':h['position'],'checked_at':h['checked_at'],'target_url':h['target_url'] or ''}
    result=[]
    for r in groups.values():
        samples=r.pop('samples');cells={};previous=None
        for day,sample in sorted(samples.items()):
            pos=sample['position'];diff=None;direction='same'
            if previous is not None:
                if 1<=pos<=100 and 1<=previous<=100:
                    diff=previous-pos;direction='up' if diff>0 else 'down' if diff<0 else 'same'
                elif 1<=pos<=100 and previous==0:direction='entered'
                elif pos==0 and 1<=previous<=100:direction='left'
            if day in dates:cells[day]={**sample,'diff':diff,'direction':direction}
            previous=pos
        latest=max(samples) if samples else None
        r.update(cells=cells,current_position=samples[latest]['position'] if latest else None,
                 target_url=samples[latest]['target_url'] if latest else r['target_url'])
        if search.lower() not in (r['keyword']+' '+r['domain']+' '+r['target_url']).lower():continue
        result.append(r)
    def rank(r):return r['current_position'] if r['current_position'] and r['current_position']<=100 else 101
    if sort=='best':result.sort(key=lambda r:(rank(r),r['keyword'].casefold(),r['region']))
    elif sort=='worst':result.sort(key=lambda r:(-rank(r),r['keyword'].casefold(),r['region']))
    elif sort in ('growth','drop'):
        def delta(r):
            values=[r['cells'][d] for d in dates if d in r['cells']]
            return (values[0].get('diff') or 0) if values else 0
        result.sort(key=lambda r:(-delta(r) if sort=='growth' else delta(r),r['keyword'].casefold()))
    else:result.sort(key=lambda r:(r['keyword'].casefold(),r['region'],r['domain']))
    columns=[]
    for day in dates:
        values=[r['cells'][day]['position'] for r in result if day in r['cells']]
        columns.append({'date':day,'label':'.'.join(reversed(day.split('-'))),
            'measured':len(values),'top10_percent':round(sum(1<=v<=10 for v in values)/len(values)*100) if values else None})
    return {'dates':columns,'rows':result,'timezone':'Europe/Moscow','source':'checker'}


def timeline_kpi(timeline):
    rows=timeline['rows'];values=[r['current_position'] for r in rows if r['current_position'] is not None]
    ranks=[p for p in values if 1<=p<=100]
    stamps=[cell['checked_at'] for r in rows for cell in r['cells'].values()]
    from datetime import datetime
    latest=(datetime.fromisoformat(max(stamps))+timedelta(hours=3)).strftime('%d.%m.%Y %H:%M') if stamps else 'Нет замеров'
    return {'total_domains':len({r['domain'] for r in rows}),'avg_position':round(sum(ranks)/len(ranks),1) if ranks else 0,
            'top_1':sum(p==1 for p in values),'top_2_10':sum(2<=p<=10 for p in values),
            'top_11_30':sum(11<=p<=30 for p in values),'top_31_50':sum(31<=p<=50 for p in values),
            'top_51_plus':sum(p==0 or p>50 for p in values),'top_3':sum(1<=p<=3 for p in values),
            'top_10':sum(1<=p<=10 for p in values),'top_50':sum(1<=p<=50 for p in values),
            'top_100':len(ranks),'out_of_top':sum(p==0 or p>100 for p in values),
            'tracked_queries':len(values),'unmeasured':len(rows)-len(values),'last_updated':latest}
