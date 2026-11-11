import importlib.util
from contextlib import closing
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'scripts' / 'collect_stock_prices.py'
spec = importlib.util.spec_from_file_location('stock_price_collector', path)
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def record(rows=None, age=0):
    return {'schema': 1, 'stock_code': '600519', 'window_start': '2025-01-01',
            'window_end': '2029-12-31', 'checked_through': '2026-11-09',
            'fetched_at': (NOW-timedelta(hours=age)).isoformat(), 'source': p.ENDPOINT,
            'adjustment': 'none', 'price_unit': '元/股',
            'series': rows if rows is not None else [['2026-11-09', 1444.99]]}


class StockPriceCache(unittest.TestCase):
    def test_pending_retries_after_two_hours_but_ready_stays_cached(self):
        self.assertFalse(p.cache_fresh(record([['2026-11-06',1440]],3), '600519',2025,'2026-11-09',NOW))
        self.assertTrue(p.cache_fresh(record(age=3), '600519',2025,'2026-11-09',NOW))
        self.assertFalse(p.cache_fresh(record(age=-1), '600519',2025,'2026-11-09',NOW))

    def test_fetch_failure_preserves_verified_previous_bytes_and_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/'prices/v1/600519/2025.json'
            p.atomic_json(path, record(age=24*31)); before = path.read_bytes()
            with patch.object(p, 'fetch_window', side_effect=RuntimeError('price source returned HTTP 403')) as fetch:
                result = p.refresh_window(root,'600519',2025,'2026-11-09',NOW)
                self.assertEqual(result['status'],'failed'); self.assertTrue(result['retained_previous'])
                self.assertEqual(path.read_bytes(), before)
                retry = p.refresh_window(root,'600519',2025,'2026-11-09',NOW+timedelta(minutes=30))
                self.assertEqual(retry['status'],'backoff'); self.assertEqual(fetch.call_count,1)

    def test_empty_success_cannot_erase_prior_price_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/'prices/v1/600519/2025.json'
            p.atomic_json(path, record(age=24*31)); before = path.read_bytes()
            with patch.object(p,'fetch_window',return_value=[]):
                result = p.refresh_window(root,'600519',2025,'2026-11-09',NOW)
            self.assertEqual(result['reason'],'source_history_regression')
            self.assertEqual(path.read_bytes(), before)

    def test_older_manual_target_cannot_downgrade_newer_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/'prices/v1/600519/2025.json'
            p.atomic_json(path, record(age=24*31)); before = path.read_bytes()
            with patch.object(p,'fetch_window') as fetch:
                result = p.refresh_window(root,'600519',2025,'2026-10-30',NOW)
                self.assertEqual(result['status'],'cached'); fetch.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_success_writes_shared_window_record_without_synthetic_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(p,'fetch_window',return_value=[['2026-11-09',1444.99]]):
                result = p.refresh_window(root,'600519',2025,'2026-11-09',NOW)
            saved = json.loads((root/'prices/v1/600519/2025.json').read_text(encoding='utf-8'))
            self.assertEqual(result['status'],'success')
            self.assertTrue(p.valid_cache(saved,'600519',2025))
            self.assertEqual(saved['series'],[['2026-11-09',1444.99]])
            self.assertEqual(list(root.rglob('*.tmp')),[])

    def test_scheduled_refresh_checks_calendar_time_and_published_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'calendar.db'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE calendar(trade_date TEXT PRIMARY KEY)')
                db.executemany('INSERT INTO calendar VALUES(?)', [('2026-01-02',),('2026-11-09',),('2026-12-31',)])
                db.commit()
            self.assertIsNone(p.scheduled_gate('2026-11-09',NOW,path))
            self.assertEqual(p.scheduled_gate('2026-11-06',NOW,path),'market_snapshot_pending')
            self.assertEqual(p.scheduled_gate('2026-11-09',NOW.replace(hour=6),path),'before_post_close')
            self.assertEqual(p.scheduled_gate('2026-11-08',NOW-timedelta(days=1),path),'weekend')
            self.assertEqual(p.scheduled_gate('2026-11-10',NOW+timedelta(days=1),path),'market_closed')


if __name__ == '__main__': unittest.main()
