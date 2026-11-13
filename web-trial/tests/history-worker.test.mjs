import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const src=(await readFile(new URL('../worker/history.js',import.meta.url),'utf8'))+'\n'+(await readFile(new URL('../worker/index.js',import.meta.url),'utf8'));
const worker=(await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'))).default;
class Bucket {
  objects=new Map();
  async get(k){const v=this.objects.get(k);return v?{text:async()=>v.body}:null;}
  async head(k){const v=this.objects.get(k);return v?{size:Buffer.byteLength(v.body),customMetadata:v.metadata}:null;}
  async put(k,v,o={}){this.objects.set(k,{body:typeof v==='string'?v:Buffer.from(v).toString('utf8'),metadata:o.customMetadata});}
}
async function call(env,path,method='GET',body=null,token='secret') {
  const res=await worker.fetch(new Request('https://trial.example'+path,{method,headers:{'content-type':'application/json',authorization:'Bearer '+token},...(body===null?{}:{body:JSON.stringify(body)})}),env);
  return {status:res.status,body:await res.json()};
}
const data={
 meta:{version:'hv1',target_start:'2021-01-01',target_end:'2026-10-30',generated_at:'2026-10-31T00:00:00Z',complete:false,total_funds:2,notes:['测试']},
 catalog:{items:[{fund_code:'510300',fund_name:'沪深300',market:'SH',file:'data-0000'},{fund_code:'159915',fund_name:'创业板',market:'SZ',file:'data-0000'}]},
 'data-0000':{funds:{'510300':[['2021-01-04',100,1,1,null,'missing_previous_shares'],['2021-01-05',110,1.01,1.01,10,'estimated'],['2021-01-06',null,1.02,1.02,null,'missing_previous_shares']],'159915':[]}}
};
const manifest={version:'hv1',files:Object.keys(data),hashes:Object.fromEntries(Object.entries(data).map(([k,v])=>[k,createHash('sha256').update(JSON.stringify(v)).digest('hex')])),target_start:'2021-01-01',target_end:'2026-10-30',generated_at:'2026-10-31T00:00:00Z'};
test('archive auth, atomic commit, membership and hashes',async()=>{
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 assert.equal((await call(env,'/api/v1/history/meta')).status,503);
 assert.equal((await call(env,'/internal/history/hv1/meta.json','PUT',data.meta,'bad')).status,401);
 assert.equal((await call(env,'/internal/history/commit','POST',manifest)).status,409);
 for(const [k,v] of Object.entries(data))assert.equal((await call(env,'/internal/history/hv1/'+k+'.json','PUT',v)).status,200);
 assert.equal((await call(env,'/internal/history/commit','POST',{...manifest,hashes:{...manifest.hashes,meta:'0'.repeat(64)}})).status,409);
 assert.equal((await call(env,'/internal/history/commit','POST',manifest)).status,200);
 assert.equal((await call(env,'/internal/history/hv1/meta.json','PUT',data.meta)).status,409);
 assert.equal((await call(env,'/api/v1/history/meta')).body.data.total_funds,2);
 assert.equal((await call(env,'/api/v1/history/etfs?market=SZ')).body.data.items[0].fund_code,'159915');
 assert.equal((await call(env,'/api/v1/history/etfs?q=%3Cimg%3E')).body.data.total,0);
 const result=await call(env,'/api/v1/history/etf/510300?start=2021-01-05&end=2021-01-06&page_size=1');
 assert.equal(result.status,200);assert.equal(result.body.data.total,2);assert.equal(result.body.data.items[0].estimated_flow,10);assert.equal(result.body.data.coverage.shares.count,1);
 const second=await call(env,'/api/v1/history/etf/510300?start=2021-01-05&end=2021-01-06&page_size=1&page=2');
 assert.equal(second.body.data.items[0].shares,null);assert.equal(second.body.data.items[0].estimated_flow,null);
 for(const query of ['start=2020-12-31','end=2027-01-01','start=2021-02-29','start=2022-01-01&end=2021-01-01','page_size=101'])assert.equal((await call(env,'/api/v1/history/etf/510300?'+query)).status,400);
 assert.equal((await call(env,'/api/v1/history/etf/000000')).status,404);
});
test('reject invalid dates, duplicate dates, nonfinite-like values, oversized archive and unreferenced membership',async()=>{
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 const invalid=[['2021-02-29',1,1,1,null,'raw_observation'],['2021-01-04',-1,1,1,null,'raw_observation'],['2021-01-04',1,1,1,0,'missing_previous_shares']];
 for(const row of invalid)assert.equal((await call(env,'/internal/history/hv1/data-0000.json','PUT',{funds:{'510300':[row]}})).status,400);
 const row=['2021-01-04',1,1,1,null,'raw_observation'];
 assert.equal((await call(env,'/internal/history/hv1/data-0000.json','PUT',{funds:{'510300':[row,row]}})).status,400);
 assert.equal((await call(env,'/internal/history/hv1/meta.json','PUT',{...data.meta,padding:'x'.repeat(2*1024*1024)})).status,400);
 for(const [k,v] of Object.entries(data))await call(env,'/internal/history/hv1/'+k+'.json','PUT',v);
 const badCatalog={items:data.catalog.items.slice(0,1)};
 const badMeta={...data.meta,total_funds:1};
 await call(env,'/internal/history/hv1/catalog.json','PUT',badCatalog);await call(env,'/internal/history/hv1/meta.json','PUT',badMeta);
 const hashes={...manifest.hashes,catalog:createHash('sha256').update(JSON.stringify(badCatalog)).digest('hex'),meta:createHash('sha256').update(JSON.stringify(badMeta)).digest('hex')};
 assert.equal((await call(env,'/internal/history/commit','POST',{...manifest,hashes})).status,409);
 assert.equal(await env.BUCKET.get('history-latest.json'),null);
});
test('full-range chart is not paginated and preserves missing values and version',async()=>{
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 const rows=Array.from({length:140},(_,i)=>[new Date(Date.UTC(2021,0,4+i)).toISOString().slice(0,10),100+i,i===71?null:1,1,i===71?null:0,i===71?'missing_current_nav':'estimated']);
 await env.BUCKET.put('history-latest.json',JSON.stringify(manifest));
 await env.BUCKET.put('history/hv1/catalog.json',JSON.stringify(data.catalog));
 await env.BUCKET.put('history/hv1/meta.json',JSON.stringify(data.meta));
 await env.BUCKET.put('history/hv1/data-0000.json',JSON.stringify({funds:{'510300':rows}}));
 const chart=await call(env,'/api/v1/history/etf/510300/chart?page=2&page_size=1');
 assert.equal(chart.status,200);assert.equal(chart.body.data.version,'hv1');assert.equal(chart.body.data.series.length,140);
 assert.equal(chart.body.data.series[71].unit_nav,null);assert.equal(chart.body.data.series[71].estimated_flow,null);assert.equal(chart.body.data.series[72].estimated_flow,0);
 const page=await call(env,'/api/v1/history/etf/510300?page_size=100');
 assert.equal(page.body.data.items.length,100);assert.equal(page.body.data.version,chart.body.data.version);
 const clipped=await call(env,'/api/v1/history/etf/510300/chart?start=2021-01-05&end=2021-01-06');
 assert.deepEqual(clipped.body.data.series.map(x=>x.trade_date),['2021-01-05','2021-01-06']);
 for(const path of ['/api/v1/history/etf/bad/chart','/api/v1/history/etf/510300/chart?end=2028-01-01']) assert.equal((await call(env,path)).status,400);
 assert.equal((await call(env,'/api/v1/history/etf/510300/chart','POST',{})).status,405);
 await env.BUCKET.put('history/hv1/data-0000.json',JSON.stringify({funds:{'510300':Array(8001).fill(rows[0])}}));
 assert.equal((await call(env,'/api/v1/history/etf/510300/chart')).status,503);
});
test('bounded watchlist distinguishes current from latest disclosure and missing from zero',async()=>{
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 await env.BUCKET.put('history-latest.json',JSON.stringify({...manifest,target_end:'2021-01-06'}));
 await env.BUCKET.put('history/hv1/catalog.json',JSON.stringify(data.catalog));
 await env.BUCKET.put('history/hv1/data-0000.json',JSON.stringify({funds:{
   '510300':[['2021-01-04',100,1,1,0,'estimated'],['2021-01-05',100,1,1,0,'estimated']],
   '159915':[['2021-01-05',100,1,1,0,'estimated'],['2021-01-06',100,1,1,0,'estimated']]
 }}));
 const result=await call(env,'/api/v1/history/watchlist?codes=510300,159915,510300,000000');
 assert.equal(result.status,200);const [old,current,unknown]=result.body.data.items;
 assert.equal(old.shares,null);assert.equal(old.latest_shares_date,'2021-01-05');assert.equal(old.latest_shares,100);assert.equal(old.estimated_flow,null);assert.equal(old.status,'pending');
 assert.equal(current.shares_date,'2021-01-06');assert.equal(current.estimated_flow,0);assert.equal(current.share_change,0);assert.equal(current.previous_observed_shares_date,'2021-01-05');
 assert.equal(unknown.status,'not_found');
 for(const query of ['', 'codes=bad', 'codes='+Array(31).fill('510300').join(',')]) assert.equal((await call(env,'/api/v1/history/watchlist?'+query)).status,400);
});

test('history metadata reports actual coverage dates without changing the query target',async()=>{
 const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
 const meta={...data.meta,version:'dates-v1',target_end:'2026-11-13',generated_at:'2026-11-13T08:00:00Z',total_funds:3};
 const catalog={items:[
  {...data.catalog.items[0],shares:{count:10,end:'2026-11-12'},nav:{count:10,end:'2026-11-12'},flow:{count:9,end:'2026-11-11'}},
  {...data.catalog.items[1],shares:{count:10,end:'2026-11-12'},nav:{count:9,end:'2026-11-11'},flow:{count:9,end:'2026-11-11'}},
  {fund_code:'511230',fund_name:'债券ETF',market:'SH',file:'data-0000',shares:{count:9,end:'2026-11-11'},nav:{count:4,end:'2026-11-12'}}
 ]};
 await env.BUCKET.put('history-latest.json',JSON.stringify({...manifest,version:meta.version,target_end:meta.target_end}));
 await env.BUCKET.put('history/'+meta.version+'/meta.json',JSON.stringify(meta));
 await env.BUCKET.put('history/'+meta.version+'/catalog.json',JSON.stringify(catalog));
 const reads=[];
 const get=env.BUCKET.get.bind(env.BUCKET);
 env.BUCKET.get=key=>{reads.push(key);return get(key);};
 const result=await call(env,'/api/v1/history/meta');
 assert.equal(result.status,200);
 assert.deepEqual(result.body.data,{
  ...meta,
  data_dates:{shares:{date:'2026-11-12',count:2},nav:{date:'2026-11-12',count:2},flow:{date:'2026-11-11',count:2}}
 });
 assert.deepEqual(reads,['history-latest.json','history/dates-v1/meta.json','history/dates-v1/catalog.json']);
});

test('history metadata keeps unavailable or invalid coverage dates unknown',async()=>{
 const unknown={shares:{date:null,count:0},nav:{date:null,count:0},flow:{date:null,count:0}};
 const meta={...data.meta,target_end:'2026-11-13',generated_at:'2026-11-13T08:00:00Z'};
 const cases=[
  {name:'legacy catalog without coverage',catalog:data.catalog},
  {name:'legacy catalog without flow coverage',catalog:{items:[
   {...data.catalog.items[0],shares:{count:2,end:'2026-11-12'},nav:{count:1,end:'2026-11-11'}},
   {...data.catalog.items[1],shares:{count:2,end:'2026-11-12'},nav:null,flow:null}
  ]},expected:{shares:{date:'2026-11-12',count:2},nav:{date:'2026-11-11',count:1},flow:{date:null,count:0}}},
  {name:'empty catalog',catalog:{items:[]}},
  {name:'missing catalog',catalog:null},
  {name:'invalid catalog',catalog:{items:[{fund_code:'bad'}]}},
  {name:'duplicate fund',catalog:{items:[data.catalog.items[0],data.catalog.items[0]]}},
  {name:'invalid dates and empty counts',catalog:{items:[
   {...data.catalog.items[0],shares:{count:1,end:'2026-11-14'},nav:{count:1,end:'2026-02-31'},flow:{count:0,end:'2026-11-12'}},
   {...data.catalog.items[1],shares:{count:1,end:null},nav:{count:1,end:'2020-12-31'},flow:{count:1,end:''}}
  ]}},
  {name:'catalog read failure',throws:true}
 ];
 for(const sample of cases){
  const env={BUCKET:new Bucket(),UPDATE_SECRET:'secret'};
  await env.BUCKET.put('history-latest.json',JSON.stringify({...manifest,target_end:meta.target_end}));
  await env.BUCKET.put('history/hv1/meta.json',JSON.stringify(meta));
  if(sample.catalog)await env.BUCKET.put('history/hv1/catalog.json',JSON.stringify(sample.catalog));
  if(sample.throws){
   const get=env.BUCKET.get.bind(env.BUCKET);
   env.BUCKET.get=key=>{if(key==='history/hv1/catalog.json')throw Error('unavailable');return get(key);};
  }
  const result=await call(env,'/api/v1/history/meta');
  assert.equal(result.status,200,sample.name);
  assert.deepEqual(result.body.data,{...meta,data_dates:sample.expected||unknown},sample.name);
 }
});
