"""Export bounded index overlays and verified ETF-group aggregates independently."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import history_queries as h

def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')

def aggregate(days, funds, observations, closes):
    """Absence is not zero; unknown lifetimes prevent a complete-group assertion."""
    result = []
    for day in days:
        expected = valid = unknown = 0
        observed = 0.0
        for f in funds:
            listed, delisted = f.get('listed_date'), f.get('delisted_date')
            if listed and day < listed or delisted and day >= delisted:
                continue
            if not listed:
                unknown += 1
                if not f.get('first_observed') or day < f['first_observed']:
                    continue
            expected += 1
            row = observations.get(f['fund_code'], {}).get(day)
            if row and row[5] == 'estimated' and row[4] is not None:
                valid += 1
                observed += row[4]
        observed = round(observed, 2) if valid else None
        complete = observed if expected > 0 and valid == expected and unknown == 0 else None
        result.append([day, observed, complete, valid, expected, unknown, closes.get(day)])
    return result

def latest_member(f, observations, end):
    rows = observations.get(f['fund_code'], {})
    current = rows.get(end)
    flow = current[4] if current and current[5] == 'estimated' else None
    shares = current[1] if current else None
    nav = current[2] if current else None
    return {**f, 'estimated_flow': flow, 'shares': shares, 'unit_nav': nav,
            'shares_date': end if shares is not None else None,
            'nav_date': end if nav is not None else None, 'flow_date': end if flow is not None else None,
            'status': 'observed' if current else 'pending'}

def export(parent, index_db, history_snapshot=None):
    version = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S') + '-' + uuid.uuid4().hex
    generated = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    staging = parent / ('.tmp-' + version)
    staging.mkdir(parents=True)
    files, hashes = [], {}
    def write(key, obj):
        payload = encode(obj)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError('Broad file exceeds bound: ' + key)
        (staging / (key + '.json')).write_bytes(payload)
        files.append(key)
        hashes[key] = hashlib.sha256(payload).hexdigest()
    with closing(h.connect()) as source, closing(sqlite3.connect(':memory:')) as db, closing(sqlite3.connect(index_db.resolve().as_uri()+'?mode=ro', uri=True)) as indexes:
        if not history_snapshot:
            source.backup(db)
        db.row_factory = indexes.row_factory = sqlite3.Row
        indexes.execute('BEGIN')
        end = h.target_end(db) if not history_snapshot else None
        linked_manifest = None
        linked_chunks = {}
        if history_snapshot:
            linked_manifest=json.loads((history_snapshot/'manifest.json').read_text(encoding='utf-8'))
            def linked_read(key):
                raw=(history_snapshot/(key+'.json')).read_bytes()
                if len(raw)>2*1024*1024 or hashlib.sha256(raw).hexdigest()!=linked_manifest['hashes'].get(key):
                    raise ValueError('Linked history snapshot file failed validation')
                return json.loads(raw)
            linked_meta=linked_read('meta')
            if linked_meta['version']!=linked_manifest['version'] or linked_meta['target_end']!=linked_manifest['target_end']:
                raise ValueError('Linked history snapshot does not match target date')
            end=linked_meta['target_end']
        days = [r[0] for r in indexes.execute('SELECT trade_date FROM calendar WHERE trade_date<=? ORDER BY trade_date', (end,)) if datetime.fromisoformat(r[0]).weekday()<5]
        if not days or len(days) > 12000 or days[-1] != end:
            raise ValueError('Full trading calendar does not cover target date')
        start = days[0]
        index_rows = [dict(r) for r in indexes.execute('SELECT * FROM indexes ORDER BY index_code')]
        mappings = [dict(r) for r in indexes.execute('SELECT * FROM fund_index ORDER BY fund_code,index_code')]
        if len({m['fund_code'] for m in mappings}) != len(mappings):
            raise ValueError('Conflicting or duplicate ETF index mapping')
        catalog = {f['fund_code']: f for f in (linked_read('catalog')['items'] if history_snapshot else h.catalog(db, end))}
        members, observations = [], {}
        for m in mappings:
            if m['fund_code'] not in catalog:
                continue
            f = catalog[m['fund_code']]
            if history_snapshot:
                key=f['file']
                if key not in linked_chunks:
                    linked_chunks[key]=linked_read(key)['funds']
                rows=linked_chunks[key][f['fund_code']]
            else:
                rows = h.history_rows(db, f['fund_code'], end)
            observations[f['fund_code']] = {r[0]: r for r in rows}
            members.append({'fund_code': f['fund_code'], 'fund_name': f['fund_name'],
                'market': f['market'], 'index_code': m['index_code'],
                'listed_date': f.get('listed_date'), 'delisted_date': f.get('delisted_date'),
                'first_observed': min((r[0] for r in rows), default=None),
                'mapping_source_url': m['source_url']})
        closes = {r['index_code']: {p[0]: p[1] for p in indexes.execute('SELECT trade_date,close FROM index_daily WHERE index_code=? AND trade_date<=? ORDER BY trade_date', (r['index_code'], end))} for r in index_rows}
        tables={r[0] for r in indexes.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        off_members=[dict(r) for r in indexes.execute('SELECT * FROM off_exchange_members ORDER BY fund_code')] if 'off_exchange_members' in tables else []
        # Raw reports survive a confirmed membership change for audit. Only
        # currently verified members belong in published groups or meta counts.
        off_reports=[dict(r) for r in indexes.execute('SELECT r.* FROM off_exchange_reports r JOIN off_exchange_members m USING(fund_code) WHERE r.period_end<=? ORDER BY r.period_end DESC,r.fund_code', (end,))] if {'off_exchange_reports','off_exchange_members'}.issubset(tables) else []
        off_by_code={m['fund_code']:m for m in off_members}
        descriptors = []
        groups = [('all', '全部已核验宽基 ETF', members, '000001')]
        for item in index_rows:
            code = item['index_code']
            group = [f for f in members if f['index_code'] == code]
            price_dates = sorted(closes[code])
            descriptors.append({'code': code, 'name': item['index_name'], 'fund_count': len(group),
                'available_start': price_dates[0] if price_dates else None, 'available_end': price_dates[-1] if price_dates else None,
                'source_url': item.get('source_url'), 'launch_date': item.get('launch_date'), 'base_date': item.get('base_date')})
            groups.append((code, item['index_name'], group, code))
        total = None
        for code, name, group, benchmark in groups:
            series = aggregate(days, group, observations, closes.get(benchmark, {}))
            if code == 'all':
                r = series[-1]
                total = dict(zip(['observed_flow','estimated_flow','valid_funds','expected_funds','unknown_lifecycle_funds'],r[1:6]))
            description = {'code': code, 'name': name, 'benchmark_code': benchmark,
                'benchmark_name': next((x['name'] for x in descriptors if x['code']==benchmark), '上证综指'), 'fund_count': len(group)}
            members_off=[m for m in off_members if code=='all' or m['index_code']==code]
            codes_off={m['fund_code'] for m in members_off}
            reports_off=[{**r,'fund_name':off_by_code[r['fund_code']]['fund_name']} for r in off_reports if r['fund_code'] in codes_off]
            off_files=[];off_ranges={}
            for part in range(0,len(reports_off),1000):
                key='off-'+code+'-'+str(part//1000).zfill(4)
                page=reports_off[part:part+1000]
                write(key,{'reports':page});off_files.append(key)
                off_ranges[key]={'start':min(r['period_end'] for r in page),'end':max(r['period_end'] for r in page),'count':len(page)}
            off={'members':members_off,'report_count':len(reports_off),'files':off_files,'file_ranges':off_ranges,'combined_total':None,
                'combined_reason':'frequency_or_double_count', 'notes':['场外仅展示已核验披露期的申购/赎回份额，不代表每日资金流；源缺失保留为空。','A/C或其他份额类及源合并代码可能重叠，逐条展示，不与ETF日频申赎直接相加。','场外目录和报告仍在逐只核验，当前可用记录不代表全部场外基金完整历史。']}
            write('index-'+code, {'index': description, 'members': [latest_member(f, observations, end) for f in group], 'rows': series,'off_exchange':off})
        notes = ['指数行情从来源最早可核验日期显示，基日回溯与正式发布日不同；ETF 不存在的年代没有申赎记录。',
                 '合计范围为已核验跟踪指数映射的 ETF，不保证含所有已退市或未披露产品；全组合计按基金代码去重。',
                 '指数归组依据当前核验的跟踪标的，历史改标的尚未逐期还原；分组历史不代表当时的完整产品清单。',
                 '已有净额为有效基金小计；缺失、未核验、上市边界或未知存续期使完整合计留空，缺失不填零。',
                 '净申赎按相邻交易日份额差乘前日净值估算，不能识别投资者。全部组合的黑线仅为上证综指参考。']
        meta = {'version':version,'generated_at':generated,'target_start':start,'target_end':end,'indexes':descriptors,
                'total':total,'total_funds':len(members),'mapping_complete':False,'notes':notes,'etf_target_start':linked_meta['target_start'] if history_snapshot else h.TARGET_START}
        meta['off_exchange']={'members':len(off_members),'report_funds':len({r['fund_code'] for r in off_reports}),'reports':len(off_reports),'complete':False}
        if 'mapping_checks' in tables:
            meta['mapping_checks']={r[0]:r[1] for r in indexes.execute('SELECT status,COUNT(*) FROM mapping_checks GROUP BY status')}
        if history_snapshot:
            meta['history_version']=linked_meta['version']
        write('calendar', {'dates':days,'source':'akshare_sina_trade_calendar'})
        semantic_meta={k:v for k,v in meta.items() if k not in ('version','generated_at','data_revision')}
        meta['data_revision'] = hashlib.sha256(encode({'hashes':hashes,'meta':semantic_meta})).hexdigest()
        write('meta', meta)
        manifest = {k:meta[k] for k in ['version','generated_at','target_start','target_end']}
        manifest.update(files=files, hashes=hashes)
        (staging/'manifest.json').write_bytes(encode(manifest))
    final = parent/version
    staging.rename(final)
    print(json.dumps({'directory':str(final),'funds':len(members),'files':len(files),'start':start,'end':end}, ensure_ascii=False),flush=True)
    return final

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('parent', type=Path)
    parser.add_argument('--index-db', type=Path, default=SERVER/'data'/'index_history.db')
    parser.add_argument('--history-snapshot', type=Path)
    args=parser.parse_args()
    export(args.parent,args.index_db,args.history_snapshot)
