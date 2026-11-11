import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import test from 'node:test';
import assert from 'node:assert/strict';
const src=(await Promise.all(['history','broad','index'].map(x=>readFile(new URL('../worker/'+x+'.js',import.meta.url),'utf8')))).join('\n');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'))).default;
class Bucket {objects=new Map();async get(k){const v=this.objects.get(k);return v?{text:async()=>v.body}:null;}async head(k){const v=this.objects.get(k);return v?{size:Buffer.byteLength(v.body),customMetadata:v.metadata}:null;}async put(k,v,o={}){this.objects.set(k,{body:typeof v==='string'?v:Buffer.from(v).toString(),metadata:o.customMetadata});}}
async function call(env,path,method='GET',body=null,token='secret'){const r=await worker.fetch(new Request('https://example.test'+path,{method,headers:{'content-type':'application/json',authorization:'Bearer '+token},...(body===null?{}:{body:JSON.stringify(body)})}),env);return{status:r.status,body:await r.json()};}
const member={fund_code:'510300',index_code:'000300',fund_name:'沪深300ETF',estimated_flow:4,shares:100,unit_nav:1};
const rows=[['2026-11-06',null,null,0,1,0,4000],['2026-11-09',4,4,1,1,0,4010]];
const data={meta:{version:'bv1',generated_at:'2026-11-10T00:00:00Z',target_start:'2026-11-06',target_end:'2026-11-09',indexes:[{code:'000300',name:'沪深300',fund_count:1}],total_funds:1,mapping_complete:false},calendar:{dates:rows.map(r=>r[0])},'index-000300':{index:{code:'000300'},members:[member],rows},'index-all':{index:{code:'all'},members:[member],rows}};
const manifest={...Object.fromEntries(['version','generated_at','target_start','target_end'].map(k=>[k,data.meta[k]])),files:Object.keys(data),hashes:Object.fromEntries(Object.entries(data).map(([k,v])=>[k,createHash('sha256').update(JSON.stringify(v)).digest('hex')]))};
test('broad snapshots require auth, matching calendar and atomic verified commit',async()=>{
const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
assert.equal((await call(env,'/api/v1/broad/meta')).status,503);
assert.equal((await call(env,'/internal/broad/bv1/meta.json','PUT',data.meta,'bad')).status,401);
assert.equal((await call(env,'/internal/broad/commit','POST',manifest)).status,409);
for(const [k,v] of Object.entries(data))assert.equal((await call(env,'/internal/broad/bv1/'+k+'.json','PUT',v)).status,200);
assert.equal((await call(env,'/internal/broad/commit','POST',manifest)).status,200);
assert.equal((await call(env,'/internal/broad/bv1/meta.json','PUT',data.meta)).status,409);
const c=await call(env,'/api/v1/broad/calendar?start=1990-01-01&end=2026-11-10');assert.deepEqual(c.body.data.dates,['2026-11-06','2026-11-09']);
const d=(await call(env,'/api/v1/broad/index/000300')).body.data;assert.equal(d.series[0].observed_flow,null);assert.equal(d.series[1].estimated_flow,4);assert.equal(d.series[1].index_close,4010);
assert.equal((await call(env,'/api/v1/broad/index/000300?start=bad')).status,400);
assert.equal((await call(env,'/api/v1/broad/index/123456')).status,404);
});
test('partial counts cannot claim complete totals and duplicate membership is rejected',async()=>{
const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
for(const bad of [[...rows[1].slice(0,4),2,0,4010],[rows[1][0],0,0,0,0,0,4010]])assert.equal((await call(env,'/internal/broad/bv1/index-all.json','PUT',{...data['index-all'],rows:[bad]})).status,400);
assert.equal((await call(env,'/internal/broad/bv1/index-all.json','PUT',{...data['index-all'],members:[member,member]})).status,400);
assert.equal((await call(env,'/internal/broad/bv1/meta.json','PUT',{...data.meta,pad:'x'.repeat(2*1024*1024)})).status,400);
});
test('off-exchange reports are paginated by disclosure date without mixing with daily amounts',async()=>{
const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
const reports=Array.from({length:25},(_,i)=>({fund_code:String(i).padStart(6,'0'),period_start:null,period_end:'2026-09-30',subscription_shares:100,redemption_shares:40,net_subscription_shares:60,end_shares:1000}));
const off={members:reports.map(r=>({fund_code:r.fund_code})),files:['off-all-0000'],file_ranges:{'off-all-0000':{start:'2026-09-30',end:'2026-09-30',count:25}},report_count:25,combined_total:null,combined_reason:'frequency_or_double_count'};
await env.BUCKET.put('broad-latest.json',JSON.stringify({...manifest,target_start:'2026-09-01',files:[...manifest.files,'off-all-0000']}));
await env.BUCKET.put('broad/bv1/meta.json',JSON.stringify(data.meta));await env.BUCKET.put('broad/bv1/calendar.json',JSON.stringify(data.calendar));
await env.BUCKET.put('broad/bv1/index-all.json',JSON.stringify({...data['index-all'],off_exchange:off}));await env.BUCKET.put('broad/bv1/off-all-0000.json',JSON.stringify({reports}));
const page=(await call(env,'/api/v1/broad/off/all?start=2026-09-01&end=2026-11-09&page=2&page_size=20')).body.data;
assert.equal(page.total,25);assert.equal(page.reports.length,5);assert.equal(page.has_more,false);assert.equal(page.reports[0].fund_code,'000020');assert.equal(page.combined_total,null);
const empty=(await call(env,'/api/v1/broad/off/all?start=2026-10-01')).body.data;assert.equal(empty.total,0);assert.deepEqual(empty.reports,[]);
assert.equal((await call(env,'/api/v1/broad/off/all?page_size=1000')).status,400);
});
test('member amounts follow selected dates, reuse each history shard, and preserve unknown lifecycle gaps',async()=>{
 const bucket=new Bucket(),env={BUCKET:bucket},reads=new Map(),original=bucket.get.bind(bucket);bucket.get=async k=>{reads.set(k,(reads.get(k)||0)+1);return original(k);};
 const members=[{...member,listed_date:'2012-05-28',estimated_flow:9999,flow_date:'2026-11-09'},{...member,fund_code:'159919',listed_date:null,estimated_flow:9998},{...member,fund_code:'510500',listed_date:'2026-11-06',estimated_flow:9997}];
 const dates=['2026-10-09','2026-11-06','2026-11-09'],version='hv1',meta={...data.meta,history_version:version,target_start:'2021-01-01'};
 await bucket.put('broad-latest.json',JSON.stringify({...manifest,target_start:'2021-01-01'}));await bucket.put('broad/bv1/meta.json',JSON.stringify(meta));await bucket.put('broad/bv1/calendar.json',JSON.stringify({dates}));await bucket.put('broad/bv1/index-all.json',JSON.stringify({...data['index-all'],members,rows:dates.map(d=>[d,1,null,1,3,1,4000])}));
 await bucket.put('history/hv1/meta.json',JSON.stringify({version,generated_at:'2026-11-10T00:00:00Z',target_start:'2021-01-01',target_end:'2026-11-09',complete:false,total_funds:3}));
 await bucket.put('history/hv1/catalog.json',JSON.stringify({items:members.map((m,i)=>({fund_code:m.fund_code,fund_name:'ETF',market:i===1?'SZ':'SH',file:i<2?'data-0000':'data-0001'}))}));
 const row=(d,amount)=>[d,100,1,1,amount,amount===null?'missing_current_shares':'estimated'];
 await bucket.put('history/hv1/data-0000.json',JSON.stringify({funds:{'510300':dates.map((d,i)=>row(d,(i+1)*10)),'159919':dates.map((d,i)=>row(d,i+1))}}));
 await bucket.put('history/hv1/data-0001.json',JSON.stringify({funds:{'510500':[row(dates[0],900),row(dates[1],4),row(dates[2],null)]}}));
 const full=(await call(env,'/api/v1/broad/index/all?start=2026-10-09&end=2026-11-09')).body.data;
 assert.equal(full.members_period_status,'computed');assert.equal(reads.get('history/hv1/data-0000.json'),1);assert.equal(reads.get('history/hv1/data-0001.json'),1);
 assert.deepEqual(full.members.map(m=>m.period_observed_flow),[60,6,4]);assert.deepEqual(full.members.map(m=>m.period_expected_days),[3,null,2]);assert.deepEqual(full.members.map(m=>m.period_complete_flow),[60,null,null]);assert.deepEqual(full.members.map(m=>m.end_date_flow),[30,3,null]);assert.equal(full.members[0].estimated_flow,9999);
 const month=(await call(env,'/api/v1/broad/index/all?start=2026-11-01&end=2026-11-09')).body.data;assert.deepEqual(month.members.map(m=>m.period_observed_flow),[50,5,4]);
 const weekend=(await call(env,'/api/v1/broad/index/all?start=2026-11-06&end=2026-11-08')).body.data;assert.equal(weekend.members[0].period_start,'2026-11-06');assert.equal(weekend.members[0].period_end,'2026-11-08');assert.equal(weekend.members[0].end_date,'2026-11-06');assert.equal(weekend.members[0].end_date_flow,20);assert.equal(weekend.members[2].period_complete_flow,4);
 const readBefore=reads.get('history/hv1/data-0000.json');await call(env,'/api/v1/broad/index/all?start=2026-11-01&end=2026-11-09');assert.equal(reads.get('history/hv1/data-0000.json'),readBefore,'revisiting a period reuses only small immutable aggregates');
 await bucket.put('broad/bv1/meta.json',JSON.stringify({...meta,history_version:'hv2'}));await bucket.put('history/hv2/meta.json',JSON.stringify({version:'wrong'}));const unavailable=(await call(env,'/api/v1/broad/index/all?start=2026-11-01&end=2026-11-09')).body.data;assert.equal(unavailable.members_period_status,'unavailable');assert(unavailable.members.every(m=>m.period_observed_flow===null));assert(unavailable.series.length>0,'missing linked history must not hide the index chart');
});
