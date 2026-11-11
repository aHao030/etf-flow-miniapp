import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import test from 'node:test';
import assert from 'node:assert/strict';
const src=(await Promise.all(['history','broad','market','index'].map(x=>readFile(new URL('../worker/'+x+'.js',import.meta.url),'utf8')))).join('\n');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'))).default;
class Bucket {objects=new Map();async get(k){const v=this.objects.get(k);return v?{text:async()=>v.body}:null;}async head(k){const v=this.objects.get(k);return v?{size:Buffer.byteLength(v.body),customMetadata:v.metadata}:null;}async put(k,v,o={}){this.objects.set(k,{body:typeof v==='string'?v:Buffer.from(v).toString(),metadata:o.customMetadata});}}
async function call(env,p,method='GET',body=null,token='secret'){const r=await worker.fetch(new Request('https://example.test'+p,{method,headers:{'content-type':'application/json',authorization:'Bearer '+token},...(body===null?{}:{body:JSON.stringify(body)})}),env);return{status:r.status,body:await r.json()};}
const rows={meta:{version:'mv1',target_start:'2026-11-05',target_end:'2026-11-09',generated_at:'2026-11-10T00:00:00Z',total_funds:2},calendar:{dates:['2026-11-05','2026-11-06','2026-11-09']},catalog:{items:[{stock_code:'600519',stock_name:'测试股票',file:'weights-0000'}]},funds:{items:[{fund_code:'510300',fund_name:'甲',factor_file:'factors-0000',listed_date:'2026-11-06',report_date:'2026-09-30'},{fund_code:'510050',fund_name:'乙',factor_file:'factors-0000',listed_date:'2020-01-01',report_date:'2026-09-30'}]},'weights-0000':{'600519':[['510300',.2],['510050',.1]]},'factors-0000':{'510300':[['2026-11-06',100],['2026-11-09',200]],'510050':[['2026-11-05',-50],['2026-11-06',0]]},ranks:{'1d':[],'5d':[],'20d':[]},industries:{items:[]}};
const manifest={...rows.meta,files:Object.keys(rows),hashes:Object.fromEntries(Object.entries(rows).map(([k,v])=>[k,createHash('sha256').update(JSON.stringify(v)).digest('hex')]))};
test('factorized stock aggregation respects lifecycle, retains observed subtotal and does not claim completeness',async()=>{
const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
assert.equal((await call(env,'/internal/market/mv1/meta.json','PUT',rows.meta,'bad')).status,401);
assert.equal((await call(env,'/internal/market/commit','POST',manifest)).status,409);
for(const [k,v] of Object.entries(rows))assert.equal((await call(env,'/internal/market/mv1/'+k+'.json','PUT',v)).status,200,k);
assert.equal((await call(env,'/internal/market/commit','POST',manifest)).status,200);
const result=await call(env,'/api/v1/market/stock/600519?period=all');assert.equal(result.status,200);const d=result.body.data;
assert.deepEqual(d.series.map(r=>r.flow),[-5,20,40]);assert.deepEqual(d.series.map(r=>r.expected_etfs),[1,2,2]);assert.deepEqual(d.series.map(r=>r.valid_etfs),[1,2,1]);
assert.equal(d.movement.observed_net_flow,55);assert.equal(d.movement.net_flow,null);assert.equal(d.movement.complete,false);assert.equal(d.etf_contributions.length,2);
assert.equal((await call(env,'/api/v1/market/stock/600519?period=bad')).status,400);
assert.equal((await call(env,'/internal/market/mv1/meta.json','PUT',rows.meta)).status,409);
});
test('invalid weights and duplicated factor dates are rejected',async()=>{
const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
for(const weight of [-1,0,1.1])assert.equal((await call(env,'/internal/market/mv1/weights-0000.json','PUT',{'600519':[['510300',weight]]})).status,400);
assert.equal((await call(env,'/internal/market/mv1/factors-0000.json','PUT',{'510300':[['2026-11-06',0],['2026-11-06',10]]})).status,400);
for(const patch of [{stock_listed_date:'2026-02-30'},{stock_listed_date:false},{stock_listed_date:''},{listing_status:'inferred'}])assert.equal((await call(env,'/internal/market/mv1/catalog.json','PUT',{items:[{...rows.catalog.items[0],...patch}]})).status,400);
});
test('verified stock listing date removes pre-IPO scenario days',async()=>{
const env={BUCKET:new Bucket()};await env.BUCKET.put('market-latest.json',JSON.stringify(manifest));
for(const [k,v] of Object.entries(rows))await env.BUCKET.put('market/mv1/'+k+'.json',JSON.stringify(k==='catalog'?{items:[{...v.items[0],stock_listed_date:'2026-11-06',listing_status:'verified'}]}:v));
const d=(await call(env,'/api/v1/market/stock/600519?period=all')).body.data;
assert.deepEqual(d.series.map(r=>r.trade_date),['2026-11-06','2026-11-09']);assert.deepEqual(d.series.map(r=>r.flow),[20,40]);assert.equal(d.movement.observed_net_flow,60);assert.equal(d.movement.window_start,'2026-11-06');assert.equal(d.movement.requested_window_start,'2026-11-05');assert.match(d.history_note,/2026-11-06/);
});
