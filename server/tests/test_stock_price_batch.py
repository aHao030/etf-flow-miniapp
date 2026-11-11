from argparse import Namespace
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import stock_price_batch as b
import probe_stock_price_window as probe


def universe():
    return {'schema':1,'kind':'current-listed-a-shares','generated_at':'2026-11-10T08:00:00Z',
            'as_of':'2026-11-10','target_end':'2026-11-10','complete':False,'stock_count':1,
            'exchanges':[{'market':'SH','status':'observed','count':1,'observed_at':'2026-11-10T07:00:00Z'}],
            'stocks':[{'stock_code':'600519','stock_name':'test','market':'SH',
                       'listed_date':'2021-01-04','lifecycle_status':'listed',
                       'observed_at':'2026-11-10T07:00:00Z','source':'SSE official stock directory',
                       'source_url':'https://www.sse.com.cn/assortment/stock/list/share/'}]}


class ListedPriceBatch(unittest.TestCase):
    def setup_case(self,tmp):
        root=Path(tmp);server=root/'server';(server/'data').mkdir(parents=True)
        c=sqlite3.connect(server/'data/index_history.db')
        c.execute('CREATE TABLE calendar(trade_date TEXT PRIMARY KEY)')
        c.executemany('INSERT INTO calendar VALUES(?)',[(d,) for d in ['2021-01-04','2024-12-31','2025-01-02','2026-11-10']]);c.commit();c.close()
        manifest=root/'universe.json';manifest.write_text(json.dumps(universe()),encoding='utf-8')
        args=Namespace(universe_file=manifest,cache_root=root/'cache',report=root/'report.json',checkpoint=None,
                       end=None,start='1990-01-01',max_jobs=100,workers=1,interval=.4,dry_run=False)
        return server,args

    def test_verified_current_membership_and_ipo_bounds(self):
        data=universe();self.assertEqual(b.plan_windows(data['stocks'],'1990-01-01','2026-11-10'),[('600519',2020),('600519',2025)])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'universe.json'
            data['stocks'][0]['lifecycle_status']='delisted';path.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaises(ValueError):b.read_universe(path)

    def test_resume_never_refetches_successful_windows_and_preserves_ohlcv(self):
        with tempfile.TemporaryDirectory() as tmp:
            server,args=self.setup_case(tmp)
            def fetch(code,year,end):
                days=['2021-01-04','2024-12-31'] if year==2020 else ['2025-01-02','2026-11-10']
                return {'series':[[d,10.] for d in days],'candles':[[d,10.,10.,10.,10.,123.] for d in days]}
            with patch.object(b.p,'SERVER',server),patch.object(b.p,'read_catalog',return_value=('2026-11-10',{})),patch.object(b.p,'fetch_daily_window',side_effect=fetch) as source:
                b.run_batch(args);self.assertEqual(source.call_count,2)
                b.run_batch(args);self.assertEqual(source.call_count,2)
            coverage=json.loads((args.cache_root/'coverage.json').read_text(encoding='utf-8'))
            self.assertEqual(coverage['stocks'][0]['rows'],4)
            self.assertEqual(coverage['stocks'][0]['status'],'ready')
            raw=json.loads((args.cache_root/'prices/ohlcv/v1/600519/2020.json').read_text(encoding='utf-8'))
            self.assertEqual(raw['volume_unit'],'provider_raw_unverified')

    def test_source_block_stops_batch_and_survives_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            server,args=self.setup_case(tmp)
            with patch.object(b.p,'SERVER',server),patch.object(b.p,'read_catalog',return_value=('2026-11-10',{})),patch.object(b.p,'fetch_daily_window',side_effect=RuntimeError('price source returned HTTP 429')) as source:
                b.run_batch(args);self.assertEqual(source.call_count,1)
                self.assertEqual(json.loads(args.report.read_text())['status'],'source_blocked')
                b.run_batch(args);self.assertEqual(source.call_count,1)
                self.assertEqual(json.loads(args.report.read_text())['status'],'source_backoff')

    def test_501_immediately_stops_and_persists_fifteen_minute_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            server,args=self.setup_case(tmp)
            with patch.object(b.p,'SERVER',server),patch.object(b.p,'read_catalog',return_value=('2026-11-10',{})),patch.object(b.p,'fetch_daily_window',side_effect=RuntimeError('price source returned HTTP 501')) as source:
                b.run_batch(args);self.assertEqual(source.call_count,1)
                self.assertEqual(json.loads(args.report.read_text())['status'],'source_service_error')
                b.run_batch(args);self.assertEqual(source.call_count,1)
            self.assertEqual(b.source_cooldown_seconds('price source returned HTTP 501'),900)
            self.assertEqual(b.source_cooldown_seconds('price source returned HTTP 403'),7200)

    def test_single_probe_refuses_success_and_requests_failed_501_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            server,args=self.setup_case(tmp);args.code='600519';args.year=2020
            b.p.atomic_json(args.cache_root/'prices/attempts/v1/600519/2020.json',
                           {'attempted_at':'2026-11-09T08:00:00Z','target_end':'2024-12-31','reason':'price source returned HTTP 501'})
            with patch.object(b.p,'read_catalog',return_value=('2026-11-10',{})),patch.object(b.p,'fetch_daily_window',return_value={'series':[['2021-01-04',10]],'candles':[['2021-01-04',10,10,10,10,1]]}) as source:
                result=probe.probe_once(args);self.assertEqual(result['network_requests'],1)
                again=probe.probe_once(args);self.assertEqual(again['status'],'already_cached')
                self.assertEqual(source.call_count,1)

    def test_plan_makes_no_requests_or_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            server,args=self.setup_case(tmp);args.dry_run=True
            with patch.object(b.p,'SERVER',server),patch.object(b.p,'read_catalog',return_value=('2026-11-10',{})),patch.object(b.p,'fetch_daily_window') as source:
                b.run_batch(args);source.assert_not_called()
            self.assertFalse(args.cache_root.exists())

    def test_old_window_is_stable_and_missing_days_are_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=Path(tmp);r={'schema':1,'stock_code':'600519','window_start':'2020-01-01','window_end':'2024-12-31',
                'checked_through':'2024-12-31','fetched_at':'2024-12-31T08:00:00Z','source':b.p.ENDPOINT,
                'adjustment':'none','price_unit':'元/股','series':[['2020-12-31',9],['2021-01-04',10]]}
            b.p.atomic_json(cache/'prices/v1/600519/2020.json',r)
            observed=b.inspect_window(cache,'600519',2020,'2026-11-10','2021-01-04',{'2020-12-31','2021-01-04','2024-12-31'})
            self.assertEqual(observed['rows'],1)
            report=b.make_coverage(universe(),'2024-12-31','1990-01-01',[('600519',2020)],{('600519',2020):observed},['2021-01-04','2024-12-31'])
            self.assertEqual(report['stocks'][0]['status'],'partial')
            self.assertEqual(report['stocks'][0]['missing_open_days'],1)

    def test_checkpoint_rejects_different_universe_and_lock_rejects_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=b.open_checkpoint(root/'checkpoint.sqlite','a','2026-11-10');db.close()
            with self.assertRaises(ValueError):b.open_checkpoint(root/'checkpoint.sqlite','b','2026-11-10')
            with b.batch_lock(root):
                with self.assertRaises(RuntimeError):
                    with b.batch_lock(root):pass

    def test_pending_current_window_retries_after_two_hours_retaining_old_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=Path(tmp);r={'schema':1,'stock_code':'600519','window_start':'2025-01-01','window_end':'2029-12-31',
                'checked_through':'2026-11-10','fetched_at':'2026-11-10T07:30:00Z','source':b.p.ENDPOINT,
                'adjustment':'none','price_unit':'元/股','series':[['2026-11-09',10]]}
            b.p.atomic_json(cache/'prices/v1/600519/2025.json',r)
            early=b.inspect_window(cache,'600519',2025,'2026-11-10','2021-01-04',{'2026-11-09','2026-11-10'},datetime(2026,11,10,8,tzinfo=timezone.utc))
            late=b.inspect_window(cache,'600519',2025,'2026-11-10','2021-01-04',{'2026-11-09','2026-11-10'},datetime(2026,11,10,10,tzinfo=timezone.utc))
            self.assertFalse(early['retry_due']);self.assertTrue(late['retry_due'])
            self.assertEqual(late['rows'],1);self.assertEqual(late['available_end'],'2026-11-09')

    def test_universe_rejects_counts_future_time_and_unofficial_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'universe.json'
            for change in ('stock_count','exchange_count','observed_at','source','source_url','generated_at'):
                data=universe()
                if change=='stock_count':data['stock_count']=2
                elif change=='exchange_count':data['exchanges'][0]['count']=2
                elif change=='observed_at':data['stocks'][0]['observed_at']='2026-11-09T07:00:00Z'
                elif change=='source':data['stocks'][0]['source']=''
                elif change=='source_url':data['stocks'][0]['source_url']='https://example.com/directory'
                else:data['generated_at']='2099-11-10T08:00:00Z'
                path.write_text(json.dumps(data),encoding='utf-8')
                with self.subTest(change=change),self.assertRaises(ValueError):b.read_universe(path)


if __name__=='__main__':unittest.main()
