"""Public-source index prices and explicitly verified ETF benchmark mappings.

Run indices with the project Python; run mappings with the installed Scrapling
Python. No credentials, synthesized historical observations, or fund-name-only
assignments are used. Each dataset is resumable in the independent SQLite DB.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timezone, timedelta
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
BEIJING=timezone(timedelta(hours=8))

SERVER=Path(__file__).resolve().parents[1]
DEFAULT=SERVER/'data'/'index_history.db'
# Codes checked against exchange/index-provider index lists and factsheets.
INDEXES=[
 ('000001','上证综指','sh000001',['上证综合指数','上海证券综合指数']),
 ('399001','深证成指','sz399001',['深证成份指数','深证成分指数','深证成份指数(价格)']),
 ('000300','沪深300','sh000300',['沪深300指数']),
 ('000016','上证50','sh000016',['上证50指数']),
 ('000905','中证500','sh000905',['中证500指数']),
 ('000852','中证1000','sh000852',['中证1000指数']),
 ('000688','科创50','sh000688',['上证科创板50成份指数','上证科创板50成分指数']),
 ('399006','创业板指','sz399006',['创业板指数','创业板指数(价格)']),
 ('000510','中证A500','sh000510',['中证A500指数']),
 ('000010','上证180','sh000010',['上证180指数']),
 ('399330','深证100','sz399330',['深证100指数','深证100价格指数','深证100指数(价格)']),
 ('000906','中证800','sh000906',['中证800指数']),
 ('932000','中证2000','sh932000',['中证2000指数']),
 ('930050','中证A50','sh930050',['中证A50指数']),
 ('399673','创业板50','sz399673',['创业板50指数']),
 ('000698','科创100','sh000698',['上证科创板100指数']),
]
SCHEMA='''
CREATE TABLE IF NOT EXISTS indexes(index_code TEXT PRIMARY KEY,index_name TEXT NOT NULL,symbol TEXT NOT NULL,source TEXT NOT NULL,source_url TEXT NOT NULL,base_date TEXT,launch_date TEXT,note TEXT);
CREATE TABLE IF NOT EXISTS index_daily(index_code TEXT NOT NULL,trade_date TEXT NOT NULL,open REAL,high REAL,low REAL,close REAL NOT NULL,volume REAL,source TEXT NOT NULL,PRIMARY KEY(index_code,trade_date));
CREATE TABLE IF NOT EXISTS calendar(trade_date TEXT PRIMARY KEY,source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fund_index(fund_code TEXT PRIMARY KEY,index_code TEXT NOT NULL,source TEXT NOT NULL,source_url TEXT NOT NULL,benchmark TEXT NOT NULL,verified_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mapping_checks(fund_code TEXT PRIMARY KEY,benchmark TEXT,status TEXT NOT NULL,source_url TEXT NOT NULL,checked_at TEXT NOT NULL,error TEXT);
CREATE TABLE IF NOT EXISTS jobs(dataset TEXT NOT NULL,job_key TEXT NOT NULL,status TEXT NOT NULL,rows_count INTEGER NOT NULL DEFAULT 0,error TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(dataset,job_key));
'''

def now(): return datetime.now(BEIJING).isoformat()
def connect(path):
    p=Path(path).resolve()
    if p in {(SERVER/'data'/'app.db').resolve(),(SERVER/'data'/'market_history.db').resolve()}: raise ValueError('index database must be independent')
    p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(p,timeout=60);db.row_factory=sqlite3.Row;db.execute('PRAGMA journal_mode=WAL');db.executescript(SCHEMA)
    with db:
        for code,name,symbol,_ in INDEXES:
            db.execute('INSERT INTO indexes(index_code,index_name,symbol,source,source_url,note) VALUES(?,?,?,?,?,?) ON CONFLICT(index_code) DO UPDATE SET index_name=excluded.index_name,symbol=excluded.symbol',
              (code,name,symbol,'sina_index_daily',f'https://finance.sina.com.cn/realstock/company/{symbol}/nc.shtml','早期记录可能包含指数发布前回溯；数据起日不等于指数发布日期。'))
    return db

def record(db,dataset,key,status,count=0,error=None):
    with db: db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?)',(dataset,key,status,count,str(error)[:500] if error else None,now()))

def valid_prices(frame,end):
    out=[];seen=set()
    for row in frame.to_dict('records'):
        day=str(row['date'])[:10];d=date.fromisoformat(day)
        if day>end: continue
        if day in seen: raise ValueError('duplicate index observation')
        seen.add(day)
        vals=[]
        for key in ('open','high','low','close','volume'):
            v=float(row[key]);
            if not math.isfinite(v) or v<0 or (key!='volume' and v==0): raise ValueError('invalid index '+key)
            vals.append(v)
        if vals[1]<max(vals[0],vals[2],vals[3]) or vals[2]>min(vals[0],vals[1],vals[3]): raise ValueError('invalid OHLC ordering')
        out.append((day,*vals))
    if not out: raise ValueError('empty index history')
    return sorted(out)

def collect_indices(db,end):
    import akshare as ak
    calendar=sorted({str(v)[:10] for v in ak.tool_trade_date_hist_sina()['trade_date']})
    if not calendar or calendar[0]>'1991-01-01' or calendar[-1]<end: raise ValueError('calendar does not cover requested history')
    for v in calendar: date.fromisoformat(v)
    with db: db.executemany('INSERT OR REPLACE INTO calendar VALUES(?,?)',[(d,'akshare_sina_trade_calendar') for d in calendar])
    dayset=set(calendar)
    for code,name,symbol,_ in INDEXES:
        try:
            source='sina_index_daily'; source_url=f'https://finance.sina.com.cn/realstock/company/{symbol}/nc.shtml'
            try:
                frame=ak.stock_zh_index_daily(symbol);rows=valid_prices(frame,end)
            except Exception:
                if code not in ('930050','932000'):raise
                frame=ak.stock_zh_index_daily_em('csi'+code,start_date='19900101',end_date=end.replace('-',''))
                rows=valid_prices(frame,end);source='eastmoney_index_daily';source_url=f'https://quote.eastmoney.com/zs{code}.html'
            if code=='000852':
                # This provider exposes a longer published back-history. Compare
                # every overlapping close before accepting the alternate series.
                alternate=valid_prices(ak.stock_zh_index_daily_em(symbol,start_date='19900101',end_date=end.replace('-','')),end)
                primary={r[0]:r[4] for r in rows};overlap=[abs(primary[r[0]]-r[4]) for r in alternate if r[0] in primary]
                if len(overlap)<100 or max(overlap)>0.02:raise ValueError('alternate index source disagrees')
                if alternate[0][0]<rows[0][0]:
                    rows=alternate;source='eastmoney_index_daily';source_url=f'https://quote.eastmoney.com/zs{code}.html'
            if any(r[0] not in dayset for r in rows): raise ValueError('index observation outside source calendar')
            with db:
                db.executemany('INSERT OR REPLACE INTO index_daily VALUES(?,?,?,?,?,?,?,?)',[(code,*r,source) for r in rows])
                db.execute('UPDATE indexes SET source=?,source_url=? WHERE index_code=?',(source,source_url,code))
            record(db,'index_daily',code,'success',len(rows)); print(json.dumps({'index':code,'name':name,'rows':len(rows),'start':rows[0][0],'end':rows[-1][0]},ensure_ascii=False),flush=True)
        except Exception as exc:
            record(db,'index_daily',code,'failed',error=type(exc).__name__+': '+str(exc));print(json.dumps({'index':code,'status':'failed','error':type(exc).__name__+': '+str(exc)[:160]},ensure_ascii=False),flush=True)

def norm(s): return re.sub(r'\s+','',s).replace('（','(').replace('）',')').upper()
ALIASES={norm(alias):code for code,name,sym,aliases in INDEXES for alias in [name,*aliases]}
def match_benchmark(text): return ALIASES.get(norm(text))

_lock=threading.Lock();_last=0
def throttle():
    global _last
    with _lock:
        delay=max(0,0.45-(time.monotonic()-_last))
        if delay: time.sleep(delay)
        _last=time.monotonic()
def fetch_mapping(code):
    from scrapling.fetchers import Fetcher
    url=f'https://fundf10.eastmoney.com/jbgk_{code}.html'
    throttle()
    r=Fetcher.get(url,timeout=20,retries=0)
    if r.status!=200: raise ValueError('HTTP '+str(r.status))
    values={}
    for tr in r.css('table.info tr'):
        cells=tr.css('th,td')
        for i in range(0,len(cells)-1,2): values[cells[i].get_all_text().strip()]=cells[i+1].get_all_text().strip()
    if not str(values.get('基金代码','')).startswith(code): raise ValueError('fund identity missing or mismatched')
    benchmark=values.get('跟踪标的')
    if benchmark is None: raise ValueError('benchmark field missing')
    return benchmark,match_benchmark(benchmark),url

def collect_mappings(db,history,limit,refresh,new_only=False):
    logging.getLogger('scrapling').setLevel(logging.WARNING)
    with sqlite3.connect(Path(history).resolve().as_uri()+'?mode=ro',uri=True) as src:
        funds=src.execute('SELECT fund_code,fund_name FROM funds ORDER BY fund_code').fetchall()
    done=set() if refresh else {r[0] for r in db.execute("SELECT fund_code FROM mapping_checks WHERE status IN ('mapped','other_index')")}
    if new_only:done={r[0] for r in db.execute('SELECT fund_code FROM mapping_checks')}
    priority=re.compile('300|500|1000|2000|50|180|深证|上证|综指|创业|800|100')
    funds=sorted([r for r in funds if r[0] not in done],key=lambda r:(not bool(priority.search(r[1] or '')),r[0]))
    if limit: funds=funds[:limit]
    print('mapping_jobs='+str(len(funds)),flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(fetch_mapping,c):c for c,_ in funds}
        for n,future in enumerate(as_completed(jobs),1):
            code=jobs[future];url=f'https://fundf10.eastmoney.com/jbgk_{code}.html';timestamp=now()
            try:
                benchmark,index,url=future.result();status='mapped' if index else 'other_index'
                with db:
                    db.execute('INSERT OR REPLACE INTO mapping_checks VALUES(?,?,?,?,?,NULL)',(code,benchmark,status,url,timestamp))
                    if index: db.execute('INSERT OR REPLACE INTO fund_index VALUES(?,?,?,?,?,?)',(code,index,'eastmoney_fund_profile',url,benchmark,timestamp))
                    else: db.execute('DELETE FROM fund_index WHERE fund_code=?',(code,))
            except Exception as exc:
                with db: db.execute('INSERT OR REPLACE INTO mapping_checks VALUES(?,NULL,?,?,?,?)',(code,'failed',url,timestamp,type(exc).__name__+': '+str(exc)[:150]))
            if n%50==0 or n==len(funds):print(json.dumps({'checked':n,'total':len(funds),'mapped':db.execute('SELECT COUNT(*) FROM fund_index').fetchone()[0]},ensure_ascii=False),flush=True)

def collect_off_catalog(path,history):
    import akshare as ak
    rows=ak.fund_name_em().to_dict('records')
    pattern=re.compile('沪深300|HS300|中证500|中证1000|中证2000|科创50|科创100|科创板50|科创板100|中证A500|中证A50|上证50|上证180|深证100|中证800|创业板50|创业板|上证综|上证指数|深证成',re.I)
    with sqlite3.connect(Path(history).resolve().as_uri()+'?mode=ro',uri=True) as source:
        listed={r[0] for r in source.execute('SELECT fund_code FROM funds')}
    candidates=[r for r in rows if r['基金代码'] not in listed and pattern.search(r['基金简称']) and ('指数' in r['基金类型'] or '联接' in r['基金简称']) and not ('ETF' in r['基金简称'].upper() and '联接' not in r['基金简称'])]
    if not candidates:raise ValueError('empty candidate discovery')
    Path(path).write_text(json.dumps(candidates,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'off_exchange_candidates':len(candidates),'catalog_source':'https://fund.eastmoney.com/js/fundcode_search.js'},ensure_ascii=False),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',choices=['indices','mappings','off-catalog','report'],required=True);p.add_argument('--db',default=str(DEFAULT));p.add_argument('--history-db',default=str(SERVER/'data'/'market_history.db'));p.add_argument('--end',default=datetime.now(BEIJING).date().isoformat());p.add_argument('--limit',type=int,default=0);p.add_argument('--refresh-mappings',action='store_true');p.add_argument('--new-only',action='store_true');p.add_argument('--catalog',default=str(SERVER/'data'/'off_exchange_catalog_candidates.json'));p.add_argument('--report')
    a=p.parse_args();date.fromisoformat(a.end);db=connect(a.db)
    try:
        if a.dataset=='indices':collect_indices(db,a.end)
        if a.dataset=='mappings':collect_mappings(db,a.history_db,a.limit,a.refresh_mappings,a.new_only)
        if a.dataset=='off-catalog':collect_off_catalog(a.catalog,a.history_db)
        report={'generated_at':now(),'indices':[dict(r) for r in db.execute('SELECT i.index_code,i.index_name,COUNT(d.trade_date) rows,MIN(d.trade_date) start,MAX(d.trade_date) end,(SELECT COUNT(*) FROM fund_index f WHERE f.index_code=i.index_code) etfs FROM indexes i LEFT JOIN index_daily d USING(index_code) GROUP BY i.index_code ORDER BY i.index_code')],'mapping_checks':[dict(r) for r in db.execute('SELECT status,COUNT(*) count FROM mapping_checks GROUP BY status')],'jobs':[dict(r) for r in db.execute('SELECT * FROM jobs')]}
        if a.report:Path(a.report).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:db.close()

if __name__=='__main__': main()
