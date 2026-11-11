import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
import {createHash} from 'node:crypto';
globalThis.ASSETS={'/index.html':{body:'hello',contentType:'text/html; charset=utf-8'}};
const src=await readFile(new URL('../worker/index.js',import.meta.url),'utf8');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'))).default;
class Bucket {
  objects=new Map();
  async get(k){const v=this.objects.get(k);return v===undefined?null:{async text(){return v.body}};}
  async head(k){const v=this.objects.get(k);return v===undefined?null:{customMetadata:v.metadata,size:Buffer.byteLength(v.body)};}
  async put(k,v,opts={}){this.objects.set(k,{body:typeof v==='string'?v:Buffer.from(v).toString('utf8'),metadata:opts.customMetadata});}
}
function req(path,options={}) {return new Request('https://trial.example'+path,options)}
function auth(method,body,token='secret') {return {method,headers:{authorization:'Bearer '+token,'content-type':'application/json'},body:JSON.stringify(body)}}
async function call(env,path,options){const response=await worker.fetch(req(path,options),env);let body;try{body=await response.json()}catch{} return {response,body};}
const files=['meta','catalog','ranks','industries','etfs','etf-details','stocks-0000'];
const dataset={
 meta:{version:'v1',trade_date:'2026-10-30',generated_at:'2026-10-31T00:00:00Z'},
 catalog:{items:[{stock_code:'000001',stock_name:'测试甲',data_status:'ok'},{stock_code:'000002',stock_name:'未覆盖',data_status:'no_data'}]},
 ranks:{'1d':[{stock_code:'000001',flow:-5,industry:'a'},{stock_code:'000003',flow:10,industry:'a'},{stock_code:'000004',flow:3,industry:'a'},{stock_code:'000005',flow:null}], '5d':[], '20d':[]},
 industries:{items:[{industry_code:'a',industry_name:'测试行业'}]}, etfs:{items:[{etf_code:'510001',category:'宽基'}]},
 'etf-details':{'510001':{etf_name:'基金'}},
 'stocks-0000':{'000001':{base:{stock_code:'000001',stock_name:'测试甲',flow_1d:-5},periods:{'1d':{movement:{net_flow:-5}},'5d':{movement:{net_flow:null}},'20d':{movement:{net_flow:null}}}}}
};
async function upload(env,key,val=dataset[key]) {return call(env,`/internal/snapshots/v1/${key}.json`,auth('PUT',val));}
const hashes=() => Object.fromEntries(files.map(k=>[k,createHash('sha256').update(JSON.stringify(dataset[k])).digest('hex')]));
async function setup(){let env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};for(const key of files) assert.equal((await upload(env,key)).response.status,200);return env;}
test('authenticated writes, validation, partial publication, immutable versions',async()=>{
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 assert.equal((await call(env,'/internal/snapshots/v1/meta.json',auth('PUT',dataset.meta,'bad'))).response.status,401);
 assert.equal((await call(env,'/internal/snapshots/../meta.json',auth('PUT',dataset.meta))).response.status,400);
 assert.equal((await call(env,'/internal/snapshots/v1/unexpected.json',auth('PUT',{}))).response.status,400);
 assert.equal((await call(env,'/internal/snapshots/v1/meta.json',{method:'PUT',headers:{authorization:'Bearer secret','content-type':'text/plain'},body:'{}'})).response.status,400);
 assert.equal((await upload(env,'meta')).response.status,200);
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:hashes(),trade_date:'2026-10-30'}))).response.status,409);
 assert.equal(await env.BUCKET.get('latest.json'),null);
 for(const k of files.slice(1)) await upload(env,k);
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files:[...files,'meta'],hashes:hashes(),trade_date:'2026-10-30'}))).response.status,400);
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:hashes(),trade_date:'2026-10-29'}))).response.status,409);
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:{...hashes(),meta:'0'.repeat(64)},trade_date:'2026-10-30'}))).response.status,409);
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:hashes(),trade_date:'2026-10-30'}))).response.status,200);
 assert.equal((await upload(env,'meta')).response.status,409);
});
test('read, private cache headers, signed rank, pagination and null',async()=>{
 const env=await setup();
 await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:hashes(),trade_date:'2026-10-30'}));
 let r=await call(env,'/api/v1/rank?direction=in&page_size=1');
 assert.equal(r.body.data.total,2);assert.equal(r.body.data.items[0].flow,10);assert.equal(r.body.data.has_more,true);assert.equal(r.response.headers.get('cache-control'),'no-store');
 r=await call(env,'/api/v1/rank?direction=out');assert.deepEqual(r.body.data.items.map(x=>x.flow),[-5]);
 r=await call(env,'/api/v1/stocks?q=未覆盖');assert.equal(r.body.data.total,1);
 r=await call(env,'/api/v1/stock/000002?period=5d');assert.equal(r.body.data.movement.net_flow,null);assert.equal(r.body.data.movement.complete,false);
 r=await call(env,'/api/v1/stock/000001?period=1d');assert.equal(r.body.data.movement.net_flow,-5);
 assert.equal((await call(env,'/api/v1/stock/000001?trade_date=2026-10-29')).response.status,404);
 assert.equal((await call(env,'/api/v1/stock/xx')).response.status,400);
 assert.equal((await call(env,'/api/v1/stocks?page_size=101')).response.status,400);
 assert.equal((await call(env,'/api/v1/stocks?q='+'x'.repeat(51))).response.status,400);
 assert.equal((await call(env,'/api/v1/etf/510001')).body.data.etf_name,'基金');
});
test('reject rollback, unlisted data, oversized input and conditional cache reuse',async()=>{
 const env=await setup();
 await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:hashes(),trade_date:'2026-10-30'}));
 const v2={...dataset,meta:{version:'v2',trade_date:'2026-10-30',generated_at:'2026-10-30T23:59:59Z'}};
 const v2hash={};
 for(const key of files) {
   const value=v2[key]; v2hash[key]=createHash('sha256').update(JSON.stringify(value)).digest('hex');
   assert.equal((await call(env,`/internal/snapshots/v2/${key}.json`,auth('PUT',value))).response.status,200);
 }
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v2',files,hashes:v2hash,trade_date:'2026-10-30'}))).response.status,409);
 assert.equal((await call(env,'/internal/snapshots/v3/meta.json',auth('PUT',{version:'v3',trade_date:'2026-02-31',generated_at:'2026-10-31T00:00:00Z'}))).response.status,400);
 assert.equal((await call(env,'/internal/snapshots/v3/catalog.json',auth('PUT',{items:[],padding:'x'.repeat(2*1024*1024)}))).response.status,400);
 let r=await worker.fetch(req('/',{headers:{'If-None-Match':'"old"'}}),env);
 assert.equal(r.status,200); assert.equal(r.headers.get('cache-control'),'no-store');assert.equal(await r.text(),'hello');
 r=await worker.fetch(req('/api/v1/meta',{headers:{'If-None-Match':'"old"'}}),env);
 assert.equal(r.status,200); assert.equal(r.headers.get('cache-control'),'no-store');
});


test('legacy snapshots remain readable and unavailable long periods fail safely',async()=>{
 const env=await setup();
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:hashes(),trade_date:'2026-10-30'}))).response.status,200);
 assert.equal((await call(env,'/api/v1/stock/000001?period=1d')).response.status,200);
 assert.equal((await call(env,'/api/v1/stock/000001?period=5y')).response.status,503);
 assert.equal((await call(env,'/api/v1/stock/000002?period=5y')).response.status,503);
 assert.equal((await call(env,'/api/v1/rank?period=5y')).response.status,400);
 assert.equal((await call(env,'/api/v1/stock/000001?period=99y')).response.status,400);
});

test('compact stock history expands on read and rejects invalid rows',async()=>{
 const values=structuredClone(dataset), base=values['stocks-0000']['000001'].base;
 base.series_compact=[['2026-10-29',1.5,null],['2026-10-30',-5,2.3]];
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'}, hs={};
 for(const key of files){hs[key]=createHash('sha256').update(JSON.stringify(values[key])).digest('hex');assert.equal((await upload(env,key,values[key])).response.status,200);}
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:hs,trade_date:'2026-10-30'}))).response.status,200);
 const result=await call(env,'/api/v1/stock/000001?period=1d');
 assert.deepEqual(result.body.data.series,[{trade_date:'2026-10-29',flow:1.5,intensity_bp:null},{trade_date:'2026-10-30',flow:-5,intensity_bp:2.3}]);
 assert.equal('series_compact' in result.body.data,false);
 for(const rows of [[['2026-10-30',1,null],['2026-10-30',2,null]],[['2026-02-30',1,null]],[['2026-10-30','1',null]]]){
  const bad=structuredClone(values['stocks-0000']);bad['000001'].base.series_compact=rows;
  assert.equal((await call(env,'/internal/snapshots/v2/stocks-0000.json',auth('PUT',bad))).response.status,400);
 }
});

async function setupLong(status='verified') {
 const values=structuredClone(dataset);
 const w={window_start:'2021-10-30',window_end:'2026-10-30',start_exclusive:true,window_days:status==='verified'?1214:null,calendar_status:status};
 values.meta.stock_periods=['1d','5d','20d','3m','6m','1y','2y','3y','5y'];
 values.meta.stock_windows={'5y':w};
 values['stocks-0000']['000001'].base.series=[{trade_date:'2021-10-29',flow:5},{trade_date:'2021-10-30',flow:10},{trade_date:'2021-11-01',flow:null},{trade_date:'2026-10-30',flow:5},{trade_date:'2026-11-02',flow:7}];
 values['stocks-0000']['000001'].periods['5y']={movement:{...w,period:'5y',valid_days:1,missing_days:status==='verified'?1213:null,available_start:'2026-10-30',available_end:'2026-10-30',complete:false,net_flow:null,inflow:null,outflow:null,observed_net_flow:5},etf_contributions:[],coverage:{}};
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 const newHashes={};
 for(const key of files){
  newHashes[key]=createHash('sha256').update(JSON.stringify(values[key])).digest('hex');
  assert.equal((await call(env,`/internal/snapshots/v1/${key}.json`,auth('PUT',values[key]))).response.status,200);
 }
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:newHashes,trade_date:'2026-10-30'}))).response.status,200);
 return {env,values};
}

test('long windows preserve incomplete flow and crop series by natural dates',async()=>{
 const {env}=await setupLong();
 let r=await call(env,'/api/v1/stock/000001?period=5y');
 assert.equal(r.response.status,200);
 assert.equal(r.body.data.movement.net_flow,null);
 assert.equal(r.body.data.movement.observed_net_flow,5);
 assert.equal(r.body.data.movement.complete,false);
 assert.equal(r.body.data.movement.window_days,1214);
 assert.deepEqual(r.body.data.series,[{trade_date:'2021-11-01',flow:null},{trade_date:'2026-10-30',flow:5}]);
 r=await call(env,'/api/v1/stock/000002?period=5y');
 assert.equal(r.response.status,200);
 assert.equal(r.body.data.movement.window_days,1214);
 assert.equal(r.body.data.movement.valid_days,0);
 assert.equal(r.body.data.movement.missing_days,1214);
 assert.equal(r.body.data.movement.net_flow,null);
});

test('unverified calendar reports unknown expected trading days',async()=>{
 const {env}=await setupLong('observed');
 const r=await call(env,'/api/v1/stock/000002?period=5y');
 assert.equal(r.response.status,200);
 assert.equal(r.body.data.movement.window_days,null);
 assert.equal(r.body.data.movement.missing_days,null);
 assert.equal(r.body.data.movement.complete,false);
});

test('reject malformed long movement and windows during upload',async()=>{
 const {values}=await setupLong();
 const bad=structuredClone(values['stocks-0000']);
 bad['000001'].periods['5y'].movement.net_flow=1;
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 assert.equal((await call(env,'/internal/snapshots/v2/stocks-0000.json',auth('PUT',bad))).response.status,400);
 bad['000001'].periods['5y'].movement.net_flow=null;
 bad['000001'].periods['5y'].movement.window_start='2021-02-30';
 assert.equal((await call(env,'/internal/snapshots/v2/stocks-0000.json',auth('PUT',bad))).response.status,400);
 for(const delta of [{missing_days:0},{window_start:'2026-10-25'},{available_start:'2021-10-30'},{available_start:'2026-10-31'},{available_end:'2026-10-29'},{valid_days:0,available_start:null,available_end:null,observed_net_flow:0},{complete:true,valid_days:1214,missing_days:0,net_flow:null}]) {
   const invalid=structuredClone(values['stocks-0000']); Object.assign(invalid['000001'].periods['5y'].movement,delta);
   assert.equal((await call(env,'/internal/snapshots/v2/stocks-0000.json',auth('PUT',invalid))).response.status,400);
 }
});

test('natural-year anchors handle leap days',async()=>{
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 const meta={version:'leap',trade_date:'2024-02-29',generated_at:'2024-03-01T00:00:00Z',stock_windows:{'1y':{window_start:'2023-02-28',window_end:'2024-02-29',start_exclusive:true,window_days:243,calendar_status:'verified'}}};
 assert.equal((await call(env,'/internal/snapshots/leap/meta.json',auth('PUT',meta))).response.status,200);
 meta.stock_windows['1y'].window_start='2023-03-01';
 assert.equal((await call(env,'/internal/snapshots/leap/meta.json',auth('PUT',meta))).response.status,400);
});

test('all supported long periods use their own natural window for uncovered stocks',async()=>{
 const values=structuredClone(dataset), starts={'3m':'2026-07-30','6m':'2026-04-30','1y':'2025-10-30','2y':'2024-10-30','3y':'2023-10-30','5y':'2021-10-30'};
 values.meta.stock_windows=Object.fromEntries(Object.entries(starts).map(([p,start])=>[p,{window_start:start,window_end:'2026-10-30',start_exclusive:true,window_days:null,calendar_status:'observed'}]));
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'}, newHashes={};
 for(const key of files){newHashes[key]=createHash('sha256').update(JSON.stringify(values[key])).digest('hex');assert.equal((await call(env,`/internal/snapshots/v1/${key}.json`,auth('PUT',values[key]))).response.status,200);}
 assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:newHashes,trade_date:'2026-10-30'}))).response.status,200);
 for(const [p,start] of Object.entries(starts)){
  const r=await call(env,`/api/v1/stock/000002?period=${p}`);
  assert.equal(r.response.status,200);assert.equal(r.body.data.movement.window_start,start);assert.equal(r.body.data.movement.window_days,null);assert.equal(r.body.data.movement.net_flow,null);
 }
});

test('observed calendar accepts short fixed windows and unknown long expected days',async()=>{
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'}, meta=structuredClone(dataset.meta);
 const short={'1d':1,'5d':5,'20d':20};
 meta.stock_windows=Object.fromEntries(Object.entries(short).map(([p,n])=>[p,{window_start:'2026-10-29',window_end:'2026-10-30',start_exclusive:false,window_days:n,calendar_status:'observed'}]));
 Object.assign(meta.stock_windows,{'5y':{window_start:'2021-10-30',window_end:'2026-10-30',start_exclusive:true,window_days:null,calendar_status:'observed'}});
 assert.equal((await call(env,'/internal/snapshots/v1/meta.json',auth('PUT',meta))).response.status,200);
 meta.stock_windows['5d'].window_days=3;
 assert.equal((await call(env,'/internal/snapshots/v1/meta.json',auth('PUT',meta))).response.status,400);
});

test('streamed commit rejects missing stock and ETF details without publishing',async()=>{
 for (const missing of ['stock','etf']) {
  const values=structuredClone(dataset);
  if (missing==='stock') values['stocks-0000']={};
  else values['etf-details']={};
  const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'}, sums={};
  for (const key of files) {
   sums[key]=createHash('sha256').update(JSON.stringify(values[key])).digest('hex');
   assert.equal((await call(env,`/internal/snapshots/v1/${key}.json`,auth('PUT',values[key]))).response.status,200);
  }
  assert.equal((await call(env,'/internal/commit',auth('POST',{version:'v1',files,hashes:sums,trade_date:'2026-10-30'}))).response.status,409);
  assert.equal(await env.BUCKET.get('latest.json'),null);
 }
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 const wrong={'100001':structuredClone(dataset['stocks-0000']['000001'])};
 assert.equal((await call(env,'/internal/snapshots/v1/stocks-0000.json',auth('PUT',wrong))).response.status,400);
});
