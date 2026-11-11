import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import sqlite3
import tempfile
import json

SOURCE = Path(__file__).resolve().parents[1]/'server'/'scripts'/'collect_market_holdings.py'
spec=importlib.util.spec_from_file_location('collect_market_holdings',SOURCE)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def response(code='510300',day='2026-09-30',stocks=None):
    stocks=stocks or [('600519','12.50%'),('00700','8.00%')]
    rows=''.join('<tr><td>1</td><td>'+s+'</td><td>name</td><td>price</td><td>change</td><td>'+w+'</td><td>10</td><td>20</td></tr>' for s,w in stocks)
    return 'var apidata={ content:"<div class=\'boxitem\'><h4><a href=\'https://fund.eastmoney.com/'+code+'.html\'>name</a>'+day+'</h4><table><tbody>'+rows+'</tbody></table></div>",arryear:[]};'

def combined(*reports):
    return 'var apidata={ content:"'+''.join(r.split('content:"',1)[1].rsplit('",arryear:',1)[0] for r in reports)+'",arryear:[]};'

class Holdings(unittest.TestCase):
    def test_known_ashares_only_and_do_not_normalize_partial_positions(self):
        day,rows,coverage=module.parse(response(),'510300',{'600519'},'2026-11-10')
        self.assertEqual(day,'2026-09-30');self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][2],.125);self.assertEqual(coverage,12.5)
    def test_wrong_fund_response_rejected(self):
        with self.assertRaisesRegex(ValueError,'identity'):
            module.parse(response('510050'),'510300',{'600519'},'2026-11-10')
    def test_duplicate_stock_and_future_report_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):
            module.parse(response(stocks=[('600519','2%'),('600519','3%')]),'510300',{'600519'},'2026-11-10')
        with self.assertRaisesRegex(ValueError,'future'):
            module.parse(response(day='2026-12-31'),'510300',{'600519'},'2026-11-10')
    def test_missing_weight_unit_rejected(self):
        with self.assertRaisesRegex(ValueError,'unit'):
            module.parse(response(stocks=[('600519','12.50')]),'510300',{'600519'},'2026-11-10')
    def test_no_report_is_unavailable_not_zero_holdings(self):
        with self.assertRaises(module.NoDisclosedReport):
            module.parse('var apidata={ content:"",arryear:[]};','510300',{'600519'},'2026-11-10')
    def test_year_rollover_uses_last_disclosed_report(self):
        with patch.object(module.fetchers,'http_get',side_effect=['var apidata={ content:"",arryear:[]};',response(day='2025-12-31')]),patch.object(module,'NEXT',0),patch.object(module.time,'sleep'):
            result=module.fetch_one('510300',{'600519'},'2026-01-03')
            self.assertEqual(result[1],'2025-12-31');self.assertIsNone(result[-1])
    def test_recent_broader_report_preferred_without_merging_dates(self):
        stocks=[(str(600000+i),'1%') for i in range(12)]
        body=combined(response(day='2026-09-30',stocks=[('600999','50%')]),response(day='2026-06-30',stocks=stocks))
        day,rows,coverage=module.parse(body,'510300',{s for s,_ in stocks}|{'600999'},'2026-11-10')
        self.assertEqual(day,'2026-06-30');self.assertEqual(len(rows),12);self.assertAlmostEqual(coverage,12)
        self.assertNotIn('600999',{r[0] for r in rows})
    def test_broader_report_older_than_twelve_months_does_not_displace_latest(self):
        stocks=[(str(600000+i),'1%') for i in range(12)]
        body=combined(response(day='2026-09-30',stocks=[('600999','50%')]),response(day='2025-06-30',stocks=stocks))
        day,rows,_=module.parse(body,'510300',{s for s,_ in stocks}|{'600999'},'2026-11-10')
        self.assertEqual(day,'2026-09-30');self.assertEqual(rows[0][0],'600999')
    def test_current_quarter_can_use_recent_prior_annual_disclosure(self):
        stocks=[(str(600000+i),'1%') for i in range(12)]
        with patch.object(module.fetchers,'http_get',side_effect=[response(day='2026-03-31'),response(day='2025-12-31',stocks=stocks)]) as http,patch.object(module,'NEXT',0),patch.object(module.time,'sleep'):
            result=module.fetch_one('510300',{'600519'}|{s for s,_ in stocks},'2026-05-01')
        self.assertEqual(result[1],'2025-12-31');self.assertEqual(len(result[2]),12)
        self.assertIn('month=6',http.call_args_list[0].args[0]);self.assertIn('month=12',http.call_args_list[1].args[0])
    def test_schema_version_marks_legacy_rows_for_upgrade(self):
        db=sqlite3.connect(':memory:');db.executescript(module.SCHEMA)
        db.execute("INSERT INTO fund_holdings VALUES('510300','partial','2026-09-30','disclosed_positions',20,10,'url','2026-11-10T10:00:00+08:00',NULL)")
        module.ensure_schema(db)
        self.assertEqual(db.execute('SELECT collector_version FROM fund_holdings').fetchone()[0],0)
        module.ensure_schema(db)
        self.assertEqual(db.execute('SELECT count(*) FROM fund_holdings WHERE collector_version=?',(module.COLLECTOR_VERSION,)).fetchone()[0],0)
    def test_invalid_broad_report_is_quarantined_without_discarding_valid_quarter(self):
        body=combined(response(day='2026-09-30',stocks=[('600519','12%')]),response(day='2026-06-30',stocks=[('600519','10%'),('600519','2%')]))
        notes=[];candidates=module.parse_candidates(body,'510300',{'600519'},'2026-11-10',notes)
        day,rows,coverage=module.select_report(candidates,'2026-11-10')
        self.assertEqual(day,'2026-09-30');self.assertEqual(coverage,12)
        self.assertEqual(notes,[{'report_date':'2026-06-30','reason':'duplicate A-share holding'}])
    def test_bad_identity_is_not_quarantined_to_silently_keep_another_report(self):
        body=combined(response(),response(code='510050',day='2026-06-30'))
        with self.assertRaisesRegex(ValueError,'identity'):
            module.parse(body,'510300',{'600519'},'2026-11-10')
    def test_invalid_optional_annual_preserves_verified_current_report(self):
        annual=response(day='2025-12-31',stocks=[('600519','10%'),('600519','2%')])
        with patch.object(module.fetchers,'http_get',side_effect=[response(),annual]),patch.object(module,'NEXT',0),patch.object(module.time,'sleep'):
            result=module.fetch_one('510300',{'600519'},'2026-11-10')
        self.assertEqual(result[1],'2026-09-30');self.assertIsNone(result[-1])
        self.assertEqual(result[-2],[{'report_date':'2025-12-31','reason':'duplicate A-share holding'}])
    def test_no_verified_current_report_does_not_search_older_report(self):
        bad=response(stocks=[('600519','10%'),('600519','2%')])
        with patch.object(module.fetchers,'http_get',return_value=bad) as http,patch.object(module,'NEXT',0),patch.object(module.time,'sleep'):
            result=module.fetch_one('510300',{'600519'},'2026-11-10')
        self.assertIsNone(result[1]);self.assertIn('RejectedReports',result[-1]);self.assertEqual(http.call_count,1)
    def test_official_directory_extends_old_stock_identity_subset(self):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE stock_listings(stock_code TEXT)')
        db.execute("INSERT INTO stock_listings VALUES('600777')")
        known=module.identity_codes(db,{'600519'})
        self.assertEqual(known,{'600519','600777'})
        _,rows,_=module.parse(response(stocks=[('600777','2%')]),'510300',known,'2026-11-10')
        self.assertEqual(rows[0][0],'600777')
    def test_response_cache_integrity_and_age(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=Path(folder);body=response()
            module.save_response(cache,'510300',2026,'6',body)
            today=module.datetime.now(module.ZoneInfo('Asia/Shanghai')).date().isoformat()
            self.assertEqual(module.cached_response(cache,'510300',2026,'6',today,7),body)
            path=module.response_cache_path(cache,'510300',2026,'6')
            row=json.loads(path.read_text(encoding='utf-8'));row['body']='tampered';path.write_text(json.dumps(row),encoding='utf-8')
            self.assertIsNone(module.cached_response(cache,'510300',2026,'6',today,7))
            module.save_response(cache,'510300',2026,'6',body)
            row=json.loads(path.read_text(encoding='utf-8'));row['observed_at']='2020-01-01';path.write_text(json.dumps(row),encoding='utf-8')
            self.assertIsNone(module.cached_response(cache,'510300',2026,'6',today,7))
    def test_targeted_retry_excludes_unavailable_reports_and_old_version(self):
        db=sqlite3.connect(':memory:');module.ensure_schema(db)
        for code,error,version in [('510300','FetchError: timeout',module.COLLECTOR_VERSION),('510500','NoDisclosedReport: missing',module.COLLECTOR_VERSION),('510050','FetchError: timeout',0)]:
            db.execute("INSERT INTO fund_holdings(fund_code,status,stock_count,source,observed_at,error,collector_version) VALUES(?,'unavailable',0,'url','2026-11-10',?,?)",(code,error,version))
        self.assertEqual(module.transport_retry_codes(db),{'510300'})

if __name__=='__main__': unittest.main()
