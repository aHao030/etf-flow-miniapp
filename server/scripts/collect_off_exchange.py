"""Verify target-index fund classes and collect separately disclosed share flows.

The source reports shares, not cash. No aggregate with listed ETF flows is made.
All verified matching classes receive their available reported histories.
Absent/failed disclosures remain pending rather than becoming zero.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import json
import math
from pathlib import Path
import re
import threading
import time
from collect_index_history import connect, fetch_mapping, now, SERVER, throttle

SCHEMA='''
CREATE TABLE IF NOT EXISTS off_exchange_members(fund_code TEXT PRIMARY KEY,fund_name TEXT NOT NULL,index_code TEXT NOT NULL,share_class TEXT,fund_type TEXT NOT NULL,source_url TEXT NOT NULL,report_status TEXT NOT NULL,benchmark TEXT,verified_at TEXT);
CREATE TABLE IF NOT EXISTS off_exchange_reports(fund_code TEXT NOT NULL,period_start TEXT,period_end TEXT NOT NULL,subscription_shares REAL,redemption_shares REAL,net_subscription_shares REAL,end_shares REAL,share_class TEXT,merged_codes TEXT,source_url TEXT NOT NULL,PRIMARY KEY(fund_code,period_end));
CREATE TABLE IF NOT EXISTS off_exchange_checks(fund_code TEXT PRIMARY KEY,status TEXT NOT NULL,error TEXT,checked_at TEXT NOT NULL);
'''
_report_lock=threading.Lock();_report_counts={}

def num(value):
    if value in (None,'','---','--'):return None
    v=float(value)
    if not math.isfinite(v) or v<0:raise ValueError('invalid disclosed shares')
    return v

def parse_reports(text,code,share_class):
    # JSONDecoder only reads the documented data array. Never execute provider JS.
    m=re.search(r'"data"\s*:\s*(\[)',text)
    if not m or '亿份' not in text:raise ValueError('missing report array or declared units')
    raw,_=json.JSONDecoder().raw_decode(text[m.start(1):]);out=[];seen=set()
    if not isinstance(raw,list) or len(raw)>1000:raise ValueError('invalid report count')
    for r in sorted(raw,key=lambda x:x.get('FSRQ','')):
        if str(r.get('BZDM'))!=code:raise ValueError('report identity mismatch')
        day=str(r['FSRQ']);date.fromisoformat(day)
        if day[5:] not in ('03-31','06-30','09-30','12-31'):continue
        if day in seen:raise ValueError('duplicate report period')
        seen.add(day)
        s=num(r.get('QJSG'));red=num(r.get('QJSH'));end=num(r.get('QMZFE'))
        if s is None and red is None:continue
        # Period dates come from the prior disclosed report, never a trading day.
        quarter_start=date(int(day[:4]),((int(day[5:7])-1)//3)*3+1,1)
        preceding=(quarter_start-timedelta(days=1)).isoformat()
        start=quarter_start.isoformat() if out and out[-1]['period_end']==preceding else None
        out.append({'fund_code':code,'period_start':start,'period_end':day,'subscription_shares':s,'redemption_shares':red,'net_subscription_shares':s-red if s is not None and red is not None else None,'end_shares':end,'share_class':share_class,'merged_codes':str(r.get('MERGECODE') or ''),'source_url':f'https://fundf10.eastmoney.com/gmbd_{code}.html'})
    return out

def process(row,limit):
    from scrapling.fetchers import Fetcher
    code=row['基金代码'];name=row['基金简称']
    if 'ETF' in name.upper() and '联接' not in name:return {'code':code,'status':'listed_etf_excluded'}
    benchmark,index,profile=fetch_mapping(code)
    if not index:return {'code':code,'status':'other_index'}
    match=re.search(r'([A-Z])(?:\([^)]*\))?$',name);share_class=match[1] if match else '未标注'
    result={'code':code,'status':'verified','name':name,'index':index,'class':share_class,'type':'feeder' if '联接' in name else 'index_fund','source':profile,'benchmark':benchmark,'reports':[],'report_status':'pending'}
    with _report_lock:
        selected=not limit or _report_counts.get(index,0)<limit
        if selected:_report_counts[index]=_report_counts.get(index,0)+1
    if selected:
        try:
            url=f'https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&code={code}&mode=0&rt=0.123'
            throttle()
            r=Fetcher.get(url,headers={'Referer':f'https://fundf10.eastmoney.com/gmbd_{code}.html'},timeout=20,retries=0)
            if r.status!=200:raise ValueError('HTTP '+str(r.status))
            result['reports']=parse_reports(r.body.decode('utf-8'),code,share_class)
            result['report_status']='observed' if result['reports'] else 'pending'
        except Exception as exc:
            result['report_status']='failed';result['error']=type(exc).__name__+': '+str(exc)[:120]
            with _report_lock:_report_counts[index]-=1
    return result

def store_result(db, result):
    """Stop serving disproved memberships while retaining raw reported history."""
    code=result['code']
    with db:
        db.execute('INSERT OR REPLACE INTO off_exchange_checks VALUES(?,?,?,?)',
                   (code,result['status'],result.get('error'),now()))
        if result['status'] in ('other_index','listed_etf_excluded'):
            # An affirmative classification change differs from a source failure.
            # Export joins current members, so retained reports leave that scope.
            db.execute('DELETE FROM off_exchange_members WHERE fund_code=?',(code,))
        elif result['status']=='verified':
            db.execute('INSERT OR REPLACE INTO off_exchange_members VALUES(?,?,?,?,?,?,?,?,?)',
                       (code,result['name'],result['index'],result['class'],result['type'],result['source'],result['report_status'],result['benchmark'],now()))
            for r in result['reports']:
                db.execute('INSERT OR REPLACE INTO off_exchange_reports VALUES(?,?,?,?,?,?,?,?,?,?)',
                           tuple(r[k] for k in ['fund_code','period_start','period_end','subscription_shares','redemption_shares','net_subscription_shares','end_shares','share_class','merged_codes','source_url']))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',default=str(SERVER/'data'/'index_history.db'));p.add_argument('--catalog',default=str(SERVER/'data'/'off_exchange_catalog_candidates.json'));p.add_argument('--per-index-reports',type=int,default=0);p.add_argument('--refresh-reports',action='store_true');p.add_argument('--recheck-unmapped',action='store_true');p.add_argument('--report',required=True);a=p.parse_args()
    db=connect(a.db);db.executescript(SCHEMA)
    raw=json.loads(Path(a.catalog).read_text(encoding='utf-8'))
    # Request primary classes first; all candidate classes remain eligible.
    # Classification is independently verified, never inferred from these names.
    raw.sort(key=lambda r:(not bool(re.search(r'A$',r['基金简称'])),r['基金代码']))
    done={r[0] for r in db.execute("SELECT c.fund_code FROM off_exchange_checks c LEFT JOIN off_exchange_members m USING(fund_code) WHERE c.status='other_index' OR (c.status='verified' AND m.report_status='observed')")}
    if a.refresh_reports:done={r[0] for r in db.execute("SELECT fund_code FROM off_exchange_checks WHERE status='other_index'")}
    if a.recheck_unmapped:done-= {r[0] for r in db.execute("SELECT fund_code FROM off_exchange_checks WHERE status='other_index'")}
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(process,r,a.per_index_reports):r['基金代码'] for r in raw if r['基金代码'] not in done}
        print('off_exchange_jobs='+str(len(jobs)),flush=True)
        for n,f in enumerate(as_completed(jobs),1):
            code=jobs[f]
            try:
                result=f.result()
                store_result(db,result)
            except Exception as exc:
                with db:db.execute('INSERT OR REPLACE INTO off_exchange_checks VALUES(?,?,?,?)',(code,'failed',type(exc).__name__+': '+str(exc)[:120],now()))
            if n%100==0 or n==len(jobs):print(json.dumps({'checked':n,'total':len(jobs),'members':db.execute('SELECT COUNT(*) FROM off_exchange_members').fetchone()[0],'reports':db.execute('SELECT COUNT(*) FROM off_exchange_reports').fetchone()[0]}),flush=True)
    report={'generated_at':now(),'candidate_classes':len(raw),'checks':[dict(r) for r in db.execute('SELECT status,COUNT(*) count FROM off_exchange_checks GROUP BY status')],'coverage':[dict(r) for r in db.execute('SELECT m.index_code,COUNT(DISTINCT m.fund_code) members,COUNT(r.period_end) reports,MIN(r.period_end) start,MAX(r.period_end) end FROM off_exchange_members m LEFT JOIN off_exchange_reports r USING(fund_code) GROUP BY m.index_code')],'note':'按基金页当前跟踪标的逐一核验，采集所有匹配类可用报告；不将份额折算资金，不把联接与ETF重复合计。基金名筛选仅用于候选发现，不证明全市场完整；当前指数映射不是历史指数成分重建。'}
    Path(a.report).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False),flush=True);db.close()

if __name__=='__main__':main()
