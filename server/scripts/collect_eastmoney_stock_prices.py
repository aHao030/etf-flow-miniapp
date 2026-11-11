"""Collect one independent, full-IPO Eastmoney record per currently listed stock.

This never overwrites or joins Tencent price records. Dry-run does not fetch data.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from contextlib import closing
from datetime import datetime,timedelta,timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time

import collect_stock_prices as p
import stock_price_batch as common
from history import eastmoney_stock_prices as source


def location(root,code):return root/'prices/eastmoney/v1'/f'{code}.json'
def attempt_location(root,code):return root/'prices/eastmoney/attempts/v1'/f'{code}.json'


def cooldown(reason):
    match=re.search(r'eastmoney returned HTTP (\d{3})',reason)
    status=int(match[1]) if match else None
    return 7200 if status in (401,403,429) else 900 if status and 500<=status<=599 else 0


def provider_blocked_until(root, now):
    """Share access/service cooldown across daily refresh and all frozen batches."""
    candidates=[]
    path=root/'locks/eastmoney/provider-cooldown.json'
    if path.exists():
        state=p.read_json(path)
        if not isinstance(state,dict):raise ValueError('invalid provider cooldown')
        candidates.append(p.stamp(state['blocked_until']))
    for checkpoint in (root/'batches').glob('eastmoney-*.sqlite'):
        with closing(sqlite3.connect(checkpoint.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("SELECT value FROM meta WHERE key='blocked_until'").fetchone()
            if row:candidates.append(p.stamp(row[0]))
    return max((until for until in candidates if until>now),default=None)


def persist_provider_stop(root, reason, seconds, now):
    until=now+timedelta(seconds=seconds)
    prior=provider_blocked_until(root,now)
    if prior and prior>until:until=prior
    p.atomic_json(root/'locks/eastmoney/provider-cooldown.json',{'blocked_until':until.isoformat(),'reason':reason})
    return until


class Throttle:
    def __init__(self,interval):self.interval=interval;self.lock=threading.Lock();self.blocked=threading.Event();self.last=0.
    def fetch(self,stock,target):
        with self.lock:
            if self.blocked.is_set():raise RuntimeError('batch stopped before request')
            delay=self.last+self.interval-time.monotonic()
            if delay>0:time.sleep(delay)
            if self.blocked.is_set():raise RuntimeError('batch stopped before request')
            self.last=time.monotonic()
        try:return source.fetch_history(stock,target)
        except RuntimeError as exc:
            if cooldown(str(exc)):self.blocked.set()
            raise


def usable(root,stock,target,now=None):
    record=p.read_json(location(root,stock['stock_code']))
    if not source.valid_record(record,stock) or record['checked_through']<target:return None
    now=now or datetime.now(timezone.utc)
    age=(now-p.stamp(record['fetched_at'])).total_seconds()
    if age<0:return None
    pending=record['checked_through']==target and (not record['series'] or record['series'][-1][0]<target)
    return {'record':record,'retry_due':pending and age>=7200}


def observed(record,stock,target,calendar):
    rows=[r for r in record['series'] if stock['listed_date']<=r[0]<=target and r[0] in calendar]
    return {'stock_code':stock['stock_code'],'status':'cached','rows':len(rows),
            'available_start':rows[0][0] if rows else None,'available_end':rows[-1][0] if rows else None,
            'source_window_observed':True}


def refresh(root,stock,target,fetcher,now=None):
    now=now or datetime.now(timezone.utc);code=stock['stock_code']
    current=usable(root,stock,target,now)
    if current and not current['retry_due']:return {'stock_code':code,'status':'cached','network_requests':0}
    attempt=p.read_json(attempt_location(root,code))
    if isinstance(attempt,dict) and attempt.get('target_end')==target:
        try:
            wait_seconds=cooldown(str(attempt.get('reason',''))) or 7200
            if 0<=(now-p.stamp(attempt['attempted_at'])).total_seconds()<wait_seconds:
                return {'stock_code':code,'status':'backoff','network_requests':0}
        except (KeyError,ValueError):pass
    old=p.read_json(location(root,code))
    old_valid=source.valid_record(old,stock)
    try:
        bundle=fetcher(stock,target)
        record=source.make_record(stock,target,bundle,now.isoformat(timespec='milliseconds').replace('+00:00','Z'))
        if not source.valid_record(record,stock):raise ValueError('invalid complete provider record')
        if old_valid and not {r[0] for r in old['candles'] if r[0]<=target}.issubset({r[0] for r in record['candles']}):
            raise ValueError('same-provider history regression')
        if old_valid and not {r[0] for r in old['series'] if r[0]<=target}.issubset({r[0] for r in record['series']}):
            raise ValueError('same-provider price history regression')
        encoded=json.dumps(record,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
        if len(encoded)>source.MAX_BYTES:raise ValueError('provider record exceeds 2 MiB')
        p.atomic_json(location(root,code),record)
        return {'stock_code':code,'status':'success','network_requests':1,'rows':len(record['series']),
                'available_start':record['series'][0][0] if record['series'] else None,
                'available_end':record['series'][-1][0] if record['series'] else None}
    except Exception as exc:
        reason=str(exc)[:180] if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
        p.atomic_json(attempt_location(root,code),{'attempted_at':common.utcnow(),'target_end':target,'reason':reason})
        return {'stock_code':code,'status':'failed','reason':reason,'network_requests':0 if reason=='batch stopped before request' else 1,'retained_previous':old_valid}


def checkpoint_open(path,fingerprint,target):
    path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE IF NOT EXISTS jobs(stock_code TEXT PRIMARY KEY,status TEXT,updated_at TEXT,result TEXT);')
    state=dict(db.execute('SELECT key,value FROM meta'))
    expected={'provider':'eastmoney','universe_sha256':fingerprint,'target_end':target}
    if state and any(state.get(k)!=v for k,v in expected.items()):db.close();raise ValueError('checkpoint identity mismatch')
    with db:db.executemany('INSERT OR IGNORE INTO meta VALUES(?,?)',list(expected.items()))
    return db


def store_stop(db,reason,seconds):
    candidate=datetime.now(timezone.utc)+timedelta(seconds=seconds)
    previous=dict(db.execute('SELECT key,value FROM meta')).get('blocked_until')
    # A concurrent 5xx must never shorten an earlier 401/403/429 cooldown.
    if previous and p.stamp(previous)>candidate:return previous
    with db:db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',[
        ('blocked_until',candidate.isoformat()),('blocked_reason',reason)])
    return candidate.isoformat()


def import_seeds(manifest_path,universe,cache_root):
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if manifest.get('schema')!=1 or manifest.get('provider')!='eastmoney' or manifest.get('source')!=source.SOURCE or manifest.get('target_end')!=universe['target_end']:
        raise ValueError('seed manifest identity mismatch')
    samples=manifest.get('samples');stocks={s['stock_code']:s for s in universe['stocks']}
    if not isinstance(samples,list) or len(samples)>2:raise ValueError('only the two reviewed samples may be imported')
    results=[]
    for sample in samples:
        code=sample['stock_code'];stock=stocks[code];raw=Path(sample['file']).read_bytes()
        if len(raw)>source.MAX_BYTES or hashlib.sha256(raw).hexdigest()!=sample['sha256']:raise ValueError('sample content hash mismatch')
        data=json.loads(raw);candles=data['rows']
        if data.get('source')!=source.SOURCE or data.get('adjustment')!='none' or sample.get('identity_code')!=code or sample.get('identity_market')!=source.market_id(stock['market']):
            raise ValueError('sample source identity mismatch')
        source.validate_candles(candles,stock['listed_date'],universe['target_end'])
        if sample['provider_total']!=len(candles) or candles[0][0]!=stock['listed_date'] or candles[-1][0]!=universe['target_end']:
            raise ValueError('sample does not cover the reviewed IPO-to-target boundaries')
        bundle={'provider_total':len(candles),'source_name':sample.get('source_name'),'candles':candles,'series':[[r[0],r[2]] for r in candles if r[5]>0]}
        record=source.make_record(stock,universe['target_end'],bundle,p.stamp(sample['fetched_at']).isoformat(timespec='milliseconds').replace('+00:00','Z'))
        record['sample_evidence_sha256']=sample['sha256']
        if not source.valid_record(record,stock):raise ValueError('sample record validation failed')
        if usable(cache_root,stock,universe['target_end']):results.append({'stock_code':code,'status':'already_cached'});continue
        p.atomic_json(location(cache_root,code),record)
        results.append({'stock_code':code,'status':'imported','rows':len(record['series']),'evidence_sha256':sample['sha256']})
    return results


def preserve_tencent_public_artifacts(root):
    for name in ('coverage','progress'):
        path=root/f'{name}.json';old=p.read_json(path)
        if old and old.get('provider')!='eastmoney' and not (root/f'{name}.tencent.json').exists():p.atomic_json(root/f'{name}.tencent.json',old)


def run(args):
    universe,fingerprint=common.read_universe(args.universe_file);target=universe['target_end']
    latest,_=p.read_catalog()
    if target>latest:raise ValueError('universe target is beyond the published closed date')
    if args.import_seeds:
        if not args.seed_manifest:raise ValueError('--import-seeds requires --seed-manifest')
        imported=import_seeds(args.seed_manifest,universe,args.cache_root)
        p.atomic_json(args.report,{'schema':1,'provider':'eastmoney','status':'imported_samples','generated_at':common.utcnow(),'network_requests':0,'results':imported})
        print(json.dumps({'status':'imported_samples','network_requests':0,'results':imported}),flush=True);return
    with closing(sqlite3.connect((p.SERVER/'data/index_history.db').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        calendar=[r[0] for r in db.execute('SELECT trade_date FROM calendar WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date',('1990-01-01',target))]
    if not calendar or calendar[-1]!=target:raise ValueError('full verified calendar does not reach target')
    days=set(calendar);stocks=sorted(universe['stocks'],key=lambda s:s['stock_code']);jobs=[(s['stock_code'],0) for s in stocks]
    results={};queued=[]
    for stock in stocks:
        current=usable(args.cache_root,stock,target)
        if current:results[(stock['stock_code'],0)]=observed(current['record'],stock,target,days)
        if not current or current['retry_due']:queued.append(stock)
    checkpoint=args.checkpoint or args.cache_root/'batches'/f'eastmoney-{fingerprint[:16]}-{target}.sqlite'
    if args.dry_run:
        report={'schema':1,'provider':'eastmoney','status':'planned','generated_at':common.utcnow(),'target_end':target,
                'stocks':len(stocks),'expected_windows':len(stocks),'cached_stocks':len(results),'queued_stocks':len(queued),
                'network_requests':0,'max_new_requests':min(args.max_jobs,len(queued)),'checkpoint':str(checkpoint),
                'universe_sha256':fingerprint,'universe_complete':universe['complete']}
        p.atomic_json(args.report,report);print(json.dumps(report,ensure_ascii=True),flush=True);return
    args.report.parent.mkdir(parents=True,exist_ok=True);events=args.report.with_suffix('.events.jsonl')
    attempted=0;visited=0;status='running';started=common.utcnow();throttle=Throttle(args.interval);coverage=None
    def snapshot(final=False):
        nonlocal coverage
        completed=sum(bool(r.get('source_window_observed')) for r in results.values())
        progress={'schema':1,'provider':'eastmoney','source_label':'东方财富不复权日K','generated_at':common.utcnow(),
            'target_end':target,'stocks':len(stocks),'windows_total':len(stocks),'windows_completed':completed,
            'attempted_this_run':attempted,'status':status}
        p.atomic_json(args.cache_root/'progress.json',progress)
        if final or attempted%500==0:
            coverage=common.make_coverage(universe,target,'1990-01-01',jobs,results,calendar)
            coverage.update(provider='eastmoney',source=source.SOURCE,source_label='东方财富不复权日K',
                            request_unit='one_full_ipo_history_per_stock')
            p.atomic_json(args.cache_root/'coverage.json',coverage)
        p.atomic_json(args.report,{'schema':1,'provider':'eastmoney','started_at':started,'updated_at':common.utcnow(),
            'status':status,'target_end':target,'universe_sha256':fingerprint,'checkpoint':str(checkpoint),
            'progress':progress,'summary':coverage['summary'],'events_file':str(events),
            'note':'Independent provider dataset. Tencent successes remain unchanged; no cross-provider curve merging.'})
    with common.batch_lock(args.cache_root/'locks/eastmoney'),closing(checkpoint_open(checkpoint,fingerprint,target)) as db,events.open('a',encoding='utf-8') as log:
        state=dict(db.execute('SELECT key,value FROM meta'));until=state.get('blocked_until')
        preserve_tencent_public_artifacts(args.cache_root)
        now=datetime.now(timezone.utc)
        if (until and p.stamp(until)>now) or provider_blocked_until(args.cache_root,now):status='source_backoff';snapshot(True);return
        snapshot(True);iterator=iter(queued);exhausted=False;futures={};consecutive=0
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                while futures or not exhausted:
                    while not exhausted and not throttle.blocked.is_set() and len(futures)<args.workers and attempted+len(futures)<args.max_jobs:
                        stock=next(iterator,None)
                        if stock is None:exhausted=True;break
                        futures[pool.submit(refresh,args.cache_root,stock,target,throttle.fetch)]=stock
                    if not futures:break
                    done,_=wait(futures,return_when=FIRST_COMPLETED)
                    for future in done:
                        stock=futures.pop(future);item=future.result();visited+=1;attempted+=item.get('network_requests',0)
                        current=usable(args.cache_root,stock,target)
                        if current:
                            seen=observed(current['record'],stock,target,days)
                            item={**seen,**item,'rows':seen['rows'],'available_start':seen['available_start'],'available_end':seen['available_end']}
                        results[(stock['stock_code'],0)]=item
                        with db:db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)',(stock['stock_code'],item['status'],common.utcnow(),json.dumps(item,separators=(',',':'))))
                        log.write(json.dumps({'at':common.utcnow(),**item},ensure_ascii=False)+'\n');log.flush()
                        seconds=cooldown(item.get('reason',''));consecutive=consecutive+1 if item['status']=='failed' else 0
                        if seconds or consecutive>=5 and status=='running':
                            throttle.blocked.set();status='source_blocked' if seconds==7200 else 'source_service_error' if seconds else 'stopped_after_repeated_errors'
                            seconds=seconds or 900
                            store_stop(db,item.get('reason','repeated errors'),seconds)
                            persist_provider_stop(args.cache_root,item.get('reason','repeated errors'),seconds,datetime.now(timezone.utc))
                        if visited%100==0:snapshot();print(json.dumps({'provider':'eastmoney','attempted':attempted,'stocks':len(stocks),'status':status}),flush=True)
                    if throttle.blocked.is_set():exhausted=True
            if status=='running':status='finished_pass' if exhausted else 'job_limit'
        except KeyboardInterrupt:throttle.blocked.set();status='interrupted';raise
        except Exception:throttle.blocked.set();status='internal_error';raise
        finally:snapshot(True)
    print(json.dumps({'provider':'eastmoney','status':status,'attempted':attempted,'summary':coverage['summary']},ensure_ascii=True),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--universe-file',type=Path,required=True)
    parser.add_argument('--cache-root',type=Path,default=p.ROOT/'price-cache')
    parser.add_argument('--checkpoint',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--dry-run',action='store_true');mode.add_argument('--import-seeds',action='store_true')
    parser.add_argument('--seed-manifest',type=Path)
    parser.add_argument('--max-jobs',type=int,default=6000)
    parser.add_argument('--workers',type=int,choices=[1,2],default=2)
    parser.add_argument('--interval',type=float,default=.6)
    args=parser.parse_args()
    if not 1<=args.max_jobs<=10000 or not .6<=args.interval<=60:parser.error('invalid job bound or minimum 0.6 second global interval')
    logging.disable(logging.INFO)
    run(args)


if __name__=='__main__':main()
