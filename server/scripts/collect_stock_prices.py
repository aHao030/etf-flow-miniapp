"""Prewarm or refresh the same bounded price windows used by the website.

Use the installed Scrapling Python. Only known stock codes are accepted. Historical
windows and unchanged current prices are cached; source failures preserve old data.
"""
import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import sqlite3
import tempfile
import time

SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent / 'web-trial' / '.local'
sys.path.insert(0, str(SERVER))
from history.stock_price_source import ENDPOINT, fetch_window, fetch_daily_window, symbol_for, window_bounds

VERSION = re.compile(r'^\d{14}-[a-f0-9]{32}$')
BEIJING = timezone(timedelta(hours=8))


def scheduled_gate(target, now=None, calendar_path=SERVER / 'data' / 'index_history.db'):
    """Skip scheduled work unless the known exchange calendar says today opened."""
    local = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    today = local.date().isoformat()
    if local.weekday() >= 5:
        return 'weekend'
    try:
        with closing(sqlite3.connect(calendar_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            first, last = db.execute('SELECT MIN(trade_date), MAX(trade_date) FROM calendar').fetchone()
            if not first or not first <= today <= last:
                return 'calendar_unverified'
            opened = db.execute('SELECT 1 FROM calendar WHERE trade_date=?', (today,)).fetchone()
    except sqlite3.Error:
        return 'calendar_unverified'
    if not opened:
        return 'market_closed'
    if (local.hour, local.minute) < (15, 30):
        return 'before_post_close'
    if target != today:
        return 'market_snapshot_pending'
    return None


def read_catalog(root=ROOT):
    selected = json.loads((root / 'local-view.json').read_text(encoding='utf-8'))
    version = selected.get('market_version', '')
    if not VERSION.fullmatch(version):
        raise ValueError('verified market snapshot is unavailable')
    folder = root / 'market-snapshots' / version
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != version:
        raise ValueError('market version mismatch')
    def verified(name):
        raw = (folder / (name + '.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest['hashes'].get(name):
            raise ValueError('market snapshot hash mismatch')
        return json.loads(raw)
    meta, catalog = verified('meta'), verified('catalog')
    return meta['target_end'], {s['stock_code']: s for s in catalog['items']}


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('cache time needs timezone')
    return parsed


def valid_cache(record, code, year):
    try:
        if not isinstance(record, dict) or record.get('schema') != 1 or record.get('stock_code') != code:
            return False
        if record.get('window_start') != f'{year}-01-01' or record.get('window_end') != f'{year+4}-12-31':
            return False
        through = date.fromisoformat(record['checked_through']).isoformat()
        if not record['window_start'] <= through <= record['window_end']:
            return False
        if record.get('source') != ENDPOINT or record.get('adjustment') != 'none' or record.get('price_unit') != '元/股':
            return False
        stamp(record['fetched_at'])
        rows = record['series']
        if not isinstance(rows, list) or len(rows) > 1500:
            return False
        previous = ''
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                return False
            day, close = row
            if date.fromisoformat(day).isoformat() != day or not record['window_start'] <= day <= through or day <= previous:
                return False
            if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or not 0 < close < 1e8:
                return False
            previous = day
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def cache_fresh(record, code, year, target, now):
    if not valid_cache(record, code, year):
        return False
    through = min(target, record['window_end'])
    age = (now - stamp(record['fetched_at'])).total_seconds()
    if age < 0 or record['checked_through'] < through:
        return False
    # Current missing target-day data may be disclosed later the same evening.
    pending = through == target and (not record['series'] or record['series'][-1][0] < through)
    return age < (2 * 3600 if pending else 30 * 86400)


def read_json(path):
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return None
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.price-', suffix='.tmp', delete=False) as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        temporary = Path(stream.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh_window(cache_root, code, year, target, now=None, fetcher=None):
    now = now or datetime.now(timezone.utc)
    _, through = window_bounds(year, target)
    path = cache_root / 'prices' / 'v1' / code / f'{year}.json'
    attempt_path = cache_root / 'prices' / 'attempts' / 'v1' / code / f'{year}.json'
    old = read_json(path)
    # An explicitly older historical request must never downgrade a newer cache.
    if (valid_cache(old, code, year) and old['checked_through'] > through) or cache_fresh(old, code, year, target, now):
        return {'stock_code': code, 'window': year, 'status': 'cached', 'rows': len(old['series'])}
    attempt = read_json(attempt_path)
    if isinstance(attempt, dict) and attempt.get('target_end') == through:
        try:
            retry_after = 900 if str(attempt.get('reason','')).startswith('price source returned HTTP 501') else 7200
            if 0 <= (now - stamp(attempt['attempted_at'])).total_seconds() < retry_after:
                return {'stock_code': code, 'window': year, 'status': 'backoff'}
        except (KeyError, ValueError):
            pass
    try:
        fetched = (fetcher or fetch_window)(code, year, through)
        rows = fetched['series'] if isinstance(fetched, dict) else fetched
        # Never erase a prior verified price history because a successful-shaped
        # response suddenly becomes empty or loses dates.
        if valid_cache(old, code, year) and not {r[0] for r in old['series'] if r[0] <= through}.issubset({r[0] for r in rows}):
            raise ValueError('source_history_regression')
        record = {'schema': 1, 'stock_code': code, 'window_start': f'{year}-01-01',
                  'window_end': f'{year+4}-12-31', 'checked_through': through,
                  'fetched_at': now.isoformat(timespec='milliseconds').replace('+00:00', 'Z'), 'source': ENDPOINT,
                  'adjustment': 'none', 'price_unit': '元/股', 'series': rows}
        if not valid_cache(record, code, year):
            raise ValueError('invalid_cache')
        if isinstance(fetched, dict) and 'candles' in fetched:
            # Independent optional raw OHLCV sidecar; never change the website's
            # close-price schema or relabel the provider's volume as shares.
            raw = {k:v for k,v in record.items() if k != 'series'}
            raw.update(columns=['date','open','close','high','low','provider_volume'],
                       volume_unit='provider_raw_unverified', candles=fetched['candles'])
            atomic_json(cache_root / 'prices' / 'ohlcv' / 'v1' / code / f'{year}.json', raw)
        atomic_json(path, record)
        return {'stock_code': code, 'window': year, 'status': 'success', 'rows': len(rows),
                'available_start': rows[0][0] if rows else None, 'available_end': rows[-1][0] if rows else None}
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        reason = reason[:160]
        atomic_json(attempt_path, {'attempted_at': now.isoformat(timespec='milliseconds').replace('+00:00', 'Z'), 'target_end': through, 'reason': reason})
        return {'stock_code': code, 'window': year, 'status': 'failed', 'reason': reason, 'retained_previous': valid_cache(old, code, year)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--codes', nargs='+')
    selection.add_argument('--refresh-cached', action='store_true')
    selection.add_argument('--all-listed', action='store_true')
    parser.add_argument('--universe-file', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--workers', type=int, choices=[1,2], default=2)
    parser.add_argument('--interval', type=float, default=.6)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--start', default='1990-01-01')
    parser.add_argument('--end')
    parser.add_argument('--cache-root', type=Path, default=ROOT / 'price-cache')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--max-jobs', type=int, default=3000)
    args = parser.parse_args()
    if args.all_listed:
        if not args.universe_file:
            parser.error('--all-listed requires a freshly verified --universe-file')
        if not 1 <= args.max_jobs <= 100000 or not .4 <= args.interval <= 60:
            parser.error('invalid task bound or interval; minimum global interval is 0.4 seconds')
        from stock_price_batch import run_batch
        return run_batch(args)
    if args.universe_file or args.checkpoint or args.dry_run:
        parser.error('universe/checkpoint/dry-run options require --all-listed')
    latest, stocks = read_catalog()
    target = args.end or latest
    first = date.fromisoformat(args.start).isoformat()
    date.fromisoformat(target)
    if not '1990-01-01' <= first <= target <= latest or not 1 <= args.max_jobs <= 10000:
        parser.error('invalid range or task bound; use only published closed-market dates')
    jobs = set()
    em_codes, em_report = set(), None
    if args.refresh_cached:
        skip = scheduled_gate(target)
        if skip:
            report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'target_end': target,
                      'status': 'skipped', 'reason': skip, 'attempted': 0, 'results': []}
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=True), flush=True)
            return
        from eastmoney_delta_maintenance import maintain
        em_codes, em_report = maintain(args.cache_root, stocks, target, args.max_jobs, interval=args.interval)
    if args.codes:
        for code in args.codes:
            if code not in stocks:
                parser.error('stock is outside the verified catalogue: ' + code)
            symbol_for(code)
            start = max(first, stocks[code].get('stock_listed_date') or first)
            if start > target:
                continue
            for year in range(1990 + 5 * ((int(start[:4]) - 1990) // 5), int(target[:4]) + 1, 5):
                jobs.add((code, year))
    else:
        # No full-market discovery: only price windows the user has already opened.
        for path in (args.cache_root / 'prices' / 'v1').glob('*/*.json'):
            code = path.parent.name
            if code in stocks and code not in em_codes and re.fullmatch(r'(199\d|20\d\d)\.json', path.name):
                year = int(path.stem)
                if year % 5 == 0 and year <= int(target[:4]):
                    jobs.add((code, year))
        # A failed first request has no successful record but still deserves a
        # bounded scheduled retry after the same backoff interval.
        for path in (args.cache_root / 'prices' / 'attempts' / 'v1').glob('*/*.json'):
            code = path.parent.name
            if code in stocks and code not in em_codes and re.fullmatch(r'(199\d|20\d\d)\.json', path.name):
                year = int(path.stem)
                if year % 5 == 0 and year <= int(target[:4]): jobs.add((code, year))
    results, attempted = [], em_report['network_requests'] if em_report else 0
    for code, year in sorted(jobs):
        if attempted >= args.max_jobs or (em_report and em_report['halt_remaining']):
            break
        item = refresh_window(args.cache_root, code, year, target)
        results.append(item)
        print(json.dumps(item, ensure_ascii=True), flush=True)
        if item['status'] in ('success', 'failed'):
            attempted += 1
            if item.get('reason', '').startswith(('price source returned HTTP 401', 'price source returned HTTP 403', 'price source returned HTTP 429')):
                break
            time.sleep(.4)
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'target_end': target,
              'eligible_windows': len(jobs), 'visited_windows': len(results), 'attempted': attempted,
              'adjustment': 'none', 'price_unit': '元/股', 'results': results}
    if em_report is not None:
        report['eastmoney'] = em_report
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__': main()
