"""Export a consistent, private, versioned read-only web snapshot.

Usage: ETF_FLOW_DB_PATH=... python export_web_snapshot.py OUTPUT_PARENT
The source database is copied to memory before any read. The unique output
subdirectory is renamed into place only after all checks have passed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import app
import config
import db
import monitoring

LIMIT = 2 * 1024 * 1024
EXT = ('movement', 'etf_contributions', 'coverage', 'history_basis', 'history_note')


def series_totals_agree(series_sum, observed, count):
    """Account for cumulative rounding of independently displayed cents."""
    if series_sum is None or observed is None:
        return series_sum is None and observed is None
    return abs(series_sum-observed) <= max(0.2, count*0.005+0.011)


def dump(folder, key, value, files, hashes):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > LIMIT:
        raise ValueError(f'{key}: {len(raw)} bytes exceeds 2 MiB')
    (folder / f'{key}.json').write_bytes(raw)
    files.append(key)
    hashes[key] = hashlib.sha256(raw).hexdigest()


def pages(fn, **params):
    out = []
    page = 1
    while True:
        result = fn(page=page, page_size=100, **params)['data']
        out.extend(result['items'])
        if not result['has_more']:
            return out
        page += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output_parent', type=Path)
    parser.add_argument('--disk-snapshot', action='store_true', help='use a private temporary SQLite copy to reduce memory for large history')
    args = parser.parse_args()
    src_path = Path(os.environ.get('ETF_FLOW_DB_PATH', config.DB_PATH)).resolve(strict=True)
    now = datetime.now(timezone.utc)
    version = now.strftime('%Y%m%d%H%M%S') + '-' + uuid.uuid4().hex
    generated_at = now.isoformat(timespec='seconds').replace('+00:00', 'Z')
    parent = args.output_parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / version
    folder = Path(tempfile.mkdtemp(prefix='.building-', dir=parent))
    source = sqlite3.connect(src_path.as_uri() + '?mode=ro', uri=True)
    snapshot_file = None
    if args.disk_snapshot:
        fd, snapshot_file = tempfile.mkstemp(prefix='etf-export-', suffix='.sqlite')
        os.close(fd)
    mem = sqlite3.connect(snapshot_file or ':memory:')
    mem.row_factory = sqlite3.Row
    try:
        source.backup(mem)
        source.close()
        # Export-only indexes: the source database is never modified.
        mem.execute('CREATE INDEX IF NOT EXISTS export_flow_stock_date ON flow_by_etf(stock_code,trade_date,fund_code)')
        mem.execute('CREATE INDEX IF NOT EXISTS export_daily_stock_date ON flow_daily(stock_code,trade_date)')
        def query(sql, params=()):
            return [dict(row) for row in mem.execute(sql, params)]
        def query_one(sql, params=()):
            rows = query(sql, params)
            return rows[0] if rows else None
        db.query, db.query_one = query, query_one
        app.cache.invalidate()
        td = db.meta_get('latest_date')
        if not td:
            raise ValueError('数据库没有 latest_date')
        calendar_data = monitoring.calendar_snapshot(td)
        frozen = {period: monitoring.window_for(td,period,calendar_data) for period in config.STOCK_PERIODS}
        # One immutable calendar snapshot for every result in this export.
        def frozen_window(date, period, calendar_data=None):
            if date != td: raise ValueError('unexpected export date')
            return frozen[period]
        monitoring.window_for = frozen_window
        meta = app.meta()['data']
        meta.update(version=version, generated_at=generated_at, trade_date=td,
                    update_schedule={'timezone': 'Asia/Shanghai', 'times':['15:30','19:30','22:30'],
                                     'note':'工作日盘后检查；当日份额未披露则保留上期'})
        catalog = pages(app.stocks, q='')
        industries = app.industries()['data']
        etfs = pages(app.etfs, category=None)
        details = {r['etf_code']: app.etf_detail(r['etf_code'], trade_date=None)['data'] for r in etfs}
        ranks = {}
        for period in config.PERIODS:
            ranks[period] = pages(app.rank, period=period, direction='in', industry=None, trade_date=None) + pages(app.rank, period=period, direction='out', industry=None, trade_date=None)
        chunks = defaultdict(dict)
        count = 0
        for item in catalog:
            if item['data_status'] == 'no_data':
                continue
            code = item['stock_code']
            full = app.stock_detail(code, trade_date=None, period='1d')['data']
            base = {k:v for k,v in full.items() if k not in EXT}
            base['series'] = app._series_for(td,code,'5y')
            periods = {'1d': {k:full[k] for k in EXT}}
            derived = db.query_one('SELECT flow_1d_yuan FROM flow_derived WHERE trade_date=? AND stock_code=?', (td,code)) or {'flow_1d_yuan':None}
            for period in config.STOCK_PERIODS[1:]:
                ext = monitoring.stock_movement(code, td, period, derived)
                for c in ext['etf_contributions']:
                    c['weight_source'] = app._ws_enum(c['weight_source'])
                periods[period] = ext
            for period in config.STOCK_PERIODS:
                move = periods[period]['movement']
                actual = move['net_flow']
                if period in config.PERIODS:
                    expected = base[f'flow_{period}']
                    if (actual is None) != (expected is None) or (actual is not None and abs(actual-expected) > 0.2):
                        raise ValueError(f'{code} {period}: movement {actual} != derived {expected}')
                dates=set(frozen[period]['_dates'])
                series=[r for r in base['series'] if r['trade_date'] in dates and r['flow'] is not None]
                series_sum=sum(r['flow'] for r in series) if series else None
                observed=move['observed_net_flow']
                if not series_totals_agree(series_sum, observed, len(series)):
                    raise ValueError(f'{code} {period}: series {series_sum} != observed {observed}')
                if actual is not None:
                    contributions = periods[period]['etf_contributions']
                    total = sum(c['flow'] for c in contributions if c['flow'] is not None)
                    if abs(total-actual) > 0.2:
                        raise ValueError(f'{code} {period}: ETF sum {total} != net {actual}')
            # Shared, compact representation; the Worker expands it to the
            # established API shape before returning an individual stock.
            base['series_compact'] = [[r['trade_date'], r['flow'], r['intensity_bp']] for r in base.pop('series')]
            chunks['stocks-'+code[:4]][code] = {'base':base, 'periods':periods}
            count += 1
        files, hashes = [], {}
        for key, value in [('meta',meta),('catalog',{'items':catalog}),('ranks',ranks),('industries',industries),('etfs',{'items':etfs}),('etf-details',details)]:
            dump(folder,key,value,files,hashes)
        for key, value in sorted(chunks.items()):
            dump(folder,key,value,files,hashes)
        if len(files) > 200:
            raise ValueError(f'{len(files)} files exceeds maximum 200')
        manifest = {'version':version,'files':files,'hashes':hashes,'trade_date':td}
        (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        folder.rename(target)
        print(f'snapshot={target} date={td} stocks={len(catalog)} covered={count} files={len(files)}')
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        source.close()
        mem.close()
        if snapshot_file:
            Path(snapshot_file).unlink(missing_ok=True)

if __name__ == '__main__':
    main()
