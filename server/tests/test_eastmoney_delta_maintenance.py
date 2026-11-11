"""Exercise incremental updates against isolated files and simulated provider replies."""
from collections import Counter
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

SERVER = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SERVER), str(SERVER / 'scripts')]
import eastmoney_delta_maintenance as maintenance
import collect_stock_prices as collector
from history import eastmoney_stock_prices as prices
from collect_stock_prices import atomic_json

PREVIOUS = '2026-11-10'
TARGET = '2026-11-11'
NOW = datetime(2026, 11, 11, 8, 30, tzinfo=timezone.utc)
ANCHORS = ('600519', '000001')
THIRD = '300750'
RECOVERY = '688827'
RECENT = ['2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-09', PREVIOUS]


def stock(code):
    listed = {'600519': '2001-08-27', '000001': '1991-04-03',
              THIRD: '2018-06-11', RECOVERY: '2026-11-09'}.get(code, '2002-01-04')
    market = 'SH' if code.startswith('6') else 'SZ'
    return {'stock_code': code, 'stock_name': 'sample ' + code, 'market': market, 'listed_date': listed,
            'lifecycle_status': 'listed', 'source': market + ' exchange official listing directory',
            'source_url': 'https://www.sse.com.cn/' if market == 'SH' else 'https://www.szse.cn/',
            'observed_at': '2026-11-10T07:00:00Z'}


def candle(day):
    return [day, 10, 11, 12, 9, 100, 1000]


def bundle(rows):
    return {'candles': rows, 'provider_total': len(rows), 'source_name': 'sample',
            'series': [[row[0], row[2]] for row in rows if row[5] > 0]}


class MarketFixture:
    def __init__(self, directory, *, recovery=False, extras=()):
        self.cache = Path(directory) / 'price-cache'
        codes = ANCHORS + (THIRD,) + extras + ((RECOVERY,) if recovery else ())
        self.stocks = {code: stock(code) for code in codes}
        self.catalog = {code: {'stock_code': code, 'stock_name': row['stock_name'], 'market': row['market'],
                               'stock_listed_date': row['listed_date']} for code, row in self.stocks.items()}
        self.raw = {}
        self.calls = []
        self.clock = 0.0
        self.no_full_request = Mock(side_effect=AssertionError('unexpected full-history request'))
        for code in ANCHORS + (THIRD,) + extras:
            days = [self.stocks[code]['listed_date'], *RECENT]
            if code == '000001':
                # Source history outside the present shared calendar must survive a refresh.
                days.insert(1, '1991-09-29')
            self.raw[code] = [candle(day) for day in days]
            record = prices.make_record(self.stocks[code], PREVIOUS, bundle(self.raw[code]), '2026-11-10T08:00:00Z')
            atomic_json(self.path(code), record)
        self.write_universe()
        self.sentinel = self.cache / 'prices/v1/600519/2025.json'
        self.sentinel.parent.mkdir(parents=True)
        self.sentinel.write_bytes(b'independent Tencent fixture')
        if recovery:
            atomic_json(self.cache / f'prices/eastmoney/attempts/v1/{RECOVERY}.json', {
                'target_end': PREVIOUS, 'attempted_at': '2026-11-10T09:44:22Z',
                'reason': 'source total mismatch or possible truncation'})

    def write_universe(self):
        counts = Counter(s['market'] for s in self.stocks.values())
        manifest = {'schema': 1, 'kind': 'current-listed-a-shares', 'generated_at': '2026-11-10T08:00:00Z',
                    'as_of': PREVIOUS, 'target_end': PREVIOUS, 'complete': False, 'stock_count': len(self.stocks),
                    'exchanges': [{'market': market, 'status': 'verified', 'count': count,
                                   'observed_at': '2026-11-10T07:00:00Z'} for market, count in counts.items()],
                    'stocks': list(self.stocks.values())}
        raw = json.dumps(manifest, separators=(',', ':')).encode()
        digest = hashlib.sha256(raw).hexdigest()[:12]
        path = self.cache.parent / f'price-universes/current-listed-20261110-080000-{digest}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def path(self, code):
        return self.cache / f'prices/eastmoney/v1/{code}.json'

    def read(self, code):
        return json.loads(self.path(code).read_bytes())

    def price_bytes(self):
        return {code: self.path(code).read_bytes() for code in self.stocks if self.path(code).exists()}

    def reply(self, request_stock, start, end, *, pending=False, change=None, total_adjustment=0):
        code = request_stock['stock_code']
        self.calls.append((code, start, end, self.clock))
        full = self.raw[code] + ([] if pending else [candle(end)])
        short = [row.copy() for row in full if start <= row[0] <= end]
        if change:
            change(short)
        payload = {'rc': 0, 'data': {'code': code, 'market': prices.market_id(request_stock['market']),
                   'dktotal': len(full) + total_adjustment, 'klines': [','.join(map(str, row)) for row in short]}}
        return 200, json.dumps(payload).encode()

    def sleep(self, seconds):
        self.clock += seconds

    def run(self, budget, *, fetcher=None, full_fetcher=None, now=NOW):
        return maintenance.maintain(self.cache, self.catalog, TARGET, budget, now=now,
                                    fetcher=fetcher or self.reply, full_fetcher=full_fetcher or self.no_full_request,
                                    interval=.6, sleeper=self.sleep, monotonic=lambda: self.clock)


class DeltaMaintenanceTests(unittest.TestCase):
    def test_two_anchor_budget_commits_only_verified_rows_and_reuses_gate(self):
        with TemporaryDirectory() as directory:
            fixture = MarketFixture(directory)
            eligible, first = fixture.run(2)
            self.assertEqual(set(eligible), set(fixture.raw))
            self.assertEqual(first['network_requests'], 2)
            self.assertEqual(first['updated'], 2)
            self.assertFalse(first['current_listings_reverified'])
            self.assertEqual(first['listing_universe_as_of'], [PREVIOUS])
            self.assertEqual({call[0] for call in fixture.calls}, set(ANCHORS))
            self.assertGreaterEqual(fixture.calls[1][3] - fixture.calls[0][3], .6)
            for code, start, end, _ in fixture.calls:
                self.assertEqual((start, end), (fixture.raw[code][-5][0], TARGET))
                self.assertEqual(fixture.read(code)['candles'], fixture.raw[code] + [candle(TARGET)])
                self.assertTrue(prices.valid_record(fixture.read(code), fixture.stocks[code]))
            self.assertEqual(fixture.read(THIRD)['checked_through'], PREVIOUS)
            self.assertIn('1991-09-29', [row[0] for row in fixture.read('000001')['series']])
            _, resume = fixture.run(1, now=NOW + timedelta(minutes=1))
            self.assertEqual((resume['network_requests'], resume['updated']), (1, 1))
            self.assertEqual([call[0] for call in fixture.calls[2:]], [THIRD])
            _, same_target = fixture.run(3, now=NOW + timedelta(minutes=2))
            self.assertEqual(same_target['network_requests'], 0)
            self.assertEqual(fixture.sentinel.read_bytes(), b'independent Tencent fixture')
            fixture.no_full_request.assert_not_called()

    def test_scheduled_entry_skips_weekend_holiday_and_preclose_before_price_maintenance(self):
        with TemporaryDirectory() as directory:
            fixture = MarketFixture(directory)
            calendar = Path(directory) / 'calendar.db'
            with closing(sqlite3.connect(calendar)) as db:
                db.execute('CREATE TABLE calendar(trade_date TEXT PRIMARY KEY)')
                db.executemany('INSERT INTO calendar VALUES(?)', [(PREVIOUS,), ('2026-11-12',), ('2026-11-13',)])
                db.commit()
            scenarios = [(NOW, 'market_closed'), (NOW + timedelta(days=3), 'weekend'),
                         (NOW.replace(day=12, hour=5), 'before_post_close')]
            old = fixture.price_bytes()
            gate = collector.scheduled_gate
            for index, (at, reason) in enumerate(scenarios):
                with self.subTest(reason=reason):
                    report = Path(directory) / f'run-{index}.json'
                    target = at.astimezone(collector.BEIJING).date().isoformat()
                    argv = ['collect_stock_prices.py', '--refresh-cached', '--cache-root', str(fixture.cache), '--report', str(report)]
                    with patch.object(sys, 'argv', argv), patch.object(collector, 'read_catalog', return_value=(target, fixture.catalog)), \
                         patch.object(collector, 'scheduled_gate', side_effect=lambda day: gate(day, at, calendar)), \
                         patch.object(maintenance, 'maintain') as eastmoney, patch.object(collector, 'refresh_window') as tencent, \
                         redirect_stdout(io.StringIO()):
                        collector.main()
                    outcome = json.loads(report.read_bytes())
                    self.assertEqual((outcome['reason'], outcome['attempted']), (reason, 0))
                    eastmoney.assert_not_called()
                    tencent.assert_not_called()
            self.assertEqual(fixture.price_bytes(), old)

    def test_every_overlap_field_or_inconsistent_total_rejects_both_anchor_writes(self):
        for field in ['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'total']:
            with self.subTest(field=field), TemporaryDirectory() as directory:
                fixture = MarketFixture(directory)
                original = fixture.price_bytes()

                def fetch(request_stock, start, end):
                    if not fixture.calls:
                        return fixture.reply(request_stock, start, end)

                    def alter(rows):
                        if field == 'date':
                            rows.pop(0)
                        elif field != 'total':
                            index, value = {'open': (1, 10.5), 'close': (2, 11.5), 'high': (3, 12.5),
                                            'low': (4, 8.5), 'volume': (5, 101), 'amount': (6, 1001)}[field]
                            rows[0][index] = value

                    return fixture.reply(request_stock, start, end, change=alter, total_adjustment=int(field == 'total'))

                _, report = fixture.run(3, fetcher=fetch)
                self.assertEqual((report['network_requests'], report['updated']), (2, 0))
                self.assertEqual(fixture.price_bytes(), original)
                fixture.no_full_request.assert_not_called()

    def test_pending_retries_only_that_stock_after_two_hours_and_keeps_real_last_date(self):
        with TemporaryDirectory() as directory:
            fixture = MarketFixture(directory)

            def pending(stock, start, end):
                return fixture.reply(stock, start, end, pending=stock['stock_code'] == THIRD)

            _, first = fixture.run(3, fetcher=pending)
            self.assertEqual((first['network_requests'], first['pending']), (3, 1))
            record = fixture.read(THIRD)
            self.assertEqual((record['checked_through'], record['series'][-1][0]), (TARGET, PREVIOUS))
            _, early = fixture.run(3, now=NOW + timedelta(minutes=119))
            self.assertEqual(early['network_requests'], 0)
            _, late = fixture.run(3, now=NOW + timedelta(minutes=121))
            self.assertEqual((late['network_requests'], late['updated'], late['pending']), (1, 1, 0))
            self.assertEqual({c[0] for c in fixture.calls[:2]}, set(ANCHORS))
            self.assertEqual([c[0] for c in fixture.calls[2:]], [THIRD, THIRD])
            self.assertEqual(fixture.read(THIRD)['series'][-1], [TARGET, 11])

    def test_new_trading_day_requires_a_source_candle_after_both_previous_query_cutoffs(self):
        for delayed_anchor in ANCHORS:
            with self.subTest(delayed_anchor=delayed_anchor), TemporaryDirectory() as directory:
                fixture = MarketFixture(directory)
                # This stock's latest prior candle is older than its query cutoff. A late candle for
                # that same cutoff proves the old day was revised, but provides no cross-day evidence.
                before = fixture.read(delayed_anchor)
                before['candles'].pop()
                before['series'].pop()
                before['provider_total'] -= 1
                atomic_json(fixture.path(delayed_anchor), before)
                old = fixture.price_bytes()

                def fetch(stock, start, end):
                    return fixture.reply(stock, start, end, pending=stock['stock_code'] == delayed_anchor)

                _, result = fixture.run(3, fetcher=fetch)
                self.assertEqual((result['status'], result['updated']), ('waiting_verification', 0))
                self.assertNotIn(THIRD, [c[0] for c in fixture.calls])
                self.assertEqual(fixture.price_bytes(), old)
                _, early = fixture.run(3, now=NOW + timedelta(minutes=119))
                self.assertEqual(early['network_requests'], 0)
                _, later = fixture.run(3, now=NOW + timedelta(minutes=121))
                self.assertEqual((later['network_requests'], later['updated']), (3, 3))

    def test_five_consecutive_data_errors_halt_the_remaining_stocks_for_fifteen_minutes(self):
        with TemporaryDirectory() as directory:
            extra = tuple(f'6005{i:02d}' for i in range(10, 16))
            fixture = MarketFixture(directory, extras=extra)
            original = fixture.price_bytes()

            def inconsistent(stock, start, end):
                return fixture.reply(stock, start, end, total_adjustment=int(stock['stock_code'] not in ANCHORS))

            _, result = fixture.run(20, fetcher=inconsistent)
            self.assertEqual((result['network_requests'], result['updated']), (7, 2))
            self.assertTrue(result['halt_remaining'])
            self.assertEqual([code for code in fixture.stocks if code not in ANCHORS and fixture.path(code).read_bytes() != original[code]], [])
            _, early = fixture.run(20, now=NOW + timedelta(minutes=14))
            self.assertEqual(early['network_requests'], 0)
            _, after = fixture.run(20, now=NOW + timedelta(minutes=16))
            self.assertGreater(after['updated'], 0)

    def test_price_write_interruption_after_both_anchors_validate_can_resume_on_the_same_target(self):
        with TemporaryDirectory() as directory:
            fixture = MarketFixture(directory)
            writes = []

            def flaky_write(path, value):
                if path in [fixture.path(c) for c in ANCHORS]:
                    writes.append(path.stem)
                    if len(writes) == 2:
                        raise OSError('simulated local write interruption')
                atomic_json(path, value)

            with patch.object(collector, 'atomic_json', side_effect=flaky_write):
                _, interrupted = fixture.run(3)
            self.assertEqual(interrupted['network_requests'], 2)
            self.assertEqual(len(writes), 2)
            _, resumed = fixture.run(3, now=NOW + timedelta(minutes=1))
            self.assertLessEqual(resumed['network_requests'], 2)
            for code in fixture.raw:
                self.assertEqual(fixture.read(code)['series'][-1], [TARGET, 11])

    def test_access_or_server_failure_persists_global_cooldown_and_writes_no_prices(self):
        for status, minutes in [(403, 120), (503, 15)]:
            with self.subTest(status=status), TemporaryDirectory() as directory:
                fixture = MarketFixture(directory)
                old = fixture.price_bytes()
                refused = Mock(return_value=(status, b'error'))
                _, stopped = fixture.run(3, fetcher=refused)
                self.assertEqual(stopped['network_requests'], 1)
                refused.assert_called_once()
                _, early = fixture.run(3, now=NOW + timedelta(minutes=minutes - 1))
                self.assertEqual(early['network_requests'], 0)
                self.assertEqual(fixture.price_bytes(), old)
                self.assertEqual(fixture.calls, [])
                _, resumed = fixture.run(2, now=NOW + timedelta(minutes=minutes + 1))
                self.assertEqual((resumed['network_requests'], resumed['updated']), (2, 2))

    def test_prior_full_history_failure_requires_verified_directory_and_shared_budget(self):
        for correct_identity in (True, False):
            with self.subTest(verified=correct_identity), TemporaryDirectory() as directory:
                fixture = MarketFixture(directory, recovery=True)
                if not correct_identity:
                    fixture.catalog[RECOVERY]['stock_listed_date'] = '2026-11-06'
                full = Mock(return_value=bundle([candle(d) for d in ('2026-11-09', PREVIOUS, TARGET)]))
                _, gate_only = fixture.run(2, full_fetcher=full)
                self.assertEqual(gate_only['network_requests'], 2)
                self.assertFalse(fixture.path(RECOVERY).exists())
                full.assert_not_called()
                _, remaining = fixture.run(4, full_fetcher=full, now=NOW + timedelta(minutes=1))
                self.assertLessEqual(remaining['network_requests'], 4)
                if correct_identity:
                    full.assert_called_once()
                    requested_stock, end = full.call_args.args
                    self.assertEqual((requested_stock['stock_code'], requested_stock['listed_date'], end),
                                     (RECOVERY, '2026-11-09', TARGET))
                    self.assertTrue(prices.valid_record(fixture.read(RECOVERY), fixture.stocks[RECOVERY]))
                else:
                    full.assert_not_called()
                    self.assertFalse(fixture.path(RECOVERY).exists())


if __name__ == '__main__':
    unittest.main()
