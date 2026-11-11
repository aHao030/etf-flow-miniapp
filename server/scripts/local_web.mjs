// Immutable fund snapshots; only public stock-price caches can be written. Never contacts the hosted Site or accepts uploads.
import http from 'node:http';
import {readFile,writeFile,mkdir,rename} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../web-trial');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(await readFile(path.join(root,'dist/server/index.js'))).toString('base64'))).default;
const versionPattern=/^\d{14}-[a-f0-9]{32}$/;
const appId='etf-flow-local';
const priceKey=/^prices\/(?:attempts\/)?v1\/\d{6}\/(?:199\d|20\d\d)\.json$/;
const priceReadKey=/^prices\/eastmoney\/v1\/\d{6}\.json$/;
const priceRoot=path.join(root,'.local/price-cache');
async function config(){
  const value=JSON.parse(await readFile(path.join(root,'.local/local-view.json'),'utf8'));
  if(!versionPattern.test(value.stock_version)||!versionPattern.test(value.history_version))throw Error('Invalid local snapshot selection');
  if(value.broad_version&&!versionPattern.test(value.broad_version))throw Error('Invalid broad snapshot selection');
  if(value.market_version&&!versionPattern.test(value.market_version))throw Error('Invalid market snapshot selection');
  return value;
}
async function archive(folder,version){
  const dir=path.join(root,'.local',folder,version);
  const manifest=JSON.parse(await readFile(path.join(dir,'manifest.json'),'utf8'));
  if(manifest.version!==version||!Array.isArray(manifest.files))throw Error('Invalid manifest');
  return {dir,manifest};
}
async function readChecked(a,name){
  if(!/^[a-z0-9-]+\.json$/.test(name)||!a.manifest.files.includes(name.slice(0,-5)))return null;
  const body=await readFile(path.join(a.dir,name),'utf8');
  if(createHash('sha256').update(body).digest('hex')!==a.manifest.hashes[name.slice(0,-5)])throw Error('Snapshot changed');
  return {text:async()=>body};
}
const server=http.createServer(async(req,res)=>{
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
  if(req.headers.host!=='127.0.0.1:4173'){res.writeHead(421);res.end();return;}
  if(!['GET','HEAD'].includes(req.method)||req.url.startsWith('/internal/')){res.writeHead(405);res.end();return;}
  try{
    const c=await config();
    if(req.url==='/local/status'){
      res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({app_id:appId,...c}));return;
    }
    if(req.url==='/local/price-coverage'){
      try{
        const raw=await readFile(path.join(priceRoot,'coverage.json'),'utf8');
        if(Buffer.byteLength(raw)>8*1024*1024)throw Error('Invalid coverage');
        const value=JSON.parse(raw);
        if(value.schema!==1||value.kind!=='listed-stock-price-coverage'||!value.summary)throw Error('Invalid coverage');
        const summary={};
        for(const key of ['stocks','expected_windows','completed_windows','stocks_with_data','stocks_from_listing','stocks_through_target','rows','failed_windows','pending_windows']){
          if(!Number.isSafeInteger(value.summary[key])||value.summary[key]<0)throw Error('Invalid count');
          summary[key]=value.summary[key];
        }
        const payload={schema:1,kind:value.kind,provider:value.provider==='eastmoney'?'eastmoney':'tencent',generated_at:value.generated_at,target_end:value.target_end,universe_as_of:value.universe_as_of,universe_complete:value.universe_complete===true,summary};
        res.writeHead(200,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(payload));
      }catch{
        res.writeHead(404,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify({status:'not_available'}));
      }
      return;
    }
    const [stock,history]=await Promise.all([archive('snapshots',c.stock_version),archive('history-snapshots',c.history_version)]);
    const broad=c.broad_version?await archive('broad-snapshots',c.broad_version):null;
    const market=c.market_version?await archive('market-snapshots',c.market_version):null;
    const bucket={async get(key){
      if(priceKey.test(key)||priceReadKey.test(key)){try{const body=await readFile(path.join(priceRoot,key),'utf8');if(Buffer.byteLength(body)>2*1024*1024)return null;JSON.parse(body);return {text:async()=>body};}catch{return null;}}
      if(key==='latest.json'){
        const meta=JSON.parse(await (await readChecked(stock,'meta.json')).text());
        return {text:async()=>JSON.stringify({...stock.manifest,generated_at:meta.generated_at})};
      }
      if(key==='history-latest.json')return {text:async()=>JSON.stringify(history.manifest)};
      if(key==='broad-latest.json')return broad?{text:async()=>JSON.stringify(broad.manifest)}:null;
      if(key==='market-latest.json')return market?{text:async()=>JSON.stringify(market.manifest)}:null;
      if(market&&key.startsWith('market/'+c.market_version+'/'))return readChecked(market,key.slice(('market/'+c.market_version+'/').length));
      if(broad&&key.startsWith('broad/'+c.broad_version+'/'))return readChecked(broad,key.slice(('broad/'+c.broad_version+'/').length));
      for(const [prefix,a] of [['snapshots/'+c.stock_version+'/',stock],['history/'+c.history_version+'/',history]]){
        if(key.startsWith(prefix))return readChecked(a,key.slice(prefix.length));
      }
      return null;
    },async put(key,value){
      if(!priceKey.test(key))throw Error('Local write is limited to public price caches');
      const body=typeof value==='string'?value:Buffer.from(value).toString('utf8');if(Buffer.byteLength(body)>2*1024*1024)throw Error('Price cache too large');JSON.parse(body);
      const destination=path.join(priceRoot,key);await mkdir(path.dirname(destination),{recursive:true});
      const temporary=destination+'.'+randomUUID()+'.tmp';await writeFile(temporary,body,'utf8');await rename(temporary,destination);
    }};
    const result=await worker.fetch(new Request('http://127.0.0.1:4173'+req.url,{method:req.method,headers:req.headers}),{BUCKET:bucket});
    res.writeHead(result.status,Object.fromEntries(result.headers));res.end(Buffer.from(await result.arrayBuffer()));
  }catch{res.writeHead(503,{'Content-Type':'text/plain; charset=utf-8'});res.end('本地数据尚未就绪，请检查快照配置与完整性。');}
});
server.listen(4173,'127.0.0.1',()=>console.log('ETF local application: http://127.0.0.1:4173'));
