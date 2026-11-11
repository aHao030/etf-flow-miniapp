import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
globalThis.ASSETS={'/index.html':{body:'ok',contentType:'text/html'}};
const src=await readFile('web-trial/worker/index.js','utf8');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'))).default;
const dir=resolve(process.argv[2]);const m=JSON.parse(await readFile(resolve(dir,'manifest.json'),'utf8'));
const store=new Map(); const env={UPDATE_SECRET:'secret',BUCKET:{async put(k,v,o={}){store.set(k,{body:typeof v==='string'?v:Buffer.from(v).toString('utf8'),meta:o.customMetadata});},async get(k){const x=store.get(k);return x?{text:async()=>x.body}:null;},async head(k){const x=store.get(k);return x?{customMetadata:x.meta,size:Buffer.byteLength(x.body)}:null;}}};
for(const key of m.files){const body=await readFile(resolve(dir,key+'.json'));const r=await worker.fetch(new Request(`https://site/internal/snapshots/${m.version}/${key}.json`,{method:'PUT',headers:{authorization:'Bearer secret','content-type':'application/json'},body}),env);if(r.status!==200)throw Error(key+' '+r.status+' '+await r.text());}
let r=await worker.fetch(new Request('https://site/internal/commit',{method:'POST',headers:{authorization:'Bearer secret','content-type':'application/json'},body:JSON.stringify(m)}),env);if(r.status!==200)throw Error('commit '+r.status+' '+await r.text());
for(const path of ['/api/v1/meta','/api/v1/stocks?q=600519',...['1d','5d','20d','3m','6m','1y','2y','3y','5y'].map(p=>'/api/v1/stock/600519?period='+p),'/api/v1/rank?period=5d&direction=out','/api/v1/etfs']){const x=await worker.fetch(new Request('https://site'+path),env);if(x.status!==200)throw Error(path+' '+x.status+' '+await x.text());}
console.log('Snapshot Worker round-trip passed: '+m.files.length+' files; representative APIs OK');
