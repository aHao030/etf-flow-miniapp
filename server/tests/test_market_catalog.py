import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('market_export',Path(__file__).resolve().parents[1]/'scripts'/'export_market_snapshot.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class MarketCatalogTests(unittest.TestCase):
    def test_official_universe_is_not_limited_to_legacy_holdings(self):
        old={'600519':{'stock_code':'600519','stock_name':'旧名','industry_code':'food','industry_name':'食品'},'000022':{'stock_code':'000022','stock_name':'历史产品'}}
        official={'600519':{'stock_name':'贵州茅台','listed_date':'2001-08-27'},'688001':{'stock_name':'华兴源创','listed_date':'2019-07-22'}}
        result=module.merge_stock_catalog(old,official)
        self.assertEqual(set(result),{'600519','000022','688001'})
        self.assertEqual(result['688001']['stock_name'],'华兴源创')
        self.assertEqual(result['688001']['listing_status'],'verified')
        self.assertIsNone(result['688001']['industry_code'])
        self.assertEqual(result['600519']['industry_code'],'food')
        self.assertEqual(result['600519']['stock_name'],'贵州茅台')
        self.assertEqual(result['000022']['listing_status'],'unknown')
        self.assertEqual(old['600519']['stock_name'],'旧名')

if __name__=='__main__':unittest.main()
