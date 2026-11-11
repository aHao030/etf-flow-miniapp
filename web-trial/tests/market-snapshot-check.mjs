import {readFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const directory=path.resolve(process.argv[2]),manifest=JSON.parse(await readFile(path.join(directory,'manifest.json'),'utf8'));
const src=(await Promise.all(['history','broad','market','index'].map(x=>readFile(new URL('../worker/'+x+'.js',import.meta.url),'utf8')))).join('\n');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'))).default,objects=new Map();
const env={UPDATE_SECRET:'test-only',BUCKET:{async get(k){const v=objects.get(k);return v?{text:async()=>v.body}:null;},async head(k){const v=objects.get(k);return v?{size:Buffer.byteLength(v.body),customMetadata:v.metadata}:null;},async put(k,v,o={}){objects.set(k,{body:typeof v==='string'?v:Buffer.from(v).toString(),metadata:o.customMetadata});}}};
async function call(p,method='GET',body=null){const r=await worker.fetch(new Request('https://test.local'+p,{method,headers:{authorization:'Bearer test-only','content-type':'application/json'},...(body?{body}: {})}),env);const value=await r.json();assert.equal(r.status,200,JSON.stringify(value));return value.data;}
for(const key of manifest.files){const raw=await readFile(path.join(directory,key+'.json'));assert.equal(createHash('sha256').update(raw).digest('hex'),manifest.hashes[key]);await call('/internal/market/'+manifest.version+'/'+key+'.json','PUT',raw);}
await call('/internal/market/commit','POST',JSON.stringify(manifest));
const meta=await call('/api/v1/market/meta'),stats=[];
for(const code of ['600519','300750','601318'])for(const period of ['1d','20d','5y','all']){const began=performance.now(),d=await call('/api/v1/market/stock/'+code+'?period='+period);assert.equal(d.movement.net_flow,null);assert.equal(d.movement.complete,false);const sum=d.series.filter(r=>r.flow!==null).reduce((a,r)=>a+r.flow,0);assert(d.movement.observed_net_flow===null||Math.abs(sum-d.movement.observed_net_flow)<.01);const breakdown=d.etf_contributions.filter(r=>r.flow!==null).reduce((a,r)=>a+r.flow,0);assert(d.movement.observed_net_flow===null||Math.abs(breakdown-sum)<.01);stats.push({code,period,days:d.series.length,related:d.etf_contributions.length,ms:Math.round(performance.now()-began)});}
console.log(JSON.stringify({verified:true,version:manifest.version,files:manifest.files.length,coverage:meta.coverage,stocks:meta.covered_stocks,checks:stats}));
