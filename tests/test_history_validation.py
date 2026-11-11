import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from history import nav_source, nav_backfill, storage
import history_queries

class Response:
    def __init__(self, body):
        self.content = body.encode('utf-8') if isinstance(body, str) else json.dumps(body).encode()
    def raise_for_status(self): pass
    def json(self): return json.loads(self.content)

class Session:
    def __init__(self, payload): self.payload = payload
    def get(self, *args, **kwargs): return Response(self.payload)

class HistoryValidation(unittest.TestCase):
    def test_money_market_share_changes_are_not_reported_as_subscription(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.execute("INSERT INTO funds(fund_code,fund_name,market,category) VALUES('511880','money','SH','货币市场基金')")
                for day, shares in [('2021-01-04',100),('2021-01-05',101)]:
                    db.execute('INSERT INTO calendar VALUES (?,?)',(day,'test'))
                    db.execute('INSERT INTO shares VALUES (?,?,?,?)',('511880',day,shares,'test'))
                    db.execute('INSERT INTO nav VALUES (?,?,?,?,?)',('511880',day,100,None,'test'))
                    db.execute('INSERT INTO nav_verified VALUES (?,?,?)',('511880',day,'test'))
                storage.record(db,'nav-v2','511880:2020-12-31:2021-01-05','success',2)
            rows=history_queries.history_rows(db,'511880','2021-01-05')
            self.assertEqual(len(rows),2)
            self.assertTrue(all(r[4] is None and r[5]=='money_market' for r in rows))
            self.assertEqual(rows[-1][1:3],[101,100])
            db.close()

    @staticmethod
    def source(points, acc=None):
        if acc is None: acc = []
        points = [dict({'unitMoney':''}, **point) for point in points]
        return 'var fS_code="510300";var Data_netWorthTrend = '+json.dumps(points)+';var Data_ACWorthTrend = '+json.dumps(acc)+';'

    def test_shanghai_date_and_null_cumulative(self):
        # UTC 16:00 is next calendar day in Shanghai.
        ms = int(datetime(2021,1,3,16,tzinfo=timezone.utc).timestamp()*1000)
        text=self.source([{'x':ms,'y':1.23}], [[ms,None]])
        rows=nav_source.fetch_all('510300','2021-01-04','2021-01-04', Session(text))
        self.assertEqual(rows[0][1:4], ('2021-01-04',1.23,None))

    def test_missing_money_and_script_injection(self):
        with self.assertRaises(nav_source.UnsupportedNav):
            nav_source.fetch_all('511990','2021-01-01','2021-12-31',Session('var fS_code="511990";var Data_millionCopiesIncome = [];'))
        with self.assertRaises(nav_source.NavError):
            nav_source.fetch_all('510300','2021-01-01','2021-12-31',Session('var fS_code="510300";var Data_netWorthTrend = alert(1);'))

    def test_duplicate_and_empty_series_are_not_success(self):
        ms=1609689600000
        for text in (self.source([]),self.source([{'x':ms,'y':1},{'x':ms,'y':2}])):
            with self.assertRaises(nav_source.NavError):
                nav_source.fetch_all('510300','2021-01-01','2021-12-31',Session(text))

    def test_wrong_source_fund_is_rejected(self):
        text = self.source([{'x':1609689600000,'y':1}]).replace('510300','159915')
        with self.assertRaises(nav_source.NavError):
            nav_source.fetch_all('510300','2021-01-01','2021-12-31',Session(text))

    def test_events_are_extracted_without_executing_js(self):
        point={'x':1609689600000,'y':1.2,'unitMoney':'每份分红0.01元'}
        rows,events=nav_source.fetch_bundle('510300','2021-01-01','2021-12-31',Session(self.source([point])))
        self.assertEqual(len(rows),1)
        self.assertEqual(events[0][2], '每份分红0.01元')

    def test_target_day_without_nav_is_pending_and_retried(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.execute("INSERT INTO funds(fund_code,fund_name,market) VALUES('510300','a','SH')")
                db.execute("INSERT INTO shares VALUES ('510300','2021-01-05',100,'test')")
            db.close()
            row=('510300','2021-01-04',1.2,None,'test')
            event=('510300','2021-01-04','分红','eastmoney:pingzhongdata')
            with patch.object(nav_source,'fetch_bundle',return_value=([row],[event])) as fetch:
                first=nav_backfill.run(date(2021,1,1),date(2021,1,5),delay=0)
                again=nav_backfill.run(date(2021,1,1),date(2021,1,5),delay=0)
            self.assertEqual(first['pending'],1)
            self.assertEqual(again['pending'],1)
            self.assertEqual(fetch.call_count,2)
            db=storage.connect()
            self.assertEqual(db.execute('select count(*) from fund_events').fetchone()[0],1)
            db.close()

    def test_late_shares_reopen_earlier_success(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.execute("INSERT INTO funds(fund_code,fund_name,market) VALUES('510300','a','SH')")
                storage.record(db,'nav-v2','510300:2020-12-31:2021-01-05','success',1)
                db.execute("INSERT INTO shares VALUES ('510300','2021-01-05',100,'test')")
            db.close()
            with patch.object(nav_source,'fetch_bundle',return_value=([('510300','2021-01-04',1.2,None,'test')],[])) as fetch:
                result=nav_backfill.run(date(2021,1,1),date(2021,1,5),delay=0)
            self.assertEqual(result['pending'],1)
            self.assertEqual(fetch.call_count,1)

    def test_revalidation_clears_missing_days_without_deleting_old_nav(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.execute("INSERT INTO funds(fund_code,fund_name,market) VALUES('510300','a','SH')")
                db.execute("INSERT INTO nav VALUES ('510300','2021-01-05',1.2,NULL,'old')")
                db.execute("INSERT INTO nav_verified VALUES ('510300','2021-01-05','old')")
            db.close()
            with patch.object(nav_source,'fetch_bundle',return_value=([('510300','2021-01-04',1.1,None,'test')],[])):
                nav_backfill.run(date(2021,1,1),date(2021,1,5),delay=0)
            db=storage.connect()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM nav WHERE trade_date='2021-01-05'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM nav_verified WHERE trade_date='2021-01-05'").fetchone()[0],0)
            db.close()

    def test_truncated_paginated_response_fails(self):
        doc={'ErrCode':0,'TotalCount':2,'PageSize':2,'PageIndex':1,'Data':{'LSJZList':[{'FSRQ':'2021-01-04','DWJZ':'1'}]}}
        with self.assertRaises(nav_source.NavError):
            nav_source.fetch('510300','2021-01-01','2021-12-31',Session(doc),delay=0)

    def test_resume_success_and_failure(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.execute("INSERT INTO funds(fund_code,fund_name,market) VALUES('510300','x','SH')")
            db.close()

            row=('510300','2021-01-04',1.2,None,'test')
            with patch.object(nav_source,'fetch_bundle',return_value=([row],[])) as fetch:
                result=nav_backfill.run(date(2021,1,1),date(2021,1,5),delay=0)
                again=nav_backfill.run(date(2021,1,1),date(2021,1,5),delay=0)
                self.assertEqual(result['success'],1)
                self.assertEqual(again['skipped'],1)
                self.assertEqual(fetch.call_count,1)
            with patch.object(nav_source,'fetch_bundle',side_effect=nav_source.NavError('unavailable')):
                failed=nav_backfill.run(date(2021,1,1),date(2021,1,6),delay=0)
                self.assertEqual(failed['failed'],1)
            db=storage.connect()
            self.assertEqual(db.execute('select count(*) from nav').fetchone()[0],1)
            self.assertEqual(db.execute("select status from jobs where job_key like '%2021-01-06'").fetchone()[0],'failed')
            db.close()

    def test_missing_current_nav_suppresses_estimate(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.executemany('INSERT INTO calendar VALUES (?,?)',[('2021-01-04','test'),('2021-01-05','test')])
                db.executemany('INSERT INTO shares VALUES (?,?,?,?)', [('510300','2021-01-04',100,'test'),('510300','2021-01-05',105,'test')])
                db.execute('INSERT INTO nav VALUES (?,?,?,?,?)', ('510300','2021-01-04',1.0,None,'test'))
                db.execute('INSERT INTO nav_verified VALUES (?,?,?)', ('510300','2021-01-04','test'))
                storage.record(db,'nav-v2','510300:2021-01-04:2021-01-05','success',1)
            rows=history_queries.history_rows(db,'510300','2021-01-05')
            self.assertEqual(rows[-1][4:], [None,'missing_current_nav'])
            db.close()

    def test_limit_skips_completed_jobs_before_counting(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.executemany('INSERT INTO funds(fund_code,fund_name,market) VALUES (?,?,?)', [('510300','a','SH'),('510301','b','SH')])
                storage.record(db,'nav-v2','510300:2020-12-31:2021-01-05','success',1)
            db.close()
            with patch.object(nav_source,'fetch_bundle',return_value=([('510301','2021-01-04',1.2,None,'test')],[])) as fetch:
                result=nav_backfill.run(date(2021,1,1),date(2021,1,5),limit=1,delay=0)
            self.assertEqual(result['skipped'],1)
            self.assertEqual(result['success'],1)
            self.assertEqual(fetch.call_args.args[0],'510301')

    def test_market_filter_runs_only_matching_funds(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.executemany('INSERT INTO funds(fund_code,fund_name,market) VALUES (?,?,?)', [('510300','a','SH'),('159915','b','SZ')])
            db.close()
            with patch.object(nav_source,'fetch_bundle',return_value=([('510300','2021-01-04',1.2,None,'test')],[])) as fetch:
                nav_backfill.run(date(2021,1,1),date(2021,1,5),delay=0,market='SH')
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(fetch.call_args.args[0],'510300')

    def test_thresholds_and_missing_previous_day(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.executemany('INSERT INTO calendar VALUES (?,?)',[(f'2021-01-0{x}','test') for x in (4,5,6,7)])
                db.executemany('INSERT INTO shares VALUES (?,?,?,?)', [('510300','2021-01-04',100,'test'),('510300','2021-01-05',140,'test'),('510300','2021-01-07',145,'test')])
                db.executemany('INSERT INTO nav VALUES (?,?,?,?,?)',[('510300','2021-01-04',1,None,'test'),('510300','2021-01-05',1,None,'test'),('510300','2021-01-07',1,None,'test')])
                db.executemany('INSERT INTO nav_verified VALUES (?,?,?)',[('510300',f'2021-01-0{x}','test') for x in (4,5,7)])
                storage.record(db,'nav-v2','510300:2021-01-04:2021-01-07','success',3)
            rows=history_queries.history_rows(db,'510300','2021-01-07')
            self.assertEqual(rows[1][4:], [None,'possible_split'])
            self.assertEqual(rows[2][4:], [None,'missing_previous_shares'])
            db.close()

    def test_event_and_unverified_coverage_suppress_flow(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH':str(Path(d)/'x.db')}):
            db=storage.connect()
            with db:
                db.executemany('INSERT INTO calendar VALUES (?,?)',[(f'2021-01-0{x}','test') for x in (4,5,6)])
                db.executemany('INSERT INTO shares VALUES (?,?,?,?)',[('510300',f'2021-01-0{x}',100+x,'test') for x in (4,5,6)])
                db.executemany('INSERT INTO nav VALUES (?,?,?,?,?)',[('510300',f'2021-01-0{x}',1,None,'test') for x in (4,5,6)])
                db.executemany('INSERT INTO nav_verified VALUES (?,?,?)',[('510300',f'2021-01-0{x}','test') for x in (4,5,6)])
            self.assertEqual(history_queries.history_rows(db,'510300','2021-01-06')[-1][4:],[None,'events_unverified'])
            with db:
                storage.record(db,'nav-v2','510300:2021-01-04:2021-01-06','success',3)
                db.execute("INSERT INTO fund_events VALUES ('510300','2021-01-05','分红','test')")
            rows=history_queries.history_rows(db,'510300','2021-01-06')
            self.assertEqual(rows[1][4:],[None,'fund_event'])
            self.assertEqual(rows[2][4:],[None,'fund_event'])
            with db:
                db.execute('DELETE FROM fund_events')
                db.execute("DELETE FROM nav_verified WHERE trade_date='2021-01-06'")
            self.assertEqual(history_queries.history_rows(db,'510300','2021-01-06')[-1][4:],[None,'events_unverified'])
            db.close()

if __name__ == '__main__': unittest.main()
