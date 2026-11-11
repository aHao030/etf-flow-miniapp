"""NAV retries when target-day shares and NAV have different publication times."""
import os,sys,tempfile,unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from history import storage,nav_source,nav_backfill,backfill

class RecentNavRetry(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.env=patch.dict(os.environ,{'ETF_HISTORY_DB_PATH':str(Path(self.temp.name)/'test.db')});self.env.start()
  self.db=storage.connect()
  with self.db:
   self.db.executemany('INSERT INTO calendar VALUES (?,?)',[('2026-10-27','test'),('2026-10-30','test')])
   self.db.executemany('INSERT INTO funds(fund_code,market,last_seen,current_seen,listed_date,delisted_date) VALUES(?,?,?,?,?,?)',[
    ('510001','SH','2026-10-30',1,None,None),('510002','SH','2026-10-30',1,'2026-11-02',None),
    ('510003','SH','2026-10-30',1,None,'2026-10-29'),('510004','SH','2026-10-30',1,None,None),
    ('510005','SH','2026-10-30',0,None,None)])
 def tearDown(self):
  self.db.close();self.env.stop();self.temp.cleanup()
 def test_current_missing_shares_and_nav_stays_pending_then_nav_arrives(self):
  old=('510001','2026-10-29',1.2,None,'test');today=('510001','2026-10-30',1.3,None,'test')
  with patch.object(nav_source,'fetch_bundle',side_effect=[([old],[]),([old,today],[])]) as fetch:
   first=nav_backfill.run(date(2026,10,28),date(2026,10,30),fund='510001',delay=0)
   second=nav_backfill.run(date(2026,10,28),date(2026,10,30),fund='510001',delay=0)
   third=nav_backfill.run(date(2026,10,28),date(2026,10,30),fund='510001',delay=0)
  self.assertEqual((first['pending'],second['success'],third['skipped']),(1,1,1));self.assertEqual(fetch.call_count,2)
  self.assertEqual(self.db.execute("SELECT count(*) FROM shares").fetchone()[0],0)
 def test_success_without_shares_is_reselected_in_both_paths(self):
  with self.db:storage.record(self.db,'nav-v2','510001:2026-10-27:2026-10-30','success',4)
  with patch.object(nav_backfill,'run',return_value={'success':0,'pending':1,'unsupported':0,'failed':0,'skipped':0}) as run:
   backfill.run_nav_bounded(self.db,'2026-10-28','2026-10-30',10)
  self.assertIn('510001',[x.kwargs['fund'] for x in run.call_args_list])
  with patch.object(nav_source,'fetch_bundle',return_value=([('510001','2026-10-29',1.0,None,'test')],[])) as fetch:
   result=nav_backfill.run(date(2026,10,28),date(2026,10,30),fund='510001',delay=0)
  self.assertEqual(result['pending'],1);self.assertEqual(fetch.call_count,1)
 def test_lifecycle_historical_and_unsupported_skip(self):
  with self.db:
   self.db.executemany('INSERT INTO shares VALUES(?,?,?,?)',[('510002','2026-10-30',1,'test'),('510003','2026-10-30',1,'test')])
   self.db.execute("INSERT INTO funds(fund_code,market,current_seen,first_seen) VALUES('510006','SH',1,'2026-11-02')")
  self.assertTrue(nav_backfill.expected_target(self.db,'510001','2026-10-30'))
  self.assertFalse(nav_backfill.expected_target(self.db,'510006','2026-10-30'))
  self.assertFalse(nav_backfill.expected_target(self.db,'510002','2026-10-30'))
  self.assertFalse(nav_backfill.expected_target(self.db,'510003','2026-10-30'))
  self.assertFalse(nav_backfill.expected_target(self.db,'510001','2026-09-30'))
  self.assertFalse(nav_backfill.expected_target(self.db,'510005','2026-10-30'))
  with self.db:
   storage.record(self.db,'nav-v2','510004:2026-10-27:2026-10-30','unsupported',0)
   storage.record(self.db,'nav-v2','510002:2026-10-27:2026-10-30','success',1)
   storage.record(self.db,'nav-v2','510001:2026-09-01:2026-09-30','success',1)
  with patch.object(nav_source,'fetch_bundle',side_effect=AssertionError('unexpected fetch')):
   self.assertEqual(nav_backfill.run(date(2026,10,28),date(2026,10,30),fund='510004',delay=0)['skipped'],1)
   self.assertEqual(nav_backfill.run(date(2026,10,28),date(2026,10,30),fund='510002',delay=0)['skipped'],1)
   self.assertEqual(nav_backfill.run(date(2026,9,2),date(2026,9,30),fund='510001',delay=0)['skipped'],1)
 def test_reconcile_current_jobs_only_no_observation_mutation(self):
  with self.db:
   for c in ('510001','510002','510003','510004','510005'):
    storage.record(self.db,'nav-v2',f'{c}:2026-10-27:2026-10-30','unsupported' if c=='510004' else 'success',7)
   storage.record(self.db,'nav-v2','510001:2026-09-01:2026-09-30','success',4)
   self.db.execute("INSERT INTO nav VALUES('510001','2026-10-29',1,NULL,'test')")
   self.db.execute("INSERT INTO nav_verified VALUES('510001','2026-10-29','test')")
  self.assertEqual(nav_backfill.mark_missing_current_pending(self.db,'2026-10-27','2026-10-30'),1)
  rows={r['job_key']:(r['status'],r['rows_count']) for r in self.db.execute('SELECT job_key,status,rows_count FROM jobs')}
  self.assertEqual(rows['510001:2026-10-27:2026-10-30'],('pending',7))
  self.assertEqual(rows['510001:2026-09-01:2026-09-30'],('success',4))
  for c in ('510002','510003','510005'):self.assertEqual(rows[f'{c}:2026-10-27:2026-10-30'][0],'success')
  self.assertEqual(rows['510004:2026-10-27:2026-10-30'][0],'unsupported')
  self.assertEqual(self.db.execute('select count(*) from nav_verified').fetchone()[0],1)
  with self.assertRaises(ValueError):nav_backfill.mark_missing_current_pending(self.db,'2026-09-01','2026-09-30')

if __name__=='__main__':unittest.main()
