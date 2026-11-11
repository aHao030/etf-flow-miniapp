import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('broad_export',Path(__file__).resolve().parents[1]/'scripts'/'export_broad_snapshot.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class AggregateTests(unittest.TestCase):
    def test_prelisting_and_missing_are_not_zero(self):
        days=['2026-11-05','2026-11-06','2026-11-09']
        funds=[{'fund_code':'510300','listed_date':'2026-11-06'}]
        rows=module.aggregate(days,funds,{'510300':{'2026-11-09':['2026-11-09',1,1,1,0,'estimated']}},{'2026-11-05':3900})
        self.assertEqual(rows[0][1:5],[None,None,0,0])
        self.assertEqual(rows[1][1:5],[None,None,0,1])
        self.assertEqual(rows[2][1:5],[0,0,1,1])
    def test_partial_sum_is_not_complete(self):
        funds=[{'fund_code':str(i),'listed_date':'2020-01-01'} for i in range(2)]
        r=module.aggregate(['2026-11-09'],funds,{'0':{'2026-11-09':['2026-11-09',1,1,1,-20,'estimated']}},{})[0]
        self.assertEqual(r[1:6],[-20,None,1,2,0])
    def test_unknown_lifetime_and_delisting(self):
        funds=[{'fund_code':'0','first_observed':'2021-01-01'},{'fund_code':'1','listed_date':'2020-01-01','delisted_date':'2026-11-06'}]
        r=module.aggregate(['2026-11-09'],funds,{'0':{'2026-11-09':['2026-11-09',1,1,1,30,'estimated']}},{})[0]
        self.assertEqual(r[1:6],[30,None,1,1,1])

if __name__=='__main__':unittest.main()
