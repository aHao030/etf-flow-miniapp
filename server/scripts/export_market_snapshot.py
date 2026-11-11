"""Factorized all-ETF disclosed-stock view; never expand ETF x stock x date."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

SERVER=Path(__file__).resolve().parents[1]
def encode(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
def connect(p):
    c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('BEGIN');return c

def merge_stock_catalog(legacy, listings):
    known={code:dict(row) for code,row in legacy.items()}
    for code,listing in listings.items():
        if code not in known:
            known[code]={'stock_code':code,'stock_name':listing.get('stock_name') or code,'industry_code':None,'industry_name':None}
        elif listing.get('stock_name'):
            known[code]['stock_name']=listing['stock_name']
    for code,stock in known.items():
        stock['stock_listed_date']=listings.get(code,{}).get('listed_date')
        stock['listing_status']='verified' if stock['stock_listed_date'] else 'unknown'
    return known

def export(parent,history_snapshot):
    hm=json.loads((history_snapshot/'manifest.json').read_text(encoding='utf-8'))
    def history(key):
        raw=(history_snapshot/(key+'.json')).read_bytes()
        if len(raw)>2*1024*1024 or hashlib.sha256(raw).hexdigest()!=hm['hashes'].get(key):raise ValueError('History hash mismatch')
        return json.loads(raw)
    source_meta=history('meta');end=source_meta['target_end'];start=source_meta['target_start']
    version=datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')+'-'+uuid.uuid4().hex
    generated=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    stage=parent/('.tmp-'+version);stage.mkdir(parents=True)
    files=[];hashes={}
    def write(k,v):
        raw=encode(v)
        if len(raw)>2*1024*1024:raise ValueError('Market shard exceeds 2 MiB: '+k)
        (stage/(k+'.json')).write_bytes(raw);files.append(k);hashes[k]=hashlib.sha256(raw).hexdigest()
    def shard(prefix,items):
        chunks={};current={};size=20;part=0
        for code,value in items:
            n=len(encode(value))+len(code)+5
            if current and size+n>1750000:
                write(prefix+str(part).zfill(4),current);part+=1;current={};size=20
            key=prefix+str(part).zfill(4);chunks[code]=key;current[code]=value;size+=n
        if current:write(prefix+str(part).zfill(4),current)
        return chunks
    with connect(SERVER/'data'/'stock_holdings.db') as hd,connect(SERVER/'data'/'app.db') as app,connect(SERVER/'data'/'index_history.db') as ix:
        dates=[r[0] for r in ix.execute('SELECT trade_date FROM calendar WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date',(start,end)) if datetime.fromisoformat(r[0]).weekday()<5]
        if not dates or dates[-1]!=end:raise ValueError('Calendar not ready')
        known={r['stock_code']:dict(r) for r in app.execute('SELECT * FROM stock_meta')}
        tables={r[0] for r in hd.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        listings={r['stock_code']:dict(r) for r in hd.execute('SELECT * FROM stock_listings')} if 'stock_listings' in tables else {}
        if len(listings)<4000:raise ValueError('Full official A-share catalogue is unavailable')
        # The legacy app catalogue was a holdings subset, not the A-share universe.
        # Preserve its industry fields while adding every verified exchange listing.
        known=merge_stock_catalog(known,listings)
        holdings={r['fund_code']:dict(r) for r in hd.execute('SELECT * FROM fund_holdings')}
        weights=defaultdict(list);by_fund=defaultdict(list)
        for r in hd.execute('SELECT * FROM stock_weights ORDER BY stock_code,fund_code'):
            if r['stock_code'] not in known or not 0<r['weight']<=1:raise ValueError('Invalid disclosed stock weight')
            if holdings.get(r['fund_code'],{}).get('status') not in ('ready','partial'):continue
            weights[r['stock_code']].append([r['fund_code'],r['weight']]);by_fund[r['fund_code']].append([r['stock_code'],r['weight']])
        funds=[];factors={};cached={}
        for f in history('catalog')['items']:
            code=f['fund_code'];holding=holdings.get(code,{})
            info={k:f.get(k) for k in ('fund_code','fund_name','market','category','listed_date','delisted_date')}
            info.update(status=holding.get('status','unavailable'),report_date=holding.get('report_date'),holdings_type=holding.get('holdings_type'),coverage_pct=holding.get('coverage_pct'),stock_count=len(by_fund.get(code,[])),source=holding.get('source'),error=holding.get('error'))
            for field in ('selection_notes','collector_version','identity_catalogue_hash'):
                if field in holding:
                    value=holding[field]
                    if field=='selection_notes' and isinstance(value,str):
                        try:value=json.loads(value)
                        except (ValueError,TypeError):value=[value]
                    if field=='selection_notes' and not isinstance(value,list):value=[] if value is None else [str(value)]
                    info[field]=value
            if by_fund.get(code):
                if f['file'] not in cached:cached[f['file']]=history(f['file'])['funds']
                factors[code]=[[r[0],r[4]] for r in cached[f['file']][code] if r[5]=='estimated' and r[4] is not None and (not f.get('listed_date') or r[0]>f['listed_date']) and (not f.get('delisted_date') or r[0]<f['delisted_date'])]
            info.update(factor_days=len(factors.get(code,[])),latest_factor_date=factors.get(code,[['',None]])[-1][0] if factors.get(code) else None,current_factor_ready=bool(factors.get(code) and factors[code][-1][0]==end))
            funds.append(info)
        included={f['fund_code'] for f in funds}
        weights={code:[[f,w] for f,w in entries if f in included] for code,entries in weights.items()}
        weights={code:entries for code,entries in weights.items() if entries}
        factor_files=shard('factors-',sorted(factors.items()))
        for f in funds:f['factor_file']=factor_files.get(f['fund_code'])
        weight_files=shard('weights-',sorted(weights.items()))
        ranks={};latest={}
        for period,n in [('1d',1),('5d',5),('20d',20)]:
            window=set(dates[-n:]);sums=defaultdict(float);observed=defaultdict(int)
            for code,rows in factors.items():
                valid=[(d,v) for d,v in rows if d in window]
                if not valid:continue
                for stock,weight in by_fund[code]:
                    listed=known[stock]['stock_listed_date']
                    eligible=[v for d,v in valid if not listed or d>=listed]
                    if eligible:sums[stock]+=sum(eligible)*weight;observed[stock]+=1
            ranks[period]=[{'stock_code':s,'flow':round(v,2),'n_etf':observed[s]} for s,v in sums.items()]
            if period=='1d':latest=sums
        catalog=[]
        for code,s in known.items():catalog.append({**s,'file':weight_files.get(code),'data_status':'disclosed_partial' if code in weights else 'no_data','flow_1d':round(latest[code],2) if code in latest else None})
        active=sum(bool(f['stock_count']) for f in funds);statuses=defaultdict(int)
        for f in funds:statuses[f['status']]+=1
        meta={'version':version,'generated_at':generated,'target_start':start,'target_end':end,'latest_date':end,'trade_date':end,'history_version':hm['version'],'total_funds':len(funds),'total_stocks':len(catalog),'covered_stocks':len(weights),'coverage':{'tracked_etfs':len(funds),'active_etfs':active,'pending_etfs':len(funds)-active,'statuses':dict(statuses)},'disclosure':{'short':'全目录逐只核验；资金为已披露A股持仓的估算贡献小计，不等于全市场真实净流入。'},'history_note':'按所选披露报告权重（各基金实际报告期见明细）计算的回溯情景，不是当时历史持仓或真实资金。已核验的个股上市前日期会剔除，未核验的上市边界单独说明；未披露持仓和未核验基金不能填零。'}
        meta['coverage']['current_factor_etfs']=sum(f['current_factor_ready'] for f in funds)
        meta['listing_coverage']={'verified':sum(bool(s['stock_listed_date']) for s in known.values()),'unknown':sum(not s['stock_listed_date'] for s in known.values())}
        meta['listing_sources']=sorted({(r['source'],r['source_url']) for r in listings.values()})
        collector_versions=defaultdict(int)
        for f in funds:collector_versions[str(f.get('collector_version','unknown'))]+=1
        meta['holdings_collector_versions']=dict(collector_versions)
        write('calendar',{'dates':dates});write('funds',{'items':funds});write('catalog',{'items':catalog});write('ranks',ranks)
        industries={s['industry_code']:s['industry_name'] for s in known.values() if s.get('industry_code')}
        write('industries',{'items':[{'industry_code':c,'industry_name':n} for c,n in industries.items()]})
        semantic_meta={k:v for k,v in meta.items() if k not in ('version','generated_at','data_revision')}
        meta['data_revision']=hashlib.sha256(encode({'hashes':hashes,'meta':semantic_meta})).hexdigest();write('meta',meta)
    if len(files)>500:raise ValueError('Market snapshot exceeds file bound')
    manifest={k:meta[k] for k in ['version','generated_at','target_start','target_end']};manifest.update(files=files,hashes=hashes)
    (stage/'manifest.json').write_bytes(encode(manifest));final=parent/version;stage.rename(final)
    print(json.dumps({'directory':str(final),'files':len(files),'funds':len(funds),'active':active,'covered_stocks':len(weights)},ensure_ascii=False),flush=True);return final

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('parent',type=Path);p.add_argument('--history-snapshot',type=Path,required=True);a=p.parse_args();export(a.parent,a.history_snapshot)
