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


class HoldingsPersistence(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        module.ensure_schema(self.db)
        self.source='https://fundf10.eastmoney.com/jjcc_510300.html'
        self.old_hash='a'*64
        self.observed='2026-11-10T13:30:12+08:00'
        self.attempted='2026-11-17T21:45:00+08:00'
        with self.db:
            self.db.execute("""INSERT INTO fund_holdings VALUES(
                '510300','partial','2026-06-30','latest_disclosed_positions',15,2,
                ?,?,NULL,?,'[]',?)""",(self.source,self.observed,module.COLLECTOR_VERSION,self.old_hash))
            self.db.executemany('INSERT INTO stock_weights VALUES(?,?,?,?,?,?)',[
                ('510300','600519','stock A',.125,'2026-06-30',self.source),
                ('510300','600000','stock B',.025,'2026-06-30',self.source)])

    def disclosure(self):
        return (self.db.execute('SELECT * FROM fund_holdings ORDER BY fund_code').fetchall(),
                self.db.execute('SELECT * FROM stock_weights ORDER BY fund_code,stock_code').fetchall())

    def failure(self, code='510300', identity_hash=None):
        result=(code,None,[],None,'https://fundf10.eastmoney.com/jjcc_'+code+'.html',[],
                'FetchError: source unavailable')
        return module.record_result(self.db,result,'2026-11-17',self.attempted,identity_hash or self.old_hash)

    def test_failed_refresh_preserves_exact_valid_report_weights_and_identity_time(self):
        before=self.disclosure()
        self.assertEqual(self.failure(identity_hash='b'*64),'retained_previous')
        self.assertEqual(self.disclosure(),before)
        self.assertEqual(self.db.execute('SELECT asof,attempted_at,error,identity_catalogue_hash,retained_previous FROM holding_failures').fetchone(),
            ('2026-11-17',self.attempted,'FetchError: source unavailable','b'*64,1))

    def test_failed_refresh_preserves_verified_no_ashare_report(self):
        with self.db:
            self.db.execute('DELETE FROM stock_weights')
            self.db.execute("UPDATE fund_holdings SET status='no_ashare_holdings',coverage_pct=0,stock_count=0")
        before=self.disclosure()
        self.assertEqual(self.failure(),'retained_previous')
        self.assertEqual(self.disclosure(),before)

    def test_failed_new_fund_remains_unavailable_not_a_zero_holdings_report(self):
        self.assertEqual(self.failure('510500'),'unavailable')
        self.assertEqual(self.db.execute("SELECT status,report_date,coverage_pct,stock_count,error FROM fund_holdings WHERE fund_code='510500'").fetchone(),
            ('unavailable',None,None,0,'FetchError: source unavailable'))
        self.assertEqual(self.db.execute("SELECT retained_previous FROM holding_failures WHERE fund_code='510500'").fetchone()[0],0)
        self.assertNotIn('510500',module.reusable_codes(self.db,'2026-11-17',7,self.old_hash))

    def test_invalid_old_weight_is_not_retained_as_a_verified_disclosure(self):
        with self.db:self.db.execute("UPDATE stock_weights SET weight=0 WHERE stock_code='600519'")
        self.assertEqual(self.failure(),'unavailable')
        self.assertEqual(self.db.execute('SELECT count(*) FROM stock_weights').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT retained_previous FROM holding_failures').fetchone()[0],0)

    def test_inclusive_seven_day_cache_and_failed_attempt_do_not_extend_observation(self):
        self.assertIn('510300',module.reusable_codes(self.db,'2026-11-17',7,self.old_hash))
        self.assertNotIn('510300',module.reusable_codes(self.db,'2026-11-18',7,self.old_hash))
        self.failure()
        self.assertEqual(self.db.execute('SELECT observed_at FROM fund_holdings').fetchone()[0],self.observed)
        self.assertNotIn('510300',module.reusable_codes(self.db,'2026-11-17',7,self.old_hash))
        self.assertEqual(module.transport_retry_codes(self.db),{'510300'})

    def test_later_success_atomically_replaces_single_period_and_clears_retry(self):
        self.failure()
        result=('510300','2026-09-30',[('600777','stock C',.08)],8,self.source,[],None)
        later='2026-11-17T22:30:00+08:00'
        self.assertEqual(module.record_result(self.db,result,'2026-11-17',later,'b'*64),'partial')
        self.assertEqual(self.db.execute('SELECT stock_code,weight,report_date FROM stock_weights').fetchall(),
            [('600777',.08,'2026-09-30')])
        self.assertEqual(self.db.execute('SELECT observed_at,identity_catalogue_hash,coverage_pct FROM fund_holdings').fetchone(),(later,'b'*64,8))
        self.assertEqual(self.db.execute('SELECT count(*) FROM holding_failures').fetchone()[0],0)
        self.assertEqual(module.transport_retry_codes(self.db),set())
        self.assertIn('510300',module.reusable_codes(self.db,'2026-11-17',7,'b'*64))

    def test_failed_atomic_replacement_keeps_previous_report_and_retry_evidence(self):
        self.failure()
        before=self.disclosure()
        failures=self.db.execute('SELECT * FROM holding_failures').fetchall()
        duplicate=('510300','2026-09-30',[('600777','C',.04),('600777','C',.04)],8,self.source,[],None)
        with self.assertRaises(sqlite3.IntegrityError):
            module.record_result(self.db,duplicate,'2026-11-17',self.attempted,self.old_hash)
        self.assertEqual(self.disclosure(),before)
        self.assertEqual(self.db.execute('SELECT * FROM holding_failures').fetchall(),failures)

if __name__=='__main__': unittest.main()
