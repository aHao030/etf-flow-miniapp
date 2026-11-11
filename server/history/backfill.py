"""Resumable exchange history: run from server/ via python -m history.backfill."""
import argparse, calendar as cal, time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from . import providers, storage


def save_day(db,dataset,day,rows,market,source):
    with db:
        codes=[r[0] for r in rows]
        for offset in range(0,len(codes),500):
            batch=codes[offset:offset+500]
            placeholders=','.join('?' for _ in batch)
            collision=db.execute(f'SELECT fund_code FROM funds WHERE fund_code IN ({placeholders}) AND market != ? LIMIT 1',(*batch,market)).fetchone()
            if collision: raise ValueError(f'fund code belongs to other market: {collision[0]}')
            collision=db.execute(f'SELECT fund_code FROM shares WHERE trade_date=? AND fund_code IN ({placeholders}) AND source != ? LIMIT 1',(day,*batch,source)).fetchone()
            if collision: raise ValueError(f'fund date already belongs to another source: {collision[0]} {day}')
        db.executemany('''INSERT INTO funds(fund_code,fund_name,market,category,listed_date,delisted_date,lifecycle_status,current_seen,first_seen,last_seen,source)
                          VALUES(?,?,?,?,NULL,NULL,'unknown',0,?,?,?)
                          ON CONFLICT(fund_code) DO UPDATE SET fund_name=CASE WHEN funds.current_seen=0 AND (funds.last_seen IS NULL OR excluded.last_seen>=funds.last_seen) THEN excluded.fund_name ELSE funds.fund_name END, category=COALESCE(funds.category,excluded.category),first_seen=MIN(COALESCE(funds.first_seen,excluded.first_seen),excluded.first_seen),last_seen=MAX(COALESCE(funds.last_seen,excluded.last_seen),excluded.last_seen)''',
                       [(c,name,market,'货币市场基金' if dataset=='shares-sh-money' else None,day,day,source) for c,name,_,_ in rows])
        db.executemany('INSERT INTO shares(fund_code,trade_date,shares,source) VALUES(?,?,?,?) ON CONFLICT(fund_code,trade_date) DO UPDATE SET shares=excluded.shares,source=excluded.source',[(c,d,n,source) for c,_,d,n in rows])
        storage.record(db,dataset,day,'success',len(rows))


def save_calendar(db,start,end):
    dates=providers.dates()
    if dates[0]>start or dates[-1]<end: raise ValueError('provider calendar does not cover requested interval')
    days=[d for d in dates if start<=d<=end]
    with db: db.executemany('INSERT OR IGNORE INTO calendar(trade_date,source) VALUES(?,?)',[(d,'akshare_sina_trade_calendar') for d in days])
    return days


def run_shares(db,start,end,limit,refresh=False,days=None):
    days=days if days is not None else save_calendar(db,start,end); done=failed=attempted=0
    print(f'SSE calendar {len(days)} dates',flush=True)
    for day in days:
        if not refresh and storage.success(db,'shares',day): continue
        if limit and attempted>=limit: break
        attempted+=1
        try:
            rows=providers.sse_shares(day); save_day(db,'shares',day,rows,'SH','sse_daily_etf_scale'); done+=1
        except Exception as e:
            with db: storage.record(db,'shares',day,'failed',0,f'{type(e).__name__}: {e}')
            failed+=1
            print(f'SSE failed {day} {type(e).__name__}: {str(e)[:160]}',flush=True)
        if attempted%20==0: print(f'SSE attempted={attempted} ok={done} failed={failed} date={day}',flush=True)
        time.sleep(.3)
    print(f'SSE complete attempted={attempted} ok={done} failed={failed}',flush=True)
    return failed


def run_money(db,start,end,limit,refresh=False,days=None):
    days=days if days is not None else save_calendar(db,start,end)
    pending=[d for d in days if refresh or not storage.success(db,'shares-sh-money',d)]
    if limit: pending=pending[:limit]
    print(f'SSE money jobs={len(pending)} calendar={len(days)} workers=3',flush=True)
    success=failed=0
    iterator=iter(pending)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={}
        def submit():
            day=next(iterator,None)
            if day is not None: futures[pool.submit(providers.sse_currency_shares,day)]=day
        for _ in range(3): submit()
        while futures:
            done,_=wait(futures,return_when=FIRST_COMPLETED)
            for future in done:
                day=futures.pop(future)
                try:
                    rows=future.result()
                    save_day(db,'shares-sh-money',day,rows,'SH','sse_daily_money_fund_scale')
                    success+=1
                except Exception as exc:
                    with db: storage.record(db,'shares-sh-money',day,'failed',0,f'{type(exc).__name__}: {exc}')
                    failed+=1
                    print(f'SSE money failed {day} {type(exc).__name__}: {str(exc)[:160]}',flush=True)
                if (success+failed)%25==0: print(f'SSE money processed={success+failed}/{len(pending)} ok={success} failed={failed}',flush=True)
                submit()
    print(f'SSE money complete ok={success} failed={failed}',flush=True)
    return failed


def run_sz(db,start,end,limit,dataset='shares-sz',refresh=False,days=None):
    days=days if days is not None else save_calendar(db,start,end); groups={}
    for d in days:
        if refresh or not storage.success(db,dataset,d): groups.setdefault(d[:7],[]).append(d)
    attempted=done=failed=0
    for month, missing in groups.items():
        if limit and attempted>=limit: break
        if limit: missing=missing[:limit-attempted]
        first,last=missing[0],missing[-1]
        attempted+=len(missing)
        try:
            rows=None; error=None
            for attempt in range(3):
                try:
                    rows=providers.sz_shares(first,last,set(days));break
                except Exception as e:
                    error=e
                    if attempt<2:time.sleep(2*(attempt+1))
            if rows is None: raise error
            for day in missing:
                data=rows.get(day)
                if not data:
                    with db: storage.record(db,dataset,day,'failed',0,'empty on expected trading date')
                    failed+=1
                else:
                    if dataset == 'shares-sz-verified':
                        previous = {r['fund_code']:r['shares'] for r in db.execute("SELECT fund_code,shares FROM shares WHERE trade_date=? AND source='szse_daily_etf_scale'",(day,))}
                        current = {c:n for c,_,_,n in data}
                        if previous and (previous.keys() != current.keys() or any(abs(previous[c]-current[c]) > .005 for c in previous)):
                            with db: storage.record(db,dataset,day,'failed',0,'raw source differs from previous collection; requires review')
                            failed+=1
                            continue
                    save_day(db,dataset,day,data,'SZ','szse_daily_etf_scale');done+=1
        except Exception as e:
            for day in missing:
                with db: storage.record(db,dataset,day,'failed',0,f'{type(e).__name__}: {e}')
            failed+=len(missing)
            print(f'SZSE failed {month}: {type(e).__name__} {str(e)[:150]}',flush=True)
        print(f'SZSE through={month} attempted={attempted} ok={done} failed={failed}',flush=True)
        time.sleep(.5)
    print(f'SZSE complete attempted={attempted} ok={done} failed={failed}',flush=True)
    return failed


def run_catalog(db):
    rows=providers.sz_catalog()
    now=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    with db:
        codes=[r[0] for r in rows]
        for offset in range(0,len(codes),500):
            batch=codes[offset:offset+500]
            placeholders=','.join('?' for _ in batch)
            collision=db.execute(f"SELECT fund_code FROM funds WHERE fund_code IN ({placeholders}) AND market != 'SZ' LIMIT 1",batch).fetchone()
            if collision: raise ValueError(f'fund code belongs to other market: {collision[0]}')
        db.execute("UPDATE funds SET current_seen=0, lifecycle_status=CASE WHEN delisted_date IS NOT NULL THEN 'delisted' ELSE 'unknown' END WHERE market='SZ'")
        db.executemany('''INSERT INTO funds(fund_code,fund_name,market,category,listed_date,delisted_date,lifecycle_status,current_seen,first_seen,last_seen,source) VALUES(?,?,?,?,?,NULL,'current_seen',1,?,?,?) ON CONFLICT(fund_code) DO UPDATE SET fund_name=excluded.fund_name,category=excluded.category,listed_date=COALESCE(excluded.listed_date,funds.listed_date),lifecycle_status='current_seen',current_seen=1,last_seen=MAX(COALESCE(funds.last_seen,excluded.last_seen),excluded.last_seen),source=excluded.source''',[(c,n,'SZ',cat,listing,now,now,'szse_current_etf_catalog') for c,n,cat,listing in rows])
        storage.record(db,'catalog','szse-current','success',len(rows))
    print(f'SZSE current catalog={len(rows)}',flush=True)


def update_sse_current(db):
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    now=today.isoformat()
    observed=[]
    for dataset,source in (('shares','sse_daily_etf_scale'),('shares-sh-money','sse_daily_money_fund_scale')):
        latest=db.execute("SELECT MAX(job_key) AS day FROM jobs WHERE dataset=? AND status='success' AND job_key<=?",(dataset,now)).fetchone()['day']
        if latest and (today-date.fromisoformat(latest)).days <= 10:
            codes={r['fund_code'] for r in db.execute('SELECT fund_code FROM shares WHERE trade_date=? AND source=?',(latest,source))}
            if codes: observed.append((dataset,latest,codes))
    union=set().union(*(x[2] for x in observed)) if observed else set()
    with db:
        db.execute("UPDATE funds SET current_seen=0,lifecycle_status=CASE WHEN delisted_date IS NOT NULL THEN 'delisted' ELSE 'unknown' END WHERE market='SH'")
        db.executemany("UPDATE funds SET current_seen=1,lifecycle_status='current_seen' WHERE market='SH' AND fund_code=?",[(c,) for c in union])
        storage.record(db,'catalog','sse-current','success' if len(observed)==2 else 'pending',len(union),None if len(observed)==2 else 'one or more exchange sources not current')
    print(f'SSE current observed={len(union)} sources={[(d,day,len(c)) for d,day,c in observed]}',flush=True)


def run_nav_bounded(db,start,end,limit):
    """Prioritize least-recent NAV; completed jobs do not consume the cap."""
    from . import nav_backfill
    prior=db.execute('SELECT MAX(trade_date) AS day FROM calendar WHERE trade_date<?',(start,)).fetchone()['day']
    previous = prior or (date.fromisoformat(start)-timedelta(days=7)).isoformat()
    effective_start=date.fromisoformat(previous)+timedelta(days=1)
    rows=db.execute('''SELECT f.fund_code,j.status, (SELECT MAX(n.trade_date) FROM nav n WHERE n.fund_code=f.fund_code) AS newest
      FROM funds f LEFT JOIN jobs j ON j.dataset='nav-v2' AND j.job_key=f.fund_code || ':' || ? || ':' || ?
      WHERE (COALESCE(f.last_seen,'')>=? OR f.current_seen=1)
      ORDER BY CASE WHEN j.status IS NULL THEN 0 ELSE 1 END,COALESCE(newest,''),f.fund_code''',(previous,end,start)).fetchall()
    latest=nav_backfill.latest_trade_date(db)
    eligible=[]
    for row in rows:
        if row['status']=='unsupported': continue
        if row['status']=='success':
            has_nav=db.execute('SELECT 1 FROM nav_verified WHERE fund_code=? AND trade_date=?',(row['fund_code'],end)).fetchone()
            if has_nav or not nav_backfill.expected_target(db,row['fund_code'],end,latest=latest): continue
        eligible.append(row)
        if len(eligible)>=limit: break
    print(f'NAV bounded to {len(eligible)} eligible jobs of cap {limit} for {start}..{end}',flush=True)
    total={'success':0,'unsupported':0,'pending':0,'failed':0,'skipped':0}
    for row in eligible:
        result=nav_backfill.run(effective_start,date.fromisoformat(end),fund=row['fund_code'],limit=1)
        for key in total: total[key]+=result.get(key,0)
    print(f'NAV totals {total}',flush=True)
    return total


def main():
    p=argparse.ArgumentParser(); p.add_argument('--dataset',choices=['catalog','shares','shares-money','shares-sz','shares-sz-verified','nav','all'],required=True)
    p.add_argument('--start',default='2020-12-31');p.add_argument('--end',default='2026-10-30');p.add_argument('--max-jobs',type=int,default=0)
    a=p.parse_args(); start=providers.valid_date(a.start);end=providers.valid_date(a.end)
    if start>end or end>datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat() or a.max_jobs<0: p.error('invalid range or future end')
    db=storage.connect()
    try:
        failed=0
        days=save_calendar(db,start,end) if a.dataset == 'all' else None
        refresh=days is not None and len(days)<=10
        if refresh: print('Short daily window: refreshing existing exchange share observations',flush=True)
        if a.dataset in ('catalog','all'):
            try:
                run_catalog(db)
                update_sse_current(db)
            except Exception as e:
                with db: storage.record(db,'catalog','szse-current','failed',0,f'{type(e).__name__}: {e}')
                failed+=1
                print(f'catalog failed: {type(e).__name__}: {str(e)[:160]}',flush=True)
        if a.dataset in ('shares','all'):
            failed+=run_shares(db,start,end,a.max_jobs,refresh=refresh,days=days)
        if a.dataset in ('shares-money','all'):
            failed+=run_money(db,start,end,a.max_jobs,refresh=refresh,days=days)
        if a.dataset == 'all': update_sse_current(db)
        if a.dataset in ('shares-sz','shares-sz-verified','all'):failed+=run_sz(db,start,end,a.max_jobs,'shares-sz-verified' if a.dataset=='shares-sz-verified' else 'shares-sz',refresh=refresh,days=days)
        if a.dataset in ('nav','all'):
            if a.dataset == 'nav': save_calendar(db,start,end)
            limit=a.max_jobs or 100
            target=db.execute('SELECT MAX(trade_date) AS day FROM calendar WHERE trade_date<=?',(end,)).fetchone()['day']
            if not target:
                raise ValueError('NAV requires a populated trading calendar')
            nav_start=min(start,target)
            result=run_nav_bounded(db,nav_start,target,limit)
            failed+=result['failed']
        if failed: raise SystemExit(1)
    finally:db.close()
if __name__=='__main__':main()
