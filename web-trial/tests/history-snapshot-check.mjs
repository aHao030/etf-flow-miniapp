import {readFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const directory=process.argv[2];
if(!directory)throw Error('Pass an exported history snapshot directory');
const source=await readFile(new URL('../dist/server/index.js',import.meta.url),'utf8');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))).default;
const manifest=JSON.parse(await readFile(path.join(directory,'manifest.json'),'utf8'));
const values=new Map();
const env={UPDATE_SECRET:'test-only',BUCKET:{
 async get(k){const x=values.get(k);return x?{text:async()=>x.body}:null;},
 async head(k){const x=values.get(k);return x?{size:Buffer.byteLength(x.body),customMetadata:x.metadata}:null;},
 async put(k,v,o={}){values.set(k,{body:typeof v==='string'?v:Buffer.from(v).toString('utf8'),metadata:o.customMetadata});}
}};
const call=(url,method='GET',body)=>worker.fetch(new Request('https://local.example'+url,{method,headers:{'content-type':'application/json',authorization:'Bearer test-only'},...(body?{body}:{} )}),env);
for(const key of manifest.files) {
 const body=await readFile(path.join(directory,key+'.json'),'utf8');
 const res=await call('/internal/history/'+manifest.version+'/'+key+'.json','PUT',body);
 assert.equal(res.status,200,key);
 assert.equal((await res.json()).data.sha256,manifest.hashes[key]);
}
let res=await call('/internal/history/commit','POST',JSON.stringify(manifest));
assert.equal(res.status,200,await res.text());
res=await call('/api/v1/history/meta');const meta=(await res.json()).data;
res=await call('/api/v1/history/etfs?page_size=100');assert.equal((await res.json()).data.total,meta.total_funds);
for(const code of ['510300','159915','511230']) {
 res=await call('/api/v1/history/etf/'+code+'?start=2021-01-01&end=2021-01-15');
 assert.equal(res.status,200);const data=(await res.json()).data;
 assert(data.items.every(r=>r.trade_date>='2021-01-01'&&r.trade_date<='2021-01-15'));
 if(code==='511230')assert(data.items.every(r=>r.estimated_flow===null));
}
console.log(JSON.stringify({real_archive_checks:'passed',files:manifest.files.length,funds:meta.total_funds,shares:meta.shares_rows,nav:meta.nav_rows}));
