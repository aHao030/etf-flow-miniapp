"""Export an auditable current A-share membership set from a same-day full directory run.

The date table is append/upsert-only, so its rows alone do not prove current membership.
A successful zero-rejection/zero-conflict full-directory report plus exactly matching
per-exchange and total table cardinalities proves that no stale extra rows remain.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
BJ = timezone(timedelta(hours=8))
SOURCES = {
    'SSE official stock directory': ('SH', 'https://www.sse.com.cn/assortment/stock/list/share/'),
    'SZSE official A-share directory': ('SZ', 'https://www.szse.cn/market/product/stock/list/index.html'),
}


def export(db_path: Path, evidence_path: Path, blocked_evidence_path: Path, as_of: str, output_dir: Path):
    day = date.fromisoformat(as_of)
    if day > datetime.now(BJ).date():
        raise ValueError('A future date cannot be a verified current membership date')
    evidence_raw = evidence_path.read_bytes()
    evidence = json.loads(evidence_raw)
    observed = datetime.fromisoformat(evidence['observed_at'].replace('Z', '+00:00'))
    if observed.tzinfo is None or observed.astimezone(BJ).date() != day:
        raise ValueError('Directory evidence must be from the requested Beijing date')
    entries = {x['exchange']: x for x in evidence['exchanges']}
    expected = {}
    for name, market in [('SSE main board', 'SH'), ('SSE STAR', 'SH'), ('SZSE A-share', 'SZ')]:
        item = entries[name]
        if item['status'] != 'observed' or item['rejected_rows'] != 0 or item['conflicts'] or not isinstance(item['verified_rows'], int) or item['verified_rows'] <= 0:
            raise ValueError('A partial directory result cannot prove current membership')
        expected[market] = expected.get(market, 0) + item['verified_rows']
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        records = [dict(r) for r in connection.execute('SELECT stock_code,stock_name,listed_date,source,source_url FROM stock_listings ORDER BY stock_code')]
    counts = {'SH': 0, 'SZ': 0}
    stocks = []
    for row in records:
        source = SOURCES.get(row['source'])
        if source is None or row['source_url'] != source[1]:
            raise ValueError('Unknown listing provenance')
        market = source[0]
        if not re.fullmatch(r'\d{6}', row['stock_code']) or not row['stock_name'] or not row['stock_name'].strip() or not date(1990, 1, 1) <= date.fromisoformat(row['listed_date']) <= day:
            raise ValueError('Invalid explicit exchange identity or listing date')
        if market == 'SH' and not row['stock_code'].startswith('6') or market == 'SZ' and row['stock_code'][0] not in '03':
            raise ValueError('Exchange and security identity disagree')
        counts[market] += 1
        stocks.append({**row, 'market': market, 'lifecycle_status': 'listed', 'observed_at': evidence['observed_at']})
    if counts != expected or len(stocks) != evidence['total_verified'] or len(stocks) != evidence.get('verified_with_name') or len({x['stock_code'] for x in stocks}) != len(stocks):
        raise ValueError('Stored directory has stale extras, missing rows, or unverified names')
    blocked_raw = blocked_evidence_path.read_bytes()
    blocked = json.loads(blocked_raw)
    blocked_at = datetime.fromisoformat(blocked['observed_at'].replace('Z', '+00:00'))
    bj = next((x for x in blocked.get('exchanges', []) if x.get('exchange') == 'BSE'), None)
    if blocked_at.tzinfo is None or blocked_at.astimezone(BJ).date() != day or not bj or bj.get('status') != 'blocked' or bj.get('http_status') != 403:
        raise ValueError('BSE gap must carry the explicit same-day access-failure evidence')
    config = json.loads((ROOT / 'web-trial/.local/local-view.json').read_text('utf8'))
    target_end = config['market_target']
    if date.fromisoformat(target_end) > day:
        raise ValueError('Published market target cannot be in the future')
    catalog_path = ROOT / 'web-trial/.local/market-snapshots' / config['market_version'] / 'catalog.json'
    catalog = json.loads(catalog_path.read_text('utf8'))['items']
    codes = {x['stock_code'] for x in stocks}
    gaps = [{'market': 'BJ', 'status': 'unverified', 'reason': 'official_directory_access_denied', 'http_status': 403, 'source_url': bj['source_url'], 'observed_at': blocked['observed_at'], 'known_current_count': None}]
    doc = {
        'schema': 1, 'kind': 'current-listed-a-shares', 'as_of': as_of,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        'target_end': target_end, 'market_version': config['market_version'],
        'complete': False, 'requested_markets': ['SH', 'SZ', 'BJ'], 'verified_markets': ['SH', 'SZ'],
        'stock_count': len(stocks), 'exchanges': [{'market': market, 'status': 'verified', 'count': count, 'observed_at': evidence['observed_at']} for market, count in counts.items()] + gaps,
        'evidence': {'directory_report': str(evidence_path.resolve()), 'directory_report_sha256': hashlib.sha256(evidence_raw).hexdigest(), 'blocked_report': str(blocked_evidence_path.resolve()), 'blocked_report_sha256': hashlib.sha256(blocked_raw).hexdigest(), 'stock_rows_sha256': hashlib.sha256(json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest(), 'verification': 'same-day complete zero-rejection/conflict source counts equal per-exchange and total upsert table cardinalities'},
        'gaps': gaps,
        'exclusions': [{'stock_code': x['stock_code'], 'reason': 'not_in_verified_current_exchange_directory'} for x in catalog if x['stock_code'] not in codes],
        'stocks': stocks,
        'notes': ['Only securities explicitly present in the complete current exchange directory are included.', 'Missing Beijing exchange membership is unknown, not zero; no inferred listing dates or price-presence identities are substituted.', 'Universe membership and price-history completeness are separate.']
    }
    raw = json.dumps(doc, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')
    digest = hashlib.sha256(raw).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / ('current-listed-' + day.strftime('%Y%m%d') + '-' + datetime.now(timezone.utc).strftime('%H%M%S') + '-' + digest[:12] + '.json')
    with path.open('xb') as f:
        f.write(raw)
    return {'path': str(path.resolve()), 'sha256': digest, 'as_of': as_of, 'target_end': target_end, 'stock_count': len(stocks), 'counts': counts, 'complete': False, 'exclusions': doc['exclusions'], 'gaps': gaps}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'server/data/stock_holdings.db')
    parser.add_argument('--evidence-report', type=Path, required=True)
    parser.add_argument('--blocked-evidence-report', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'web-trial/.local/price-universes')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = export(args.db, args.evidence_report, args.blocked_evidence_report, args.as_of, args.output_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
