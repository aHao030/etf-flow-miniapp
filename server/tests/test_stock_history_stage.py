import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.stage_stock_history import stage
from scripts.expand_stock_history_stage import expand
from scripts.export_web_snapshot import series_totals_agree
from scripts.build_stock_history_snapshot import cleanup_successful_build


class StockHistoryStageTests(unittest.TestCase):
    def setUp(self):
        self.app = sqlite3.connect(':memory:'); self.app.row_factory = sqlite3.Row
        self.hist = sqlite3.connect(':memory:'); self.hist.row_factory = sqlite3.Row
        self.out = sqlite3.connect(':memory:')
        self.app.executescript('''
          CREATE TABLE etf_meta(fund_code TEXT,status TEXT,market TEXT);
          CREATE TABLE meta(key TEXT,value TEXT);
          CREATE TABLE weights_used(fund_code TEXT,stock_code TEXT,stock_name TEXT,weight REAL,weight_source TEXT,weight_quarter TEXT,coverage_pct REAL);
          INSERT INTO etf_meta VALUES('510050','active','SH'),('159999','snapshot_only','SZ');
          INSERT INTO meta VALUES('computed_date','2021-01-08');
          INSERT INTO weights_used VALUES('510050','600001','X',0.5,'index','current',100);
          INSERT INTO weights_used VALUES('159999','600002','Y',0.5,'index','current',100);
        ''')
        self.hist.executescript('''
          CREATE TABLE calendar(trade_date TEXT);
          CREATE TABLE shares(fund_code TEXT,trade_date TEXT,shares REAL);
          CREATE TABLE nav(fund_code TEXT,trade_date TEXT,unit_nav REAL,acc_nav REAL);
          CREATE TABLE nav_verified(fund_code TEXT,trade_date TEXT);
          CREATE TABLE jobs(dataset TEXT,job_key TEXT,status TEXT,rows_count INTEGER);
          CREATE TABLE fund_events(fund_code TEXT,trade_date TEXT);
          CREATE TABLE funds(fund_code TEXT,category TEXT,source TEXT,listed_date TEXT);
          INSERT INTO funds VALUES('510050','stock','SSE','2010-01-01');
          INSERT INTO jobs VALUES('nav-v2','510050:2020-12-31:2021-01-08','success',6);
          INSERT INTO fund_events VALUES('510050','2021-01-06');
        ''')
        dates=['2020-12-31','2021-01-04','2021-01-05','2021-01-06','2021-01-07','2021-01-08']
        self.hist.executemany('INSERT INTO calendar VALUES(?)',[(d,) for d in dates])
        for i,d in enumerate(dates):
            self.hist.execute('INSERT INTO shares VALUES(?,?,?)',('510050',d,100+i*10))
            self.hist.execute('INSERT INTO nav VALUES(?,?,?,?)',('510050',d,2,2))
            self.hist.execute('INSERT INTO nav_verified VALUES(?,?)',('510050',d))

    def test_event_days_and_source_gaps_are_not_fabricated(self):
        self.hist.execute("DELETE FROM nav_verified WHERE trade_date='2021-01-08'")
        result = stage(self.app,self.hist,self.out,'2021-01-08')
        self.assertEqual(result, {'funds':1,'estimated':2})
        rows = {r[0]:(r[1],r[2]) for r in self.out.execute('SELECT trade_date,quality,etf_flow FROM factors')}
        self.assertEqual(rows['2021-01-05'],('estimated',20))
        self.assertEqual(rows['2021-01-06'],('fund_event',None))
        self.assertEqual(rows['2021-01-07'],('fund_event',None))
        self.assertEqual(rows['2021-01-08'],('events_unverified',None))
        self.assertEqual(self.out.execute('SELECT count(*) FROM weights').fetchone()[0],1)

    def test_end_must_match_computed_date(self):
        with self.assertRaises(ValueError): stage(self.app,self.hist,self.out,'2021-01-07')

    def test_stock_day_requires_each_active_fund(self):
        db=sqlite3.connect(':memory:'); db.execute("ATTACH DATABASE ':memory:' AS src")
        db.executescript('''
          CREATE TABLE src.metadata(key TEXT,value TEXT);
          CREATE TABLE src.weights(fund_code TEXT,stock_code TEXT,weight REAL);
          CREATE TABLE src.factors(fund_code TEXT,trade_date TEXT,quality TEXT,etf_flow REAL);
          INSERT INTO src.weights VALUES('A','600001',0.5),('B','600001',0.25);
          INSERT INTO src.factors VALUES('A','2021-01-04','estimated',100),('B','2021-01-04','estimated',200),
            ('A','2021-01-05','estimated',80),('B','2021-01-05','fund_event',NULL);
        ''')
        self.assertEqual(expand(db),(1,2))
        self.assertEqual(db.execute('SELECT trade_date,flow_yuan,n_etf FROM stock_daily').fetchall(),[('2021-01-04',100,2)])
        self.assertEqual(db.execute('SELECT sum(contribution_yuan) FROM stock_etf_daily').fetchone()[0],100)

    def test_rounded_daily_series_uses_bounded_error(self):
        self.assertTrue(series_totals_agree(100.23,100,1000))
        self.assertFalse(series_totals_agree(106,100,1000))
        self.assertFalse(series_totals_agree(None,0,1000))

    def test_cleanup_is_restricted_to_the_successful_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'builds';root.mkdir();work=root/('a'*32);work.mkdir()
            for name in ('factors.db','expanded.db','app-prototype.db','factors.db-wal','app-prototype.db-shm','unrelated.db'):
                (work/name).write_text('x')
            (work/'review').mkdir();other=root/('b'*32);other.mkdir();(other/'factors.db').write_text('keep')
            removed=cleanup_successful_build(root,work)
            self.assertEqual(len(removed),5)
            self.assertTrue((work/'unrelated.db').exists());self.assertTrue((work/'review').is_dir())
            self.assertTrue((other/'factors.db').exists())
            with self.assertRaises(ValueError): cleanup_successful_build(root,root)
            with self.assertRaises(ValueError): cleanup_successful_build(root,Path(temp))


if __name__ == '__main__': unittest.main()
