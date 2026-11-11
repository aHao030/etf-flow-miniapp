"""Bounded current-listing price backfill with durable per-window checkpoints."""
from bisect import bisect_left, bisect_right
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlparse

import collect_stock_prices as p


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def read_universe(path):
    raw = Path(path).read_bytes()
    if len(raw) > 12*1024*1024:
        raise ValueError('universe file too large')
    data = json.loads(raw)
    if data.get('schema') != 1 or data.get('kind') != 'current-listed-a-shares':
        raise ValueError('a verified current-listed-a-shares universe is required')
    generated = p.stamp(data['generated_at'])
    as_of = date.fromisoformat(data['as_of']).isoformat()
    target = date.fromisoformat(data['target_end']).isoformat()
    if target > as_of or not isinstance(data.get('complete'), bool) or generated > datetime.now(timezone.utc)+timedelta(minutes=5):
        raise ValueError('invalid universe target or completeness')
    if generated.astimezone(p.BEIJING).date().isoformat() != as_of:
        raise ValueError('universe timestamp does not match its Beijing as-of date')
    exchanges = data.get('exchanges')
    if not isinstance(exchanges, list):
        raise ValueError('universe exchange verification missing')
    if any(not isinstance(e,dict) or e.get('market') not in ('SH','SZ','BJ') for e in exchanges) or len({e['market'] for e in exchanges})!=len(exchanges):
        raise ValueError('invalid or duplicate exchange declaration')
    verified = {e['market'] for e in exchanges if e.get('status') in ('observed','verified')}
    stocks = data.get('stocks')
    if not isinstance(stocks, list) or not 1 <= len(stocks) <= 10000:
        raise ValueError('invalid current listed stock count')
    if type(data.get('stock_count')) is not int or data['stock_count']!=len(stocks):
        raise ValueError('universe stock count mismatch')
    requested=set(data.get('requested_markets',['SH','SZ','BJ']))
    if not requested.issubset({'SH','SZ','BJ'}) or data['complete'] and not requested.issubset(verified):
        raise ValueError('universe completeness contradicts exchange verification')
    def checked_time(value):
        observed=p.stamp(value)
        if observed>generated or observed.astimezone(p.BEIJING).date().isoformat()!=as_of:
            raise ValueError('source observation is not from the declared as-of day')
    for exchange in exchanges:
        checked_time(exchange['observed_at'])
    seen = set()
    for row in stocks:
        code, market = row.get('stock_code'), row.get('market')
        if code in seen or market not in verified or market not in ('SH','SZ','BJ'):
            raise ValueError('duplicate stock or unverified exchange')
        symbol = p.symbol_for(code)
        if symbol[:2].upper() != market or row.get('lifecycle_status') != 'listed':
            raise ValueError('stock market or current listing status mismatch')
        listed = date.fromisoformat(row['listed_date']).isoformat()
        if listed != row['listed_date'] or not '1990-01-01' <= listed <= as_of:
            raise ValueError('invalid IPO date')
        if not isinstance(row.get('stock_name'), str) or not row['stock_name'].strip():
            raise ValueError('stock name missing')
        checked_time(row['observed_at'])
        if not isinstance(row.get('source'),str) or not row['source'].strip():
            raise ValueError('official source label missing')
        source=urlparse(row.get('source_url',''))
        domain={'SH':'sse.com.cn','SZ':'szse.cn','BJ':'bse.cn'}[market]
        if source.scheme!='https' or source.username or source.password or source.port not in (None,443) or not source.hostname or not (source.hostname==domain or source.hostname.endswith('.'+domain)):
            raise ValueError('official listing source URL mismatch')
        seen.add(code)
    counts=Counter(row['market'] for row in stocks)
    for exchange in exchanges:
        if exchange['market'] in verified and (type(exchange.get('count')) is not int or exchange['count']!=counts[exchange['market']]):
            raise ValueError('exchange stock count mismatch')
    return data, hashlib.sha256(raw).hexdigest()


def plan_windows(stocks, start, target):
    jobs = []
    for stock in sorted(stocks, key=lambda s:s['stock_code']):
        first = max(start, stock['listed_date'])
        if first > target:
            continue
        for year in range(1990+5*((int(first[:4])-1990)//5), int(target[:4])+1, 5):
            jobs.append((stock['stock_code'], year))
    if len(jobs) > 80000:
        raise ValueError('too many bounded windows')
    return jobs


class Throttle:
    def __init__(self, interval):
        self.interval, self.lock, self.last = interval, threading.Lock(), 0.
        self.blocked = threading.Event()

    def fetch(self, code, year, end):
        with self.lock:
            if self.blocked.is_set():
                raise RuntimeError('batch source blocked; no further request')
            pause = self.last + self.interval - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            if self.blocked.is_set():
                raise RuntimeError('batch source blocked; no further request')
            self.last = time.monotonic()
        try:
            return p.fetch_daily_window(code, year, end)
        except RuntimeError as exc:
            if source_cooldown_seconds(str(exc)):
                self.blocked.set()
            raise


def access_limited(reason):
    return reason.startswith(tuple(f'price source returned HTTP {n}' for n in (401,403,429)))


def source_cooldown_seconds(reason):
    if access_limited(reason):return 7200
    if reason.startswith('price source returned HTTP 501'):return 900
    return 0


def record_source_stop(db,reason,now=None):
    now=now or datetime.now(timezone.utc)
    seconds=source_cooldown_seconds(reason)
    if not seconds:return None
    until=(now+timedelta(seconds=seconds)).isoformat()
    with db:
        db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',[
            ('blocked_until',until),('blocked_reason',reason)])
    return until


def open_checkpoint(path, fingerprint, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);'
                     'CREATE TABLE IF NOT EXISTS jobs(stock_code TEXT,year INTEGER,status TEXT,updated_at TEXT,result TEXT,PRIMARY KEY(stock_code,year));')
    old = dict(db.execute('SELECT key,value FROM meta'))
    if old and (old.get('universe_sha256') != fingerprint or old.get('target_end') != target):
        db.close()
        raise ValueError('checkpoint belongs to a different universe or target; choose a new checkpoint')
    with db:
        db.executemany('INSERT OR IGNORE INTO meta VALUES(?,?)', [('universe_sha256',fingerprint),('target_end',target)])
    return db


@contextmanager
def batch_lock(cache_root):
    """OS-released lock prevents two collectors duplicating the same source work."""
    cache_root.mkdir(parents=True, exist_ok=True)
    with (cache_root/'.batch.lock').open('a+b') as handle:
        if handle.tell()==0:
            handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('another price batch is already running for this cache') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def inspect_window(cache_root, code, year, target, listed, calendar, now=None):
    record = p.read_json(cache_root/'prices'/'v1'/code/f'{year}.json')
    through = min(target, f'{year+4}-12-31')
    if not p.valid_cache(record, code, year) or record['checked_through'] < through:
        return None
    # Stable completed historical windows are never aged out by a batch resume.
    rows = [r for r in record['series'] if listed <= r[0] <= target and r[0] in calendar]
    now=now or datetime.now(timezone.utc)
    pending_current=through==target and record['checked_through']==through and (not rows or rows[-1][0]<target)
    retry_due=pending_current and (now-p.stamp(record['fetched_at'])).total_seconds()>=7200
    return {'stock_code':code,'window':year,'status':'cached','rows':len(rows),
            'available_start':rows[0][0] if rows else None,'available_end':rows[-1][0] if rows else None,
            'checked_through':through,'source_window_observed':True,'retry_due':retry_due}


def make_coverage(universe, target, start, jobs, results, calendar):
    by_code = {}
    for code, year in jobs:
        by_code.setdefault(code, []).append(results.get((code,year)))
    stocks = []
    for stock in universe['stocks']:
        code = stock['stock_code']; entries = by_code.get(code,[])
        complete = [r for r in entries if r and r.get('source_window_observed')]
        rows = sum(r.get('rows',0) for r in complete)
        firsts = [r['available_start'] for r in complete if r.get('available_start')]
        lasts = [r['available_end'] for r in complete if r.get('available_end')]
        expected = bisect_right(calendar,target)-bisect_left(calendar,max(start,stock['listed_date']))
        gaps = max(0,expected-rows) if len(complete)==len(entries) else None
        available_start,available_end = min(firsts,default=None),max(lasts,default=None)
        state = 'ready' if rows and len(complete)==len(entries) and gaps==0 else 'partial' if rows else 'unavailable' if entries and len(complete)==len(entries) else 'pending'
        stocks.append({k:stock[k] for k in ('stock_code','stock_name','market','listed_date')} |
                      {'available_start':available_start,'available_end':available_end,'rows':rows,
                       'expected_windows':len(entries),'completed_windows':len(complete),
                       'expected_open_days':expected,'missing_open_days':gaps,'status':state})
    summary = {'stocks':len(stocks),'expected_windows':len(jobs),
               'completed_windows':sum(s['completed_windows'] for s in stocks),
               'stocks_with_data':sum(s['rows']>0 for s in stocks),
               'stocks_from_listing':sum(s['available_start']==s['listed_date'] for s in stocks),
               'stocks_through_target':sum(s['available_end']==target for s in stocks),
               'rows':sum(s['rows'] for s in stocks),
               'failed_windows':sum(r.get('status')=='failed' for r in results.values()),
               'pending_windows':len(jobs)-sum(s['completed_windows'] for s in stocks)}
    return {'schema':1,'kind':'listed-stock-price-coverage','generated_at':utcnow(),
            'target_end':target,'universe_as_of':universe['as_of'],'universe_complete':universe['complete'],
            'summary':summary,'stocks':stocks,
            'coverage_note':'源查询完成不等于历史连续完整；缺少的开市日可能是停牌或源缺失，均不填零。价格为不复权收盘价。'}


def run_batch(args):
    universe,fingerprint = read_universe(args.universe_file)
    latest,_ = p.read_catalog()
    target = args.end or universe['target_end']
    start = date.fromisoformat(args.start).isoformat()
    if target != universe['target_end'] or not '1990-01-01' <= start <= target <= latest:
        raise ValueError('use the universe target and an already published closed-market date')
    with closing(sqlite3.connect((p.SERVER/'data'/'index_history.db').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        calendar = [r[0] for r in db.execute('SELECT trade_date FROM calendar WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date',(start,target))]
    if not calendar or calendar[-1] != target:
        raise ValueError('verified trading calendar does not reach target')
    calendar_set = set(calendar)
    jobs = plan_windows(universe['stocks'], start, target)
    listed = {s['stock_code']:s['listed_date'] for s in universe['stocks']}
    checkpoint = args.checkpoint or args.cache_root/'batches'/f'{fingerprint[:16]}-{target}.sqlite'
    results, queued = {}, []
    for code,year in jobs:
        existing = inspect_window(args.cache_root,code,year,target,max(start,listed[code]),calendar_set)
        if existing: results[(code,year)] = existing
        if not existing or existing['retry_due']: queued.append((code,year))
    coverage = make_coverage(universe,target,start,jobs,results,calendar)
    if args.dry_run:
        p.atomic_json(args.report, {'schema':1,'status':'planned','generated_at':utcnow(),
            'target_end':target,'universe_sha256':fingerprint,'stocks':len(universe['stocks']),
            'expected_windows':len(jobs),'cached_windows':len(results),'queued_windows':len(queued),
            'max_new_attempts':min(args.max_jobs,len(queued)),'network_requests':0,
            'checkpoint':str(checkpoint),'universe_complete':universe['complete']})
        print(json.dumps({'status':'planned','stocks':len(universe['stocks']),'windows':len(jobs),'cached':len(results),'queued':len(queued),'network_requests':0}),flush=True)
        return
    args.report.parent.mkdir(parents=True,exist_ok=True)
    event_path=args.report.with_suffix('.events.jsonl')
    attempted=0; status='running'; started=utcnow(); throttle=Throttle(args.interval)
    def snapshot(final=False):
        nonlocal coverage
        completed=sum(bool(r.get('source_window_observed')) for r in results.values())
        progress={'schema':1,'generated_at':utcnow(),'target_end':target,'stocks':len(universe['stocks']),
                  'windows_total':len(jobs),'windows_completed':completed,'attempted_this_run':attempted,'status':status}
        p.atomic_json(args.cache_root/'progress.json',progress)
        if final or attempted%500==0:
            coverage=make_coverage(universe,target,start,jobs,results,calendar)
            p.atomic_json(args.cache_root/'coverage.json',coverage)
        p.atomic_json(args.report,{'schema':1,'started_at':started,'updated_at':utcnow(),'target_end':target,
            'status':status,'universe_sha256':fingerprint,'universe_file':str(args.universe_file),
            'checkpoint':str(checkpoint),'progress':progress,'summary':coverage['summary'],
            'events_file':str(event_path),'coverage_file':str(args.cache_root/'coverage.json')})
    with batch_lock(args.cache_root), closing(open_checkpoint(checkpoint,fingerprint,target)) as db, event_path.open('a',encoding='utf-8') as event_log:
        blocked = dict(db.execute('SELECT key,value FROM meta')).get('blocked_until')
        if blocked and p.stamp(blocked)>datetime.now(timezone.utc):
            status='source_backoff';snapshot(True);return
        snapshot(True)
        def work(job):
            code,year=job
            return p.refresh_window(args.cache_root,code,year,target,fetcher=throttle.fetch)
        iterator=iter(queued); exhausted=False; futures={}; consecutive_failures=0
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                while futures or not exhausted:
                    while not exhausted and not throttle.blocked.is_set() and len(futures)<args.workers and attempted+len(futures)<args.max_jobs:
                        job=next(iterator,None)
                        if job is None: exhausted=True;break
                        futures[pool.submit(work,job)] = job
                    if not futures: break
                    done,_=wait(futures,return_when=FIRST_COMPLETED)
                    for future in done:
                        code,year=futures.pop(future); item=future.result()
                        if item['status'] in ('success','failed'): attempted+=1
                        observed=inspect_window(args.cache_root,code,year,target,max(start,listed[code]),calendar_set)
                        if observed: item={**observed,**item,'rows':observed['rows'],'available_start':observed['available_start'],'available_end':observed['available_end']}
                        results[(code,year)]=item
                        with db:
                            db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?)',(code,year,item['status'],utcnow(),json.dumps(item,separators=(',',':'))))
                        event_log.write(json.dumps({'at':utcnow(),**item},ensure_ascii=False)+'\n');event_log.flush()
                        if source_cooldown_seconds(item.get('reason','')):
                            throttle.blocked.set();status='source_blocked' if access_limited(item['reason']) else 'source_service_error'
                            record_source_stop(db,item['reason'])
                        consecutive_failures=consecutive_failures+1 if item['status']=='failed' else 0
                        if consecutive_failures>=5 and status=='running':
                            throttle.blocked.set();status='stopped_after_repeated_errors'
                        if attempted and attempted%100==0:
                            snapshot();print(json.dumps({'attempted':attempted,'total':len(jobs),'status':status}),flush=True)
                    if throttle.blocked.is_set(): exhausted=True
            if status=='running':status='finished_pass' if exhausted else 'job_limit'
        except KeyboardInterrupt:
            throttle.blocked.set();status='interrupted';raise
        finally:
            snapshot(True)
    print(json.dumps({'status':status,'attempted':attempted,'summary':coverage['summary']},ensure_ascii=True),flush=True)
