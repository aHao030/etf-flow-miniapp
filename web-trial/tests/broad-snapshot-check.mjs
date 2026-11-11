// Exercise the actual immutable broad snapshot through the production Worker.
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const directory=path.resolve(process.argv[2]);
const manifest=JSON.parse(await readFile(path.join(directory,'manifest.json'),'utf8'));
const src=(await Promise.all(['history','broad','index'].map(x=>readFile(new URL('../worker/'+x+'.js',import.meta.url),'utf8')))).join('\n');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'))).default;
const objects=new Map();
const env={UPDATE_SECRET:'test-only',BUCKET:{async get(k){const v=objects.get(k);return v?{text:async()=>v.body}:null;},async head(k){const v=objects.get(k);return v?{size:Buffer.byteLength(v.body),customMetadata:v.metadata}:null;},async put(k,v,o={}){objects.set(k,{body:typeof v==='string'?v:Buffer.from(v).toString(),metadata:o.customMetadata});}}};
async function call(p,method='GET',body=null){const r=await worker.fetch(new Request('https://test.local'+p,{method,headers:{authorization:'Bearer test-only','content-type':'application/json'},...(body?{body}: {})}),env);const value=await r.json();assert.equal(r.status,200,JSON.stringify(value));return value.data;}
for(const key of manifest.files){const raw=await readFile(path.join(directory,key+'.json'));assert.equal(createHash('sha256').update(raw).digest('hex'),manifest.hashes[key]);await call('/internal/broad/'+manifest.version+'/'+key+'.json','PUT',raw);}
await call('/internal/broad/commit','POST',JSON.stringify(manifest));
const meta=await call('/api/v1/broad/meta');
for(const code of ['all',...meta.indexes.map(x=>x.code)]){const data=await call('/api/v1/broad/index/'+code);assert.equal(data.version,manifest.version);assert.equal(data.series.length,data.trading_dates.length);assert(data.series.every((r,i)=>r.trade_date===data.trading_dates[i]));assert(data.series.every(r=>r.estimated_flow===null||r.expected_funds>0&&r.valid_funds===r.expected_funds&&r.unknown_lifecycle_funds===0));}
console.log(JSON.stringify({verified:true,version:manifest.version,files:manifest.files.length,funds:meta.total_funds,indexes:meta.indexes.length,start:meta.target_start,end:meta.target_end}));
