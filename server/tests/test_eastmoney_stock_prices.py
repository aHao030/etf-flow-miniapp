from argparse import Namespace
from contextlib import closing
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SERVER));sys.path.insert(0,str(SERVER/'scripts'))
from history import eastmoney_stock_prices as s
import collect_eastmoney_stock_prices as c

STOCK={'stock_code':'600519','stock_name':'sample','market':'SH','listed_date':'2021-01-04','lifecycle_status':'listed','source':'SSE official directory','source_url':'https://www.sse.com.cn/list/','observed_at':'2026-11-10T07:00:00Z'}
TARGET='2026-11-10'


def payload():
    return {'rc':0,'data':{'code':'600519','market':1,'name':'sample','dktotal':2,
        'klines':['2021-01-04,10,11,12,9,100,10000,0,0,0,0','2026-11-10,11,12,13,10,200,24000,0,0,0,0']}}


class EastmoneySource(unittest.TestCase):
    def test_identity_full_total_and_ohlc_are_required(self):
        self.assertEqual(s.parse_payload(payload(),STOCK,TARGET)['series'],[['2021-01-04',11.0],['2026-11-10',12.0]])
        for mutation in ('code','market','total','OHLC'):
            body=payload()
            if mutation=='code':body['data']['code']='600000'
            elif mutation=='market':body['data']['market']=0
            elif mutation=='total':body['data']['dktotal']=2000
            else:body['data']['klines'][0]='2021-01-04,10,13,12,9,100,10000'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):s.parse_payload(body,STOCK,TARGET)

    def test_raw_zero_volume_preserved_without_invented_close(self):
        body=payload();body['data']['klines'][0]='2021-01-04,10,11,12,9,0,0'
        bundle=s.parse_payload(body,STOCK,TARGET)
        self.assertEqual(len(bundle['candles']),2);self.assertEqual(bundle['series'],[['2026-11-10',12.0]])

    def test_record_cannot_mix_or_change_prices_or_claim_units(self):
        record=s.make_record(STOCK,TARGET,s.parse_payload(payload(),STOCK,TARGET),'2026-11-10T08:00:00Z')
        self.assertTrue(s.valid_record(record,STOCK))
        for field,value in [('provider','tencent'),('fqt',1),('volume_unit','shares'),('listed_date','2001-08-27'),('provider_total',1)]:
            broken=copy.deepcopy(record);broken[field]=value
            self.assertFalse(s.valid_record(broken,STOCK))
        record['series'][0][1]=999;self.assertFalse(s.valid_record(record,STOCK))


class EastmoneyCollector(unittest.TestCase):
    def setup_case(self,tmp):
        root=Path(tmp);server=root/'server';(server/'data').mkdir(parents=True)
        with closing(sqlite3.connect(server/'data/index_history.db')) as db:
            db.execute('CREATE TABLE calendar(trade_date TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO calendar VALUES(?)',[('2021-01-04',),('2021-01-05',),(TARGET,)]);db.commit()
        u={'schema':1,'kind':'current-listed-a-shares','generated_at':'2026-11-10T08:00:00Z','as_of':TARGET,'target_end':TARGET,
           'complete':False,'stock_count':1,'exchanges':[{'market':'SH','status':'verified','count':1,'observed_at':'2026-11-10T07:00:00Z'}],'stocks':[STOCK]}
        manifest=root/'universe.json';manifest.write_text(json.dumps(u),encoding='utf-8')
        args=Namespace(universe_file=manifest,cache_root=root/'cache',checkpoint=None,report=root/'report.json',
                       import_seeds=False,seed_manifest=None,dry_run=False,max_jobs=100,workers=1,interval=.6)
        return server,args,u

    def test_single_provider_resume_preserves_tencent_and_missing_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            server,args,_=self.setup_case(tmp)
            old=args.cache_root/'prices/v1/600519/2020.json';old.parent.mkdir(parents=True);old.write_bytes(b'unchanged Tencent fixture')
            with patch.object(c.p,'SERVER',server),patch.object(c.p,'read_catalog',return_value=(TARGET,{})),patch.object(c.source,'fetch_history',return_value=s.parse_payload(payload(),STOCK,TARGET)) as fetch:
                c.run(args);c.run(args);self.assertEqual(fetch.call_count,1)
            self.assertEqual(old.read_bytes(),b'unchanged Tencent fixture')
            report=json.loads((args.cache_root/'coverage.json').read_text(encoding='utf-8'))
            self.assertEqual(report['provider'],'eastmoney');self.assertEqual(report['stocks'][0]['missing_open_days'],1)
            self.assertEqual(report['stocks'][0]['status'],'partial')

    def test_access_denial_stops_and_persistent_backoff_does_not_shorten(self):
        with tempfile.TemporaryDirectory() as tmp:
            server,args,_=self.setup_case(tmp)
            with patch.object(c.p,'SERVER',server),patch.object(c.p,'read_catalog',return_value=(TARGET,{})),patch.object(c.source,'fetch_history',side_effect=RuntimeError('eastmoney returned HTTP 403')) as fetch:
                c.run(args);c.run(args);self.assertEqual(fetch.call_count,1)
            checkpoint=next((args.cache_root/'batches').glob('*.sqlite'))
            with closing(sqlite3.connect(checkpoint)) as db:
                before=dict(db.execute('SELECT key,value FROM meta'))['blocked_until']
                c.store_stop(db,'eastmoney returned HTTP 501',900)
                after=dict(db.execute('SELECT key,value FROM meta'))['blocked_until']
                self.assertEqual(after,before)

    def test_retained_raw_date_cannot_erase_previously_verified_price_and_retries_back_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);now=datetime(2026,11,10,12,tzinfo=timezone.utc)
            old_body=payload();old_body['data']['klines'][-1]=old_body['data']['klines'][-1].replace(TARGET,'2026-11-09')
            old=s.make_record(STOCK,'2026-11-09',s.parse_payload(old_body,STOCK,'2026-11-09'),'2026-11-09T12:00:00Z')
            path=c.location(root,STOCK['stock_code']);c.p.atomic_json(path,old);old_bytes=path.read_bytes()
            new_body=payload();new_body['data']['klines'].insert(1,old_body['data']['klines'][-1]);new_body['data']['dktotal']=3
            new_body['data']['klines'][0]='2021-01-04,10,11,12,9,0,0'
            bundle=s.parse_payload(new_body,STOCK,TARGET)
            self.assertEqual({r[0] for r in old['candles']}-{r[0] for r in bundle['candles']},set())
            self.assertTrue(s.valid_record(s.make_record(STOCK,TARGET,bundle,'2026-11-10T12:00:00Z'),STOCK))
            with patch.object(c.common,'utcnow',return_value='2026-11-10T12:00:00Z'),patch.object(c.source,'fetch_history',return_value=bundle) as fetch:
                failed=c.refresh(root,STOCK,TARGET,fetch,now)
                self.assertEqual(failed['status'],'failed');self.assertTrue(failed['retained_previous'])
                self.assertEqual(failed['reason'],'same-provider price history regression')
                self.assertEqual(path.read_bytes(),old_bytes)
                retry=c.refresh(root,STOCK,TARGET,fetch,now)
                self.assertEqual(retry['status'],'backoff');self.assertEqual(retry['network_requests'],0);self.assertEqual(fetch.call_count,1)
            self.assertEqual(c.p.read_json(c.attempt_location(root,STOCK['stock_code']))['reason'],'same-provider price history regression')

    def test_dry_run_is_zero_request_and_keeps_public_tencent_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            server,args,_=self.setup_case(tmp);args.dry_run=True
            args.cache_root.mkdir();(args.cache_root/'coverage.json').write_bytes(b'old public coverage')
            with patch.object(c.p,'SERVER',server),patch.object(c.p,'read_catalog',return_value=(TARGET,{})),patch.object(c.source,'fetch_history') as fetch:
                c.run(args);fetch.assert_not_called()
            self.assertEqual((args.cache_root/'coverage.json').read_bytes(),b'old public coverage')

    def test_seed_import_requires_hash_and_preserves_independent_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,args,u=self.setup_case(tmp);sample=Path(tmp)/'sample.json'
            raw={'source':s.SOURCE,'adjustment':'none','rows':s.parse_payload(payload(),STOCK,TARGET)['candles']}
            sample.write_text(json.dumps(raw),encoding='utf-8')
            seed={'schema':1,'provider':'eastmoney','source':s.SOURCE,'target_end':TARGET,'samples':[
                {'stock_code':'600519','file':str(sample),'sha256':hashlib.sha256(sample.read_bytes()).hexdigest(),
                 'identity_code':'600519','identity_market':1,'provider_total':2,'fetched_at':'2026-11-10T08:00:00Z'}]}
            manifest=Path(tmp)/'seed.json';manifest.write_text(json.dumps(seed),encoding='utf-8')
            with patch.object(c.source,'fetch_history') as fetch:
                c.import_seeds(manifest,u,args.cache_root);fetch.assert_not_called()
            self.assertTrue(s.valid_record(c.p.read_json(c.location(args.cache_root,'600519')),STOCK))
            sample.write_text('{}',encoding='utf-8')
            with self.assertRaises(ValueError):c.import_seeds(manifest,u,args.cache_root)


if __name__=='__main__':unittest.main()
