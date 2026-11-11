from argparse import Namespace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER / 'scripts'))
import probe_eastmoney_delta as probe

TARGET = '2026-11-10'
NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
DAYS = ['2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-09', TARGET]


class ProbeTests(unittest.TestCase):
    def test_scrapling_request_uses_only_fixed_source_and_no_retry_or_redirect(self):
        package = ModuleType('scrapling'); package.__path__ = []
        fetchers = ModuleType('scrapling.fetchers')
        get = Mock(return_value=SimpleNamespace(status=200, body=b'{}'))
        fetchers.Fetcher = SimpleNamespace(get=get)
        stock = {'stock_code': '000001', 'market': 'SZ'}
        with patch.dict(sys.modules, {'scrapling': package, 'scrapling.fetchers': fetchers}):
            self.assertEqual(probe.fetch_once(stock, '2026-11-04', TARGET), (200, b'{}'))
        get.assert_called_once(); positional, options = get.call_args
        self.assertEqual(positional, (probe.source.SOURCE,)); self.assertEqual(options['params']['secid'], '0.000001')
        self.assertEqual(options['params']['beg'], '20261104'); self.assertEqual(options['params']['end'], '20261110')
        self.assertEqual(options['params']['fqt'], '0'); self.assertEqual(options['params']['klt'], '101')
        self.assertEqual(options['retries'], 0); self.assertFalse(options['follow_redirects']); self.assertFalse(options['stealthy_headers'])
        self.assertNotIn('proxy', options); self.assertNotIn('proxies', options); self.assertNotIn('headers', options)

    def fixture(self, directory, **changes):
        root = Path(directory)
        args = Namespace(cache_root=root / 'cache', report=root / 'logs' / TARGET / 'etf-delta-probe.json',
                         save_responses=True, interval=.6, **changes)
        catalog = {}
        records = {}
        for code in probe.CODES:
            listed = '2001-08-27' if code == '600519' else '1991-04-03'
            catalog[code] = {'stock_code': code, 'stock_name': 'sample', 'stock_listed_date': listed}
            stock = {'stock_code': code, 'stock_name': 'sample', 'market': 'SH' if code == '600519' else 'SZ', 'listed_date': listed}
            rows = [[day, 10, 11, 12, 9, 100, 1000] for day in DAYS]
            bundle = {'provider_total': len(rows), 'candles': rows, 'series': [[row[0], row[2]] for row in rows]}
            record = probe.source.make_record(stock, TARGET, bundle, '2026-11-10T08:00:00Z')
            probe.p.atomic_json(args.cache_root / 'prices/eastmoney/v1' / (code + '.json'), record)
            records[code] = record
        probe.p.atomic_json(args.cache_root / 'progress.json', {'provider': 'eastmoney', 'status': 'finished_pass', 'target_end': TARGET})
        # A sentinel ensures the probe never edits or creates collection checkpoints.
        (args.cache_root / 'checkpoint-sentinel').write_bytes(b'unchanged')
        return root, args, catalog, records

    def response(self, stock, start, end, records, full_total=False):
        rows = [row for row in records[stock['stock_code']]['candles'] if start <= row[0] <= end]
        return {'rc': 0, 'data': {'code': stock['stock_code'], 'market': probe.source.market_id(stock['market']),
                'dktotal': len(records[stock['stock_code']]['candles']) if full_total else len(rows),
                'klines': [','.join(map(str, row)) for row in rows]}}

    def execute(self, root, args, catalog, fetcher, **kwargs):
        with patch.object(probe, 'LOG_ROOT', root / 'logs'), patch.object(probe.p, 'read_catalog', return_value=(TARGET, catalog)), patch.object(probe.p, 'scheduled_gate', return_value=None):
            return probe.probe(args, fetcher, NOW, **kwargs)

    def test_two_fixed_probes_compare_all_columns_and_leave_all_cache_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root, args, catalog, records = self.fixture(directory)
            before = {str(path.relative_to(args.cache_root)): path.read_bytes() for path in args.cache_root.rglob('*') if path.is_file()}
            clock = [0.]
            calls = []
            def sleep(seconds):
                clock[0] += seconds
            def fetch(stock, start, end):
                calls.append((stock['stock_code'], start, end, clock[0]))
                return 200, json.dumps(self.response(stock, start, end, records, full_total=stock['stock_code'] == '000001')).encode()
            result = self.execute(root, args, catalog, fetch, sleeper=sleep, monotonic=lambda: clock[0])
            self.assertEqual(result['status'], 'completed'); self.assertEqual(result['network_requests'], 2)
            self.assertEqual([call[:3] for call in calls], [(code, DAYS[-5], TARGET) for code in probe.CODES])
            self.assertGreaterEqual(calls[1][3] - calls[0][3], .6)
            self.assertEqual([row['reported_total_scope'] for row in result['results']], ['requested_range', 'cached_full_history'])
            self.assertTrue(all(row['overlap_all_seven_fields_equal'] for row in result['results']))
            self.assertFalse(result['production_incremental_contract_verified']); self.assertEqual(result['price_cache_writes'], 0)
            after = {str(path.relative_to(args.cache_root)): path.read_bytes() for path in args.cache_root.rglob('*') if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(len(list(args.report.parent.glob('*-response.json'))), 2)

    def test_first_denial_or_server_error_stops_before_second_stock_without_retry(self):
        for status in (401, 403, 429, 500, 501, 503):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root, args, catalog, _ = self.fixture(directory)
                calls = []
                def fetch(*args):
                    calls.append(args)
                    return status, b'error'
                result = self.execute(root, args, catalog, fetch)
                self.assertEqual(result['status'], 'stopped'); self.assertEqual(len(calls), 1)
                self.assertEqual(result['network_requests'], 1); self.assertTrue(result['cache_hashes_unchanged'])

    def test_gate_or_unfinished_batch_never_requests(self):
        for reason in ('weekend', 'market_closed', 'calendar_unverified', 'before_post_close', 'market_snapshot_pending'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                root, args, catalog, _ = self.fixture(directory)
                with patch.object(probe, 'LOG_ROOT', root / 'logs'), patch.object(probe.p, 'read_catalog', return_value=(TARGET, catalog)), patch.object(probe.p, 'scheduled_gate', return_value=reason), patch.object(probe, 'fetch_once') as fetch:
                    result = probe.probe(args, now=NOW)
                self.assertEqual(result['reason'], reason); fetch.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            root, args, catalog, _ = self.fixture(directory)
            probe.p.atomic_json(args.cache_root / 'progress.json', {'provider': 'eastmoney', 'status': 'running', 'target_end': TARGET})
            with patch.object(probe, 'fetch_once') as fetch:
                result = self.execute(root, args, catalog, fetch)
            self.assertEqual(result['reason'], 'full_eastmoney_batch_not_finished_for_target'); fetch.assert_not_called()

    def test_every_overlap_field_and_total_scope_are_examined_without_silent_merge(self):
        for change in ('volume', 'missing', 'extra', 'identity', 'outside', 'total', 'oversize'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root, args, catalog, records = self.fixture(directory)
                calls = []
                def fetch(stock, start, end):
                    calls.append(stock['stock_code'])
                    data = self.response(stock, start, end, records)
                    if change == 'volume': data['data']['klines'][0] = data['data']['klines'][0].replace(',100,1000', ',101,1000')
                    elif change == 'missing': data['data']['klines'].pop(0); data['data']['dktotal'] -= 1
                    elif change == 'extra': data['data']['klines'].insert(-1, '2026-11-08,10,11,12,9,100,1000'); data['data']['dktotal'] += 1
                    elif change == 'identity': data['data']['code'] = '600000'
                    elif change == 'outside': data['data']['klines'][0] = data['data']['klines'][0].replace(DAYS[-5], DAYS[0])
                    elif change == 'total': data['data']['dktotal'] = 999
                    return 200, b'x' * (probe.source.MAX_BYTES + 1) if change == 'oversize' else json.dumps(data).encode()
                result = self.execute(root, args, catalog, fetch)
                self.assertEqual(result['status'], 'stopped'); self.assertEqual(len(calls), 1)
                self.assertTrue(result['cache_hashes_unchanged']); self.assertFalse(result['incremental_merge_performed'])

    def test_exception_text_is_not_logged_and_report_paths_cannot_target_caches_or_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root, args, catalog, _ = self.fixture(directory)
            def fetch(*_): raise RuntimeError('sensitive-query-and-header-fixture')
            result = self.execute(root, args, catalog, fetch)
            self.assertEqual(result['network_requests'], 1)
            self.assertNotIn('sensitive-query-and-header-fixture', args.report.read_text(encoding='utf-8'))
            with self.assertRaisesRegex(ValueError, 'already_exists'):
                self.execute(root, args, catalog, fetch)
            args.report = args.cache_root / 'prices/eastmoney/v1/600519.json'
            with self.assertRaisesRegex(ValueError, 'desktop_log_directory'):
                self.execute(root, args, catalog, fetch)


if __name__ == '__main__':
    unittest.main()
