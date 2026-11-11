import os
import contextlib
import sqlite3
import sys
import tempfile
import unittest
import csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import app, config, db
from cache import cache

class MonitoringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db, self.old_csv, self.old_inputs = config.DB_PATH, config.INDUSTRY_CSV, config.INPUTS_DIR
        self.old_history = os.environ.get('ETF_HISTORY_DB_PATH')
        os.environ['ETF_HISTORY_DB_PATH'] = os.path.join(self.tmp.name,'missing-calendar.db')
        config.DB_PATH = os.path.join(self.tmp.name, 'test.db')
        config.INDUSTRY_CSV = os.path.join(self.tmp.name, 'industry.csv')
        config.INPUTS_DIR = self.tmp.name
        Path(config.INDUSTRY_CSV).write_text('stock_code,stock_name,industry_code,industry_name\n000002,未覆盖,01,行业\n', encoding='utf-8')
        with contextlib.closing(sqlite3.connect(config.DB_PATH)) as c:
            c.executescript(db.SCHEMA)
            c.execute("INSERT INTO meta(key,value) VALUES('latest_date','2026-10-05')")
            c.executemany('INSERT INTO etf_meta(fund_code,fund_name,status) VALUES(?,?,?)', [('510001','基金甲','active'),('510002','基金乙','snapshot_only')])
            c.executemany('INSERT INTO weights_used(fund_code,stock_code,stock_name,weight,weight_source,weight_quarter) VALUES(?,?,?,?,?,?)', [('510001','000001','测试股',0.5,'report(2026Q2)','2026Q2'),('510002','000001','测试股',0.2,'report(2026Q2)','2026Q2')])
            c.execute("INSERT INTO stock_meta(stock_code,stock_name,industry_code,industry_name) VALUES('000001','测试股','01','行业')")
            for day in range(1,6):
                date = f'2026-10-0{day}'
                c.execute('INSERT INTO etf_share_daily(fund_code,trade_date,market,shares) VALUES(?,?,?,?)', ('510001',date,'SH',100+day))
                if day == 3:
                    continue
                value = 10 if day == 2 else -10
                c.execute('INSERT INTO flow_daily(trade_date,stock_code,stock_name,flow_yuan) VALUES(?,?,?,?)', (date,'000001','测试股',value))
                c.execute('INSERT INTO flow_by_etf(trade_date,stock_code,fund_code,contribution_yuan) VALUES(?,?,?,?)', (date,'000001','510001',value))
                c.execute('INSERT INTO flow_derived(trade_date,stock_code,flow_1d_yuan,flow_5d_yuan,valid_days_5) VALUES(?,?,?,?,?)', (date,'000001',value,-20 if day==5 else None,day-1 if day>3 else day))
            c.execute("INSERT INTO stock_meta(stock_code,stock_name) VALUES('000003','正值')")
            c.execute("INSERT INTO flow_derived(trade_date,stock_code,flow_1d_yuan) VALUES('2026-10-05','000003',100)")
            c.commit()
        cache.invalidate()

    def tearDown(self):
        config.DB_PATH, config.INDUSTRY_CSV, config.INPUTS_DIR = self.old_db, self.old_csv, self.old_inputs
        if self.old_history is None: os.environ.pop('ETF_HISTORY_DB_PATH',None)
        else: os.environ['ETF_HISTORY_DB_PATH'] = self.old_history
        cache.invalidate()
        self.tmp.cleanup()

    def test_window_and_pending(self):
        d = app.stock_detail('000001', None, '5d')['data']
        self.assertEqual(d['movement']['net_flow'], -20)
        self.assertEqual(d['movement']['net_flow'], d['flow_5d'])
        self.assertEqual(d['movement']['valid_days'], 4)
        self.assertFalse(d['movement']['complete'])
        self.assertEqual(d['coverage']['pending_etfs'], 1)
        self.assertEqual(d['etf_contributions'][0]['flow'], -20)
        self.assertEqual(d['etf_contributions'][0]['status'], 'partial')
        self.assertIsNone(d['etf_contributions'][1]['flow'])
        self.assertEqual(d['etf_contributions'][1]['status'], 'pending')
        self.assertEqual(d['history_basis'], 'current_weights')

    def test_catalog_uncovered_and_out(self):
        r = app.stocks('未覆盖', 1, 20)['data']
        self.assertEqual(r['items'][0]['stock_code'], '000002')
        d = app.stock_detail('000002', None, '1d')['data']
        self.assertEqual(d['data_status'], 'no_data')
        self.assertIsNone(d['movement']['net_flow'])
        self.assertIsNone(d['movement']['inflow'])
        self.assertEqual(app.stocks("%' OR 1=1 --", 1, 20)['data']['total'], 0)
        r = app.rank('1d', None, 'out', None, 1, 20)['data']
        self.assertEqual(r['items'][0]['flow'], -10)
        self.assertTrue(all(row['flow'] < 0 for row in r['items']))
        r = app.rank('1d', None, 'in', None, 1, 20)['data']
        self.assertEqual([row['flow'] for row in r['items']], [100])
        self.assertEqual(app.meta()['data']['coverage']['pending_etfs'], 1)

    def test_month_anchor_leap_year_and_known_calendar(self):
        dates=['2023-02-28','2023-03-01','2024-02-28','2024-02-29']
        w=app.monitoring.window_for('2024-02-29','1y',(dates,True))
        self.assertEqual(w['window_start'],'2023-02-28')
        self.assertTrue(w['start_exclusive'])
        self.assertEqual(w['window_days'],3)
        self.assertEqual(w['_dates'],dates[1:])

    def test_long_period_unknown_calendar_preserves_partial(self):
        d=app.stock_detail('000001',None,'5y')['data']
        self.assertEqual(d['movement']['window_start'],'2021-10-05')
        self.assertEqual(d['movement']['window_end'],'2026-10-05')
        self.assertEqual(d['movement']['calendar_status'],'observed')
        self.assertIsNone(d['movement']['window_days'])
        self.assertIsNone(d['movement']['missing_days'])
        self.assertFalse(d['movement']['complete'])
        self.assertIsNone(d['movement']['net_flow'])
        self.assertIsNone(d['movement']['inflow'])
        self.assertEqual(d['movement']['observed_net_flow'],-20)
        self.assertEqual(d['movement']['available_start'],'2026-10-01')
        self.assertEqual(sum(r['flow'] for r in d['series']),-20)
        self.assertEqual(len(app.stock_detail('000001',None,'1d')['data']['series']),1)
        self.assertEqual(len(app.stock_detail('000001',None,'5d')['data']['series']),4)
        missing=app.stock_detail('000002',None,'5y')['data']['movement']
        self.assertIsNone(missing['net_flow'])
        self.assertIsNone(missing['observed_net_flow'])
        self.assertIsNone(missing['available_start'])
        self.assertIsNone(missing['available_end'])

    def test_history_calendar_reads_without_importing_fund_data(self):
        path=Path(self.tmp.name)/'history.db'
        with contextlib.closing(sqlite3.connect(path)) as c:
            c.execute('CREATE TABLE calendar(trade_date TEXT PRIMARY KEY,source TEXT)')
            c.executemany('INSERT INTO calendar VALUES(?,?)',[(d,'test') for d in ['2021-10-04','2021-10-06','2026-10-05']])
            c.commit()
        os.environ['ETF_HISTORY_DB_PATH']=str(path)
        w=app.monitoring.public_window('2026-10-05','5y')
        self.assertEqual(w['window_start'],'2021-10-05')
        self.assertEqual(w['calendar_source'],'history_calendar')
        self.assertEqual(w['calendar_status'],'verified')
        self.assertEqual(w['window_days'],2)
        self.assertFalse(app.stock_detail('000001',None,'5y')['data']['movement']['complete'])

    def test_incomplete_calendar_keeps_observed_older_days(self):
        w=app.monitoring.window_for('2026-10-05','5y',(['2026-10-03','2026-10-05'],True))
        self.assertEqual(w['calendar_status'],'observed')
        self.assertIsNone(w['window_days'])
        self.assertIn('2026-10-01',w['_dates'])

    def test_long_period_calendar_missing_then_complete(self):
        path=Path(self.tmp.name)/'trading_calendar.csv'
        with path.open('w',encoding='utf8',newline='') as f:
            w=csv.writer(f);w.writerow(['trade_date']);w.writerows([[x] for x in ['2026-07-05','2026-10-01','2026-10-02','2026-10-03','2026-10-04','2026-10-05']])
        d=app.stock_detail('000001',None,'3m')['data']
        self.assertEqual(d['movement']['window_days'],5)
        self.assertEqual(d['movement']['missing_days'],1)
        self.assertIsNone(d['movement']['net_flow'])
        self.assertEqual(d['etf_contributions'][0]['status'],'partial')
        with contextlib.closing(sqlite3.connect(config.DB_PATH)) as c:
            c.execute("UPDATE etf_meta SET status='active' WHERE fund_code='510002'")
            c.execute("INSERT INTO flow_daily(trade_date,stock_code,stock_name,flow_yuan) VALUES('2026-10-03','000001','测试股',0)")
            c.execute("INSERT INTO flow_derived(trade_date,stock_code,flow_1d_yuan) VALUES('2026-10-03','000001',0)")
            c.execute("INSERT INTO flow_by_etf(trade_date,stock_code,fund_code,contribution_yuan) VALUES('2026-10-03','000001','510001',0)")
            for day in range(1,6):
                c.execute('INSERT INTO flow_by_etf(trade_date,stock_code,fund_code,contribution_yuan) VALUES(?,?,?,0)',(f'2026-10-0{day}','000001','510002'))
            c.commit()
        cache.invalidate()
        d=app.stock_detail('000001',None,'3m')['data']
        self.assertTrue(d['movement']['complete'])
        self.assertEqual(d['movement']['net_flow'],-20)
        self.assertEqual(d['movement']['missing_days'],0)

if __name__ == '__main__':
    unittest.main()

