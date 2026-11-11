import io, json, os, sys, tempfile, time, unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import openpyxl
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from history import backfill, providers, storage, nav_backfill

class HistoryTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.old=os.environ.get('ETF_HISTORY_DB_PATH')
  os.environ['ETF_HISTORY_DB_PATH']=str(Path(self.temp.name)/'history.db'); self.db=storage.connect()
 def tearDown(self):
  self.db.close()
  if self.old is None: os.environ.pop('ETF_HISTORY_DB_PATH',None)
  else: os.environ['ETF_HISTORY_DB_PATH']=self.old
  self.temp.cleanup()
 def test_resume_atomic_and_lifecycle(self):
  one=[('510300','新名字','2021-01-05',10000.0)]
  backfill.save_day(self.db,'shares','2021-01-05',one,'SH','sse')
  backfill.save_day(self.db,'shares','2021-01-04',[('510300','旧名字','2021-01-04',9000.0)],'SH','sse')
  row=self.db.execute('select * from funds').fetchone()
  self.assertEqual((row['first_seen'],row['last_seen'],row['fund_name'],row['lifecycle_status']),('2021-01-04','2021-01-05','新名字','unknown'))
  with patch.object(providers,'dates',return_value=['2021-01-04','2021-01-05']),patch.object(providers,'sse_shares',side_effect=AssertionError('must not refetch')),patch.object(backfill.time,'sleep'):
   backfill.run_shares(self.db,'2021-01-04','2021-01-05',0)
  self.assertTrue(storage.success(self.db,'shares','2021-01-04'))
  self.assertEqual(self.db.execute('select count(*) from shares').fetchone()[0],2)
 def test_sse_validates_dates_duplicates_pagination_units(self):
  row={'STAT_DATE':'2021-01-04','SEC_CODE':'510300','SEC_NAME':'测试','TOT_VOL':'1.5'}
  def source(data,total):return json.dumps({'result':data,'pageHelp':{'total':total}},ensure_ascii=False)
  with patch.object(providers.fetchers,'http_get',return_value=source([row],1)):
   self.assertEqual(providers.sse_shares('2021-01-04')[0][3],15000)
  for payload in (source([row,row],2),source([row],2),source([{**row,'STAT_DATE':'2021-01-05'}],1),source([{**row,'TOT_VOL':'NaN'}],1),source([],0)):
   with patch.object(providers.fetchers,'http_get',return_value=payload),self.assertRaises(ValueError):providers.sse_shares('2021-01-04')
 def test_cross_market_collision_aborts_entire_day(self):
  with self.db:self.db.execute("insert into funds(fund_code,market) values('159915','SZ')")
  with self.assertRaises(ValueError):
   backfill.save_day(self.db,'shares','2021-01-04',[('510300','沪市','2021-01-04',1),('159915','冲突','2021-01-04',2)],'SH','sse')
  self.assertEqual(self.db.execute('select count(*) from shares').fetchone()[0],0)
  self.assertFalse(storage.success(self.db,'shares','2021-01-04'))
 def test_money_shares_cannot_overwrite_normal_etf(self):
  day='2021-01-04'
  backfill.save_day(self.db,'shares',day,[('510001','普通ETF',day,100)],'SH','sse_daily_etf_scale')
  with self.assertRaises(ValueError):
   backfill.save_day(self.db,'shares-sh-money',day,[('510001','货币ETF',day,200)],'SH','sse_daily_money_fund_scale')
  self.assertEqual(self.db.execute('select shares from shares').fetchone()[0],100)
 def test_money_source_date_and_units(self):
  row={'STAT_DATE':'2021-01-04','SEC_CODE':'511880','SEC_NAME':'货币ETF','TOT_VOL':'2.5'}
  payload=json.dumps({'result':[row],'pageHelp':{'total':1}},ensure_ascii=False)
  with patch.object(providers,'money_get',return_value=payload) as get:
   data=providers.sse_currency_shares('2021-01-04')
  self.assertIn('COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_JYXJJ_SEARCH_L',get.call_args.args[0])
  self.assertEqual(data[0][3],25000)
  with patch.object(providers,'money_get',return_value=payload.replace('2021-01-04','2021-01-05')),self.assertRaises(ValueError):providers.sse_currency_shares('2021-01-04')
 def test_money_global_rate_limit_including_retry(self):
  times=[]
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self):return b'{}'
  def fake_open(*args,**kwargs):
   times.append(time.monotonic())
   if len(times)==1:raise OSError('test retry')
   return Response()
  providers._money_next=0
  with patch.object(providers.urllib.request,'urlopen',side_effect=fake_open),patch.object(providers.config,'FETCH_BACKOFF',0):
   with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(providers.money_get,['https://example.test']*3))
  self.assertEqual(len(times),4)
  self.assertTrue(all(b-a>=.34 for a,b in zip(times,times[1:])),times)
 def test_invalid_dates_and_invalid_numeric(self):
  for d in ('2021-02-29','2021-1-01','garbage'):
   with self.assertRaises(ValueError): providers.valid_date(d)
  for value in ('-2','NaN','Infinity',''):
   with self.assertRaises(ValueError):providers.number(value)
 def test_szse_raw_date_units_and_duplicates(self):
  def workbook(rows):
   wb=openpyxl.Workbook();ws=wb.active
   ws.append(['日期','基金代码','基金简称','基金规模(份)'])
   for row in rows:ws.append(row)
   b=io.BytesIO();wb.save(b);return b.getvalue()
  row=['2021-01-04','159915','ETF','5,407,454,936']
  with patch.object(providers.fetchers,'http_bytes',return_value=workbook([row])):
   self.assertEqual(providers.sz_shares('2021-01-04','2021-01-04',{'2021-01-04'})['2021-01-04'][0][3],5407454936)
  for rows in ([row,row], [['2021-01-05','159915','ETF','1']], [['2021-01-04','159915','ETF','NaN']]):
   with patch.object(providers.fetchers,'http_bytes',return_value=workbook(rows)),self.assertRaises(ValueError):
    providers.sz_shares('2021-01-04','2021-01-04',{'2021-01-04'})
 def test_daily_nav_fairness_and_job_limit(self):
  with self.db:
   self.db.execute("insert into calendar values('2026-10-23','test')")
   self.db.executemany("insert into funds(fund_code,last_seen,market) values(?,?,'SH')", [('510001','2026-10-30'),('510002','2026-10-30'),('510003','2026-10-30')])
   self.db.execute("insert into nav values('510001','2026-10-29',1,NULL,'test')")
   storage.record(self.db,'nav-v2','510002:2026-10-23:2026-10-30','success',1)
  with patch.object(nav_backfill,'run',return_value={'success':1,'unsupported':0,'failed':0,'skipped':0}) as run:
   result=backfill.run_nav_bounded(self.db,'2026-10-25','2026-10-30',1)
  self.assertEqual(run.call_args.kwargs['fund'],'510003')
  self.assertEqual(run.call_args.args[0].isoformat(),'2026-10-24')
  self.assertEqual(result['success'],1)
 def test_success_without_target_nav_is_retried_when_shares_arrive(self):
  with self.db:
   self.db.execute("insert into funds(fund_code,last_seen,market) values('510001','2026-10-30','SH')")
   self.db.execute("insert into calendar values('2026-10-23','test')")
   self.db.execute("insert into shares values('510001','2026-10-30',100,'test')")
   storage.record(self.db,'nav-v2','510001:2026-10-23:2026-10-30','success',1)
  with patch.object(nav_backfill,'run',return_value={'success':1}) as run:
   backfill.run_nav_bounded(self.db,'2026-10-25','2026-10-30',1)
  self.assertEqual(run.call_args.kwargs['fund'],'510001')
 def test_exchange_refresh_updates_previously_successful_days(self):
  day='2026-10-30'
  backfill.save_day(self.db,'shares',day,[('510001','沪基金',day,100)],'SH','sse_daily_etf_scale')
  backfill.save_day(self.db,'shares-sz',day,[('159001','深基金',day,200)],'SZ','szse_daily_etf_scale')
  with patch.object(providers,'sse_shares',return_value=[('510001','沪基金',day,101)]) as sh, \
       patch.object(providers,'sz_shares',return_value={day:[('159001','深基金',day,201)]}) as sz, \
       patch.object(backfill.time,'sleep'):
   backfill.run_shares(self.db,day,day,0,days=[day])
   backfill.run_sz(self.db,day,day,0,days=[day])
   sh.assert_not_called();sz.assert_not_called()
   backfill.run_shares(self.db,day,day,0,refresh=True,days=[day])
   backfill.run_sz(self.db,day,day,0,refresh=True,days=[day])
  self.assertEqual(self.db.execute("select shares from shares where fund_code='510001'").fetchone()[0],101)
  self.assertEqual(self.db.execute("select shares from shares where fund_code='159001'").fetchone()[0],201)
 def test_all_only_refreshes_short_calendar_windows(self):
  with self.db:self.db.execute("insert into calendar values('2026-10-30','test')")
  args=['backfill','--dataset','all','--start','2026-10-01','--end','2026-10-30','--max-jobs','1']
  for count,expected in ((5,True),(11,False)):
   days=[f'2026-10-{i:02d}' for i in range(1,count+1)]
   with patch.object(sys,'argv',args),patch.object(backfill,'save_calendar',return_value=days), \
        patch.object(backfill,'run_catalog'),patch.object(backfill,'update_sse_current'), \
        patch.object(backfill,'run_shares',return_value=0) as sh,patch.object(backfill,'run_sz',return_value=0) as sz,patch.object(backfill,'run_money',return_value=0) as money, \
        patch.object(backfill,'run_nav_bounded',return_value={'failed':0}):
    backfill.main()
   self.assertEqual(sh.call_args.kwargs['refresh'],expected)
   self.assertEqual(sz.call_args.kwargs['refresh'],expected)
   self.assertEqual(money.call_args.kwargs['refresh'],expected)
 def test_current_sse_presence_does_not_imply_delisting(self):
  today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
  backfill.save_day(self.db,'shares',today,[('510001','当期名称',today,100)],'SH','sse_daily_etf_scale')
  backfill.save_day(self.db,'shares-sh-money',today,[('511880','货币基金',today,200)],'SH','sse_daily_money_fund_scale')
  backfill.save_day(self.db,'shares','2021-01-04',[('510002','历史基金','2021-01-04',100)],'SH','sse_daily_etf_scale')
  backfill.update_sse_current(self.db)
  self.assertEqual(tuple(self.db.execute("select current_seen,lifecycle_status,delisted_date from funds where fund_code='510002'").fetchone()),(0,'unknown',None))
  self.assertEqual(tuple(self.db.execute("select current_seen,lifecycle_status from funds where fund_code='510001'").fetchone()),(1,'current_seen'))
  self.assertEqual(tuple(self.db.execute("select current_seen,lifecycle_status from funds where fund_code='511880'").fetchone()),(1,'current_seen'))
  backfill.save_day(self.db,'shares','2021-01-05',[('510001','历史名称','2021-01-05',100)],'SH','sse_daily_etf_scale')
  self.assertEqual(self.db.execute("select fund_name from funds where fund_code='510001'").fetchone()[0],'当期名称')
  with self.db:
   self.db.execute("update funds set delisted_date='2021-03-12',lifecycle_status='delisted' where fund_code='510002'")
  backfill.update_sse_current(self.db)
  self.assertEqual(tuple(self.db.execute("select lifecycle_status,delisted_date from funds where fund_code='510002'").fetchone()),('delisted','2021-03-12'))
 def test_sse_current_sources_may_have_different_latest_dates(self):
  day=datetime.now(ZoneInfo('Asia/Shanghai')).date()
  normal=day.isoformat(); money=(day-timedelta(days=1)).isoformat()
  backfill.save_day(self.db,'shares',normal,[('510001','普通ETF',normal,100)],'SH','sse_daily_etf_scale')
  backfill.save_day(self.db,'shares-sh-money',money,[('511880','货币ETF',money,200)],'SH','sse_daily_money_fund_scale')
  backfill.update_sse_current(self.db)
  self.assertEqual(self.db.execute("select count(*) from funds where current_seen=1").fetchone()[0],2)
  self.assertEqual(self.db.execute("select status from jobs where dataset='catalog' and job_key='sse-current'").fetchone()[0],'success')
 def test_sse_current_incomplete_without_money_source(self):
  day=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
  backfill.save_day(self.db,'shares',day,[('510001','普通ETF',day,100)],'SH','sse_daily_etf_scale')
  backfill.update_sse_current(self.db)
  self.assertEqual(self.db.execute("select status from jobs where dataset='catalog' and job_key='sse-current'").fetchone()[0],'pending')

if __name__=='__main__': unittest.main()
