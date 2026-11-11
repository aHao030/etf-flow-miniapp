"""Resume fund NAV jobs in the independent ETF history database.

Run from server/: python -m history.nav_backfill --start 2021-01-01 --end YYYY-MM-DD
"""
import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re
import sys
import time
import requests
from . import nav_source, storage

def latest_trade_date(db):
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    return db.execute('SELECT MAX(trade_date) AS day FROM calendar WHERE trade_date<=?',(today,)).fetchone()['day']

def expected_target(db, code, target, latest=None):
    """Whether missing target NAV should remain retryable, never infer old listings."""
    fund=db.execute('SELECT listed_date,delisted_date,current_seen,first_seen FROM funds WHERE fund_code=?',(code,)).fetchone()
    if not fund: return False
    if fund['listed_date'] and fund['listed_date']>target: return False
    if fund['delisted_date'] and fund['delisted_date']<=target: return False
    if db.execute('SELECT 1 FROM shares WHERE fund_code=? AND trade_date=?',(code,target)).fetchone(): return True
    if latest is None: latest=latest_trade_date(db)
    return bool(latest==target and fund['current_seen'] and (not fund['first_seen'] or fund['first_seen']<=target))

def mark_missing_current_pending(db, first, target):
    """Reconcile successful jobs in one current window; no data fetch or observation changes."""
    date.fromisoformat(first);date.fromisoformat(target)
    if first>target or latest_trade_date(db)!=target:
        raise ValueError('target must be the latest known trading date and match the window')
    rows=db.execute("SELECT f.fund_code FROM funds f JOIN jobs j ON j.dataset='nav-v2' AND j.job_key=f.fund_code||':'||?||':'||? WHERE j.status='success' AND NOT EXISTS(SELECT 1 FROM nav_verified n WHERE n.fund_code=f.fund_code AND n.trade_date=?)",(first,target,target)).fetchall()
    codes=[r['fund_code'] for r in rows if expected_target(db,r['fund_code'],target,latest=target)]
    with db:
        db.executemany("UPDATE jobs SET status='pending',error='target-day verified NAV not yet available',updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE dataset='nav-v2' AND job_key=? AND status='success'",[(f'{code}:{first}:{target}',) for code in codes])
    return len(codes)

def windows(start, end):
    # The prior trading day's NAV is needed for the first 2021 change.
    yield (start - timedelta(days=1)).isoformat(), end.isoformat()

def run(start, end, fund=None, limit=None, delay=0.25, market=None):
    db = storage.connect()
    session = requests.Session()
    try:
        rows = db.execute('SELECT fund_code FROM funds WHERE (? IS NULL OR fund_code=?) AND (? IS NULL OR market=?) ORDER BY fund_code', (fund, fund, market, market)).fetchall()
        if not rows:
            raise RuntimeError('基金目录为空或未找到指定代码')
        count = {'success':0, 'pending':0, 'unsupported':0, 'failed':0, 'skipped':0}
        attempted = 0
        for item in rows:
            code = item['fund_code']
            for first, last in windows(start, end):
                key = f'{code}:{first}:{last}'
                if storage.success(db, 'nav-v2', key):
                    status = db.execute('SELECT status FROM jobs WHERE dataset=? AND job_key=?', ('nav-v2',key)).fetchone()['status']
                    has_nav = db.execute('SELECT 1 FROM nav_verified WHERE fund_code=? AND trade_date=?', (code,last)).fetchone()
                    if status == 'unsupported' or has_nav or not expected_target(db,code,last):
                        count['skipped'] += 1
                        continue
                if limit is not None and attempted >= limit:
                    return count
                attempted += 1
                try:
                    for attempt in range(3):
                        try:
                            records, events = nav_source.fetch_bundle(code, first, last, session=session)
                            break
                        except requests.RequestException:
                            if attempt == 2:
                                raise
                            time.sleep(2 * (attempt + 1))
                    with db:
                        db.executemany('INSERT OR REPLACE INTO nav(fund_code,trade_date,unit_nav,acc_nav,source) VALUES(?,?,?,?,?)', records)
                        db.execute('DELETE FROM nav_verified WHERE fund_code=? AND trade_date>=? AND trade_date<=?', (code, first, last))
                        db.executemany('INSERT INTO nav_verified(fund_code,trade_date,source) VALUES(?,?,?)', [(row[0],row[1],row[4]) for row in records])
                        db.execute('DELETE FROM fund_events WHERE fund_code=? AND trade_date>=? AND trade_date<=? AND source=?', (code, first, last, 'eastmoney:pingzhongdata'))
                        db.executemany('INSERT OR REPLACE INTO fund_events(fund_code,trade_date,description,source) VALUES(?,?,?,?)', events)
                        has_nav = any(row[1] == last for row in records)
                        status = 'pending' if not has_nav and expected_target(db,code,last) else 'success'
                        storage.record(db, 'nav-v2', key, status, len(records), 'target-day NAV not yet available' if status == 'pending' else None)
                    count[status] += 1
                except nav_source.UnsupportedNav as exc:
                    with db:
                        storage.record(db, 'nav-v2', key, 'unsupported', 0, str(exc))
                    count['unsupported'] += 1
                except Exception as exc:
                    with db:
                        storage.record(db, 'nav-v2', key, 'failed', 0, f'{type(exc).__name__}: {str(exc)[:300]}')
                    count['failed'] += 1
                print(f'{code} {first}..{last}: {count}', flush=True)
                if delay:
                    time.sleep(delay)
        return count
    finally:
        session.close()
        db.close()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start', default='2021-01-01')
    p.add_argument('--end', default=date.today().isoformat())
    p.add_argument('--fund')
    p.add_argument('--market', choices=['SH','SZ'])
    p.add_argument('--limit', type=int)
    p.add_argument('--delay', type=float, default=0.25)
    args = p.parse_args()
    try:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        if start > end or end > date.today() or (args.fund and not re.fullmatch(r'\d{6}', args.fund)) or (args.limit is not None and args.limit < 1) or args.delay < 0.25:
            raise ValueError('invalid date, fund, limit, or delay')
        count = run(start, end, args.fund, args.limit, args.delay, args.market)
        if count['failed'] or count['pending']:
            sys.exit(1)
    except ValueError as exc:
        p.error(str(exc))

if __name__ == '__main__':
    main()
