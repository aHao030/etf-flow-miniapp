import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collect_index_history import match_benchmark,valid_prices
from collect_off_exchange import parse_reports

class Frame:
    def __init__(self,rows):self.rows=rows
    def to_dict(self,*a):return self.rows

class IndexSourceTests(unittest.TestCase):
    def test_benchmark_does_not_confuse_strategy_or_different_index(self):
        self.assertEqual(match_benchmark('深证100指数(价格)'),'399330')
        self.assertEqual(match_benchmark('沪深300指数'),'000300')
        self.assertIsNone(match_benchmark('沪深300红利低波动指数'))
        self.assertIsNone(match_benchmark('沪深300指数收益率*95%'))

    def test_raw_prices_preserve_real_weekend_and_exclude_future(self):
        base={'open':100,'high':102,'low':99,'close':101,'volume':0}
        got=valid_prices(Frame([{'date':'1992-01-04',**base},{'date':'2026-11-11',**base}]),'2026-11-09')
        self.assertEqual([r[0] for r in got],['1992-01-04'])

    def test_price_duplicates_and_nonfinite_fail(self):
        row={'date':'2026-11-09','open':100,'high':102,'low':99,'close':101,'volume':0}
        with self.assertRaises(ValueError):valid_prices(Frame([row,row]),'2026-11-09')
        with self.assertRaises(ValueError):valid_prices(Frame([{**row,'close':float('nan')}]),'2026-11-09')

    def report(self,rows):return 'var source={content:"期间申购（亿份）", "data":'+json.dumps(rows)+'};'
    def row(self,day,**kw):return {'BZDM':'000051','FSRQ':day,'QJSG':100,'QJSH':20,'QMZFE':1000,**kw}

    def test_reports_keep_missing_distinct_from_zero(self):
        rows=parse_reports(self.report([self.row('2026-03-31'),self.row('2026-06-30',QJSG='',QJSH=0)]),'000051','A')
        self.assertIsNone(rows[1]['subscription_shares'])
        self.assertEqual(rows[1]['redemption_shares'],0)
        self.assertIsNone(rows[1]['net_subscription_shares'])

    def test_report_identity_and_units_are_required(self):
        with self.assertRaises(ValueError):parse_reports(self.report([self.row('2026-03-31',BZDM='000052')]),'000051','A')
        with self.assertRaises(ValueError):parse_reports(self.report([self.row('2026-03-31')]).replace('亿份','亿元'),'000051','A')

    def test_missing_quarter_does_not_invent_period_start(self):
        rows=parse_reports(self.report([self.row('2025-12-31'),self.row('2026-06-30'),self.row('2026-09-30')]),'000051','A')
        self.assertIsNone(rows[1]['period_start'])
        self.assertEqual(rows[2]['period_start'],'2026-07-01')

if __name__=='__main__':unittest.main()
