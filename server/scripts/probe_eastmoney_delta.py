"""Read-only, at-most-two same-provider short-range probes after full backfill.

Only desktop evidence files are written. Price caches, listing manifests and
collection checkpoints are never changed, and no incremental merge is performed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import time

import collect_stock_prices as p
from history import eastmoney_stock_prices as source

CODES = ('600519', '000001')
LOG_ROOT = Path.home() / 'Desktop' / 'ETF更新日志'


def fetch_once(stock, start, end):
    from scrapling.fetchers import Fetcher
    # Fixed public selector, not a user credential. Never log params or headers.
    params = {'fields1': 'f1,f2,f3,f4,f5,f6',
              'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116',
              'ut': '7eea3edcaed734bea9cbfc24409ed989', 'klt': '101', 'fqt': '0',
              'secid': str(source.market_id(stock['market'])) + '.' + stock['stock_code'],
              'beg': start.replace('-', ''), 'end': end.replace('-', '')}
    response = Fetcher.get(source.SOURCE, params=params, timeout=30, retries=0,
                           follow_redirects=False, stealthy_headers=False)
    return response.status, response.body


def inspect_payload(body, stock, record, start, end):
    """Determine the reported total's scope without relabeling an assembled count."""
    if not isinstance(body, dict) or type(body.get('rc')) is not int or body['rc'] != 0:
        raise ValueError('source_response_not_successful')
    data = body.get('data')
    if not isinstance(data, dict) or data.get('code') != stock['stock_code'] or type(data.get('market')) is not int or data['market'] != source.market_id(stock['market']):
        raise ValueError('source_security_identity_mismatch')
    lines, total = data.get('klines'), data.get('dktotal')
    if not isinstance(lines, list) or len(lines) > source.MAX_ROWS or type(total) is not int or not 0 <= total <= source.MAX_ROWS:
        raise ValueError('invalid_source_count')
    candles = []
    for line in lines:
        if not isinstance(line, str):
            raise ValueError('invalid_kline_format')
        fields = line.split(',')
        if len(fields) < 7:
            raise ValueError('invalid_kline_fields')
        try:
            candles.append([fields[0], *[float(value) for value in fields[1:7]]])
        except ValueError as exc:
            raise ValueError('invalid_numeric_observation') from exc
    source.validate_candles(candles, start, end)
    expected = [row for row in record['candles'] if start <= row[0] <= end]
    expected_by_date = {row[0]: row for row in expected}
    received_by_date = {row[0]: row for row in candles}
    missing = sorted(set(expected_by_date) - set(received_by_date))
    extra = sorted(set(received_by_date) - set(expected_by_date))
    changed = [day for day in sorted(set(expected_by_date) & set(received_by_date))
               if expected_by_date[day] != received_by_date[day]]
    range_match, full_match = total == len(candles), total == record['provider_total']
    scope = 'ambiguous' if range_match and full_match else 'requested_range' if range_match else 'cached_full_history' if full_match else 'unresolved'
    overlap_equal = candles == expected
    return {'provider_total': total, 'returned_rows': len(candles),
            'cached_full_provider_total': record['provider_total'], 'expected_overlap_rows': len(expected),
            'reported_total_scope': scope, 'total_matches_range_rows': range_match,
            'total_matches_cached_full_rows': full_match, 'overlap_all_seven_fields_equal': overlap_equal,
            'missing_cached_dates': missing, 'extra_source_dates': extra, 'changed_cached_dates': changed,
            'available_start': candles[0][0] if candles else None,
            'available_end': candles[-1][0] if candles else None,
            'positive_volume_rows': sum(row[5] > 0 for row in candles),
            'short_range_matches_cached_history': overlap_equal and scope in ('requested_range', 'cached_full_history')}


def evidence_paths(report, now, save_responses):
    report = report.resolve()
    today = now.astimezone(p.BEIJING).date().isoformat()
    directory = (LOG_ROOT / today).resolve()
    if report.parent != directory or report.suffix != '.json' or not report.name.startswith('etf-'):
        raise ValueError('report_must_be_unique_json_in_today_desktop_log_directory')
    paths = [report]
    if save_responses:
        paths += [report.with_name(report.stem + '-' + code + '-response.json') for code in CODES]
    if any(item.exists() for item in paths):
        raise ValueError('evidence_file_already_exists')
    return report


def save_json_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def prepare(cache_root, target, catalog, now):
    progress = p.read_json(cache_root / 'progress.json')
    if not isinstance(progress, dict) or progress.get('provider') != 'eastmoney' or progress.get('target_end') != target or progress.get('status') != 'finished_pass':
        raise ValueError('full_eastmoney_batch_not_finished_for_target')
    plan = []
    for code in CODES:
        item = catalog.get(code)
        if not isinstance(item, dict) or not item.get('stock_listed_date'):
            raise ValueError('verified_stock_listing_missing')
        stock = {'stock_code': code, 'stock_name': item['stock_name'],
                 'market': p.symbol_for(code)[:2].upper(), 'listed_date': item['stock_listed_date']}
        path = cache_root / 'prices' / 'eastmoney' / 'v1' / (code + '.json')
        record = p.read_json(path)
        if not source.valid_record(record, stock) or record['checked_through'] != target or len(record['candles']) < 5 or p.stamp(record['fetched_at']) > now:
            raise ValueError('verified_full_target_cache_required')
        start = record['candles'][-5][0]
        plan.append({'stock': stock, 'record': record, 'path': path, 'start': start,
                     'cache_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return plan


def probe(args, fetcher=None, now=None, sleeper=None, monotonic=None):
    now = now or datetime.now(timezone.utc)
    if not .6 <= args.interval <= 60:
        raise ValueError('invalid_probe_interval')
    # Suppress library messages that may include a query or request headers.
    logging.disable(logging.CRITICAL)
    report_path = evidence_paths(args.report, now, args.save_responses)
    report = {'schema': 1, 'kind': 'eastmoney-read-only-short-range-probe',
              'generated_at': now.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
              'source': source.SOURCE, 'provider': 'eastmoney', 'klt': 101, 'fqt': 0,
              'adjustment': 'none', 'network_requests': 0, 'max_requests': 2,
              'status': 'not_started', 'results': [], 'price_cache_writes': 0,
              'incremental_merge_performed': False, 'production_incremental_contract_verified': False}
    plan = []
    try:
        target, catalog = p.read_catalog()
        report['target_end'] = target
        skip = p.scheduled_gate(target, now)
        if skip:
            report.update(status='skipped', reason=skip)
        else:
            plan = prepare(args.cache_root, target, catalog, now)
            last_start = None
            sleeper, monotonic = sleeper or time.sleep, monotonic or time.monotonic
            report['status'] = 'completed'
            for item in plan:
                if hashlib.sha256(item['path'].read_bytes()).hexdigest() != item['cache_sha256']:
                    report.update(status='stopped', reason='cache_changed_before_probe')
                    break
                if last_start is not None:
                    sleeper(max(0., args.interval - (monotonic() - last_start)))
                last_start = monotonic()
                result = {'stock_code': item['stock']['stock_code'], 'market': item['stock']['market'],
                          'listed_date': item['stock']['listed_date'], 'start': item['start'], 'end': target,
                          'cache_sha256_before': item['cache_sha256']}
                report['results'].append(result)
                report['network_requests'] += 1
                try:
                    status, raw = (fetcher or fetch_once)(item['stock'], item['start'], target)
                    result['http_status'] = status
                    if status != 200:
                        result['status'] = 'http_error'
                        report.update(status='stopped', reason='source_access_limited' if status in (401, 403, 429) else 'source_service_error' if 500 <= status <= 599 else 'unexpected_http_status')
                        break  # No redirects, retries, second-stock fallback, or alternate host.
                    if not isinstance(raw, bytes) or len(raw) > source.MAX_BYTES:
                        raise ValueError('invalid_or_oversized_response')
                    result['response_sha256'] = hashlib.sha256(raw).hexdigest()
                    body = json.loads(raw)
                    result.update(inspect_payload(body, item['stock'], item['record'], item['start'], target))
                    if args.save_responses:
                        response_path = report_path.with_name(report_path.stem + '-' + item['stock']['stock_code'] + '-response.json')
                        # Store only the public response body, never query strings or headers.
                        response_path.parent.mkdir(parents=True, exist_ok=True)
                        with response_path.open('xb') as stream:
                            stream.write(raw)
                        result['response_file'] = str(response_path)
                    result['status'] = 'observed' if result['short_range_matches_cached_history'] else 'unresolved'
                    if result['status'] != 'observed':
                        report.update(status='stopped', reason='short_range_semantics_unresolved')
                        break
                except Exception as exc:
                    # Never include arbitrary external exception text (may contain a full URL).
                    result.update(status='validation_or_transport_error', error_type=type(exc).__name__)
                    report.update(status='stopped', reason='validation_or_transport_error')
                    break
    except Exception as exc:
        known = {'full_eastmoney_batch_not_finished_for_target', 'verified_stock_listing_missing',
                 'verified_full_target_cache_required'}
        report.update(status='skipped', reason=str(exc) if isinstance(exc, ValueError) and str(exc) in known else 'local_precondition_error')
    finally:
        def unchanged(item):
            try:
                return hashlib.sha256(item['path'].read_bytes()).hexdigest() == item['cache_sha256']
            except OSError:
                return False
        report['cache_hashes_unchanged'] = all(unchanged(item) for item in plan)
        if not report['cache_hashes_unchanged']:
            report.update(status='stopped', reason='source_cache_changed_during_probe')
        save_json_new(report_path, report)
    print(json.dumps({'status': report['status'], 'network_requests': report['network_requests'],
                      'report': str(report_path)}, ensure_ascii=True), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-root', type=Path, default=p.ROOT / 'price-cache')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--save-responses', action='store_true')
    parser.add_argument('--interval', type=float, default=.6)
    args = parser.parse_args()
    if not .6 <= args.interval <= 60:
        parser.error('interval must be 0.6 to 60 seconds')
    probe(args)


if __name__ == '__main__':
    main()
