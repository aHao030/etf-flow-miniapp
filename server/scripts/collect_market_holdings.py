"""Collect disclosed A-share ETF positions without a fixed sample universe.

These are single-report disclosed positions, not a historical holdings database.
Prefer the most recent broader disclosure within twelve months; otherwise retain
the latest available quarterly report. Reports are never merged across dates.
Weights remain percentages of NAV as reported; incomplete positions are never
renormalized to 100%. Sources and failed/empty products remain in the catalogue.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import threading
import time
from lxml import html

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import config
import fetchers

LOCK = threading.Lock()
NEXT = 0.0
INTERVAL = .5
COLLECTOR_VERSION = 3
class NoDisclosedReport(ValueError):
    pass
class RejectedReports(ValueError):
    pass
SCHEMA = '''CREATE TABLE IF NOT EXISTS fund_holdings(
fund_code TEXT PRIMARY KEY,status TEXT NOT NULL,report_date TEXT,
holdings_type TEXT,coverage_pct REAL,stock_count INTEGER NOT NULL,
source TEXT NOT NULL,observed_at TEXT NOT NULL,error TEXT);
CREATE TABLE IF NOT EXISTS stock_weights(fund_code TEXT NOT NULL,stock_code TEXT NOT NULL,
stock_name TEXT NOT NULL,weight REAL NOT NULL,report_date TEXT NOT NULL,source TEXT NOT NULL,
PRIMARY KEY(fund_code,stock_code));
CREATE INDEX IF NOT EXISTS weights_by_stock ON stock_weights(stock_code,fund_code);
'''


def parse_positions(box, known_stocks):
    positions=[];seen=set()
    for row in box.xpath('.//tbody/tr'):
        cells=row.xpath('./td')
        if len(cells)<7: continue
        stock=cells[1].text_content().strip()
        if stock not in known_stocks: continue
        if stock in seen: raise ValueError('duplicate A-share holding')
        seen.add(stock)
        raw=cells[-3].text_content().strip().replace(',','')
        if not raw.endswith('%'): raise ValueError('holding weight unit is not NAV percent')
        weight=float(raw[:-1])/100
        if not math.isfinite(weight) or not 0<=weight<=1: raise ValueError('invalid holding weight')
        if weight: positions.append((stock,cells[2].text_content().strip(),weight))
    coverage=sum(p[2] for p in positions)*100
    if coverage>105: raise ValueError('disclosed A-share weights exceed conservative bound')
    return positions,coverage


def parse_candidates(text, code, known_stocks, asof, selection_notes=None):
    match = re.search(r'content:\"(.*)\",arryear', text, re.S)
    if not match:
        raise ValueError('missing holdings response envelope')
    content = match.group(1).replace('\\"', '"').replace('\\/', '/')
    if not content.strip():
        raise NoDisclosedReport('no report disclosed in requested year')
    document = html.fromstring(content)
    candidates = []; rejected=[]
    for box in document.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' boxitem ')]"):
        headings = box.xpath('./h4')
        if not headings:
            continue
        heading = headings[0]
        links = heading.xpath('.//a/@href')
        if not any(re.search(r'/(?:'+re.escape(code)+r')\.html(?:[?#]|$)', x) for x in links):
            raise ValueError('fund identity did not match report heading')
        dates = re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)', heading.text_content())
        if not dates:
            continue
        report_date = dates[-1]
        if date.fromisoformat(report_date).isoformat() != report_date or report_date > asof:
            raise ValueError('invalid or future report date')
        try:
            positions,coverage=parse_positions(box,known_stocks)
        except ValueError as exc:
            rejected.append({'report_date':report_date,'reason':str(exc)[:160]})
            continue
        candidates.append((report_date, positions, coverage))
    if selection_notes is not None: selection_notes.extend(rejected)
    if not candidates:
        if rejected: raise RejectedReports('all disclosed reports failed verification: '+rejected[0]['reason'])
        raise NoDisclosedReport('no report identity and date could be verified')
    return candidates


def broad_candidates(candidates, asof):
    today=date.fromisoformat(asof)
    try: oldest=today.replace(year=today.year-1)
    except ValueError: oldest=today.replace(year=today.year-1,day=28)
    return [item for item in candidates if item[0]>=oldest.isoformat() and len(item[1])>10]


def select_report(candidates, asof):
    if not candidates:
        raise NoDisclosedReport('no report identity and date could be verified')
    return max(broad_candidates(candidates,asof) or candidates,key=lambda item:item[0])


def parse(text, code, known_stocks, asof):
    return select_report(parse_candidates(text,code,known_stocks,asof),asof)


def ensure_schema(db):
    db.executescript(SCHEMA)
    if 'collector_version' not in {row[1] for row in db.execute('PRAGMA table_info(fund_holdings)')}:
        db.execute('ALTER TABLE fund_holdings ADD COLUMN collector_version INTEGER NOT NULL DEFAULT 0')
    if 'selection_notes' not in {row[1] for row in db.execute('PRAGMA table_info(fund_holdings)')}:
        db.execute("ALTER TABLE fund_holdings ADD COLUMN selection_notes TEXT NOT NULL DEFAULT '[]'")
    if 'identity_catalogue_hash' not in {row[1] for row in db.execute('PRAGMA table_info(fund_holdings)')}:
        db.execute("ALTER TABLE fund_holdings ADD COLUMN identity_catalogue_hash TEXT NOT NULL DEFAULT ''")
    db.commit()


def identity_codes(db, legacy_codes):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    official={r[0] for r in db.execute('SELECT stock_code FROM stock_listings')} if 'stock_listings' in tables else set()
    return set(legacy_codes)|official


def transport_retry_codes(db):
    return {r[0] for r in db.execute("SELECT fund_code FROM fund_holdings WHERE collector_version=? AND status='unavailable' AND error LIKE 'FetchError:%'",(COLLECTOR_VERSION,))}


def response_cache_path(cache_dir,code,year,month):
    if not re.fullmatch(r'\d{6}',code) or month not in ('6','12') or not 1900<=year<=2200:
        raise ValueError('invalid response cache identity')
    return cache_dir/(code+'-'+str(year)+'-'+month+'.json')


def cached_response(cache_dir,code,year,month,asof,max_age_days):
    if cache_dir is None: return None
    path=response_cache_path(cache_dir,code,year,month)
    try:
        if path.stat().st_size>12*1024*1024: return None
        row=json.loads(path.read_text(encoding='utf-8'))
        age=(date.fromisoformat(asof)-date.fromisoformat(row['observed_at'][:10])).days
        body=row['body']
        if row['fund_code']!=code or row['year']!=year or row['month']!=month or not 0<=age<=max_age_days:
            return None
        if hashlib.sha256(body.encode('utf-8')).hexdigest()!=row['sha256']: return None
        return body
    except (OSError,KeyError,TypeError,ValueError):
        return None


def save_response(cache_dir,code,year,month,text):
    if cache_dir is None: return
    cache_dir.mkdir(parents=True,exist_ok=True)
    path=response_cache_path(cache_dir,code,year,month)
    row={'fund_code':code,'year':year,'month':month,'observed_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),'body':text}
    raw=json.dumps(row,ensure_ascii=False)
    if len(raw.encode('utf-8'))>12*1024*1024: return
    temporary=path.with_suffix('.tmp')
    temporary.write_text(raw,encoding='utf-8');temporary.replace(path)


def fetch_one(code, known_stocks, asof, cache_dir=None, max_age_days=7):
    global NEXT
    source = 'https://fundf10.eastmoney.com/jjcc_'+code+'.html'
    selection_notes=[]
    try:
        candidates=[]
        for year in (int(asof[:4]),int(asof[:4])-1):
            month='6' if year==int(asof[:4]) else '12'
            text=cached_response(cache_dir,code,year,month,asof,max_age_days)
            cache_hit=text is not None
            if text is None:
                with LOCK:
                    remaining = NEXT-time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    NEXT=time.monotonic()+INTERVAL
                url = config.JJCC_URL+'?type=jjcc&code='+code+'&topline=10000&year='+str(year)+'&month='+month
                text=fetchers.http_get(url,headers={'Referer':source,'User-Agent':config.UA},retries=1)
            try:
                candidates.extend(parse_candidates(text,code,known_stocks,asof,selection_notes))
                # Only identity/date-verified responses enter this data-only cache.
                # It contains no authentication headers and is never executed.
                if not cache_hit: save_response(cache_dir,code,year,month,text)
                # A second request is only needed for a possible recent annual
                # disclosure; no-stock reports do not benefit from that lookup.
                if broad_candidates(candidates,asof) or not any(item[1] for item in candidates):
                    break
            except NoDisclosedReport:
                continue
            except RejectedReports:
                # A failed optional annual disclosure must not erase an already
                # independently verified quarterly report. With no verified
                # report, fail closed instead of searching older years.
                if not candidates: raise
        report_date, positions, coverage=select_report(candidates,asof)
        return code, report_date, positions, coverage, source, selection_notes, None
    except Exception as exc:
        return code, None, [], None, source, selection_notes, type(exc).__name__+': '+str(exc)[:160]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asof',default=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
    parser.add_argument('--db',type=Path,default=SERVER/'data'/'stock_holdings.db')
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--max-age-days',type=int,default=7)
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--interval',type=float,default=.5)
    parser.add_argument('--response-cache',type=Path,default=SERVER/'data'/'holdings-response-cache')
    parser.add_argument('--retry-transport-only',action='store_true',help='One pass of current-version network failures only; do not retry unavailable reports')
    args=parser.parse_args()
    if date.fromisoformat(args.asof)>datetime.now(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('future asof')
    if not 0 <= args.max_age_days <= 30 or not 1 <= args.workers <= 3 or args.interval < .35:
        raise ValueError('invalid cache age or request rate')
    global INTERVAL
    INTERVAL=args.interval
    source=sqlite3.connect((SERVER/'data'/'market_history.db').as_uri()+'?mode=ro',uri=True)
    codes=[r[0] for r in source.execute('SELECT fund_code FROM funds ORDER BY fund_code')];source.close()
    app=sqlite3.connect((SERVER/'data'/'app.db').as_uri()+'?mode=ro',uri=True)
    legacy_codes={r[0] for r in app.execute('SELECT stock_code FROM stock_meta')};app.close()
    args.db.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(args.db,timeout=60);db.execute('PRAGMA journal_mode=WAL');ensure_schema(db)
    known=identity_codes(db,legacy_codes)
    if len(known)<5000 or not codes:
        raise ValueError('Full A-share identity catalogue is unavailable')
    identity_hash=hashlib.sha256('\n'.join(sorted(known)).encode('ascii')).hexdigest()
    observed=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    cache_start=(date.fromisoformat(args.asof)-timedelta(days=args.max_age_days)).isoformat()
    done={r[0] for r in db.execute("SELECT fund_code FROM fund_holdings WHERE substr(observed_at,1,10)>=? AND substr(observed_at,1,10)<=? AND status!='unavailable' AND report_date IS NOT NULL AND collector_version=? AND identity_catalogue_hash=?",(cache_start,args.asof,COLLECTOR_VERSION,identity_hash))}
    todo=codes if args.refresh else [code for code in codes if code not in done]
    if args.retry_transport_only:
        if args.refresh: raise ValueError('refresh cannot be combined with targeted retry')
        failed=transport_retry_codes(db)
        todo=[code for code in codes if code in failed]
    print(json.dumps({'total_etfs':len(codes),'identity_stocks':len(known),'already_checked':len(done),'to_fetch':len(todo),'profile':COLLECTOR_VERSION}),flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(fetch_one,code,known,args.asof,args.response_cache,args.max_age_days) for code in todo]
        for count,future in enumerate(as_completed(futures),1):
            code,report_date,positions,coverage,source,selection_notes,error=future.result()
            status='unavailable' if error else 'partial' if positions else 'no_ashare_holdings'
            with db:
                db.execute('INSERT OR REPLACE INTO fund_holdings(fund_code,status,report_date,holdings_type,coverage_pct,stock_count,source,observed_at,error,collector_version,selection_notes,identity_catalogue_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(code,status,report_date,'broader_disclosure' if len(positions)>10 else 'latest_disclosed_positions',coverage,len(positions),source,observed,error,COLLECTOR_VERSION,json.dumps(selection_notes,ensure_ascii=False),identity_hash))
                # A failed refresh cannot silently retain obsolete weights as current.
                db.execute('DELETE FROM stock_weights WHERE fund_code=?',(code,))
                db.executemany('INSERT INTO stock_weights VALUES(?,?,?,?,?,?)',[(code,stock,name,weight,report_date,source) for stock,name,weight in positions])
            if count%50==0 or count==len(todo):
                counts=dict(db.execute('SELECT status,count(*) FROM fund_holdings GROUP BY status'))
                print(json.dumps({'processed':count,'of':len(todo),'counts':counts,'profile':COLLECTOR_VERSION,'upgraded_funds':db.execute('SELECT count(*) FROM fund_holdings WHERE collector_version=?',(COLLECTOR_VERSION,)).fetchone()[0],'distinct_stocks':db.execute('SELECT COUNT(DISTINCT stock_code) FROM stock_weights').fetchone()[0],'weights':db.execute('SELECT COUNT(*) FROM stock_weights').fetchone()[0],'excluded_reports':sum(len(json.loads(r[0])) for r in db.execute('SELECT selection_notes FROM fund_holdings WHERE collector_version=?',(COLLECTOR_VERSION,)))}),flush=True)
    print(json.dumps({'funds':db.execute('SELECT COUNT(*) FROM fund_holdings').fetchone()[0], 'weights':db.execute('SELECT COUNT(*) FROM stock_weights').fetchone()[0], 'stocks':db.execute('SELECT COUNT(DISTINCT stock_code) FROM stock_weights').fetchone()[0]}),flush=True)
    db.close()


if __name__=='__main__':main()
