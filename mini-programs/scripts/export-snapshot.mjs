// Read only verified project snapshots. No external requests are made.
import fs from 'node:fs/promises';
import {readFileSync,statSync,existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {deflateRawSync} from 'node:zlib';
import {sha256,readSelectedSnapshot,hasVerifiedInput,publishSnapshot,restoreSnapshot,recoverLock,unpackModule} from './snapshot-store.mjs';
const defaultRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const featured=['300750','600519'];
const dateOK=d=>typeof d==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(d)&&Number.isFinite(Date.parse(d))&&new Date(d).toISOString().slice(0,10)===d;
export async function exportSnapshot({root=defaultRoot,local=path.resolve(root,'../web-trial/.local'),workerFile=path.resolve(root,'../web-trial/dist/server/index.js')}={}){
const captured=new Map(), blocks={},priceFiles=new Map();
function pin(file,limit=2*1024*1024){
  if(statSync(file).size>limit)throw Error('输入文件超过限制 '+path.basename(file));
  const raw=readFileSync(file);captured.set(file,sha256(raw));return raw;
}
const sel=JSON.parse(pin(path.join(local,'local-view.json')));
const selected=readSelectedSnapshot(root);
const previousDir=selected?.dir??path.join(root,'src/lib/snapshot');
const archives={};
for(const name of ['market','broad','history']){
  const version=sel[name+'_version'];
  if(!/^\d{14}-[\da-f]{32}$/.test(version))throw Error('Invalid version');
  const folder=path.join(local,name+'-snapshots',version);
  const manifest=JSON.parse(pin(path.join(folder,'manifest.json')));
  if(manifest.version!==version||!dateOK(manifest.target_start)||!dateOK(manifest.target_end)||manifest.target_start>manifest.target_end||
     !Array.isArray(manifest.files)||manifest.files.length>500||new Set(manifest.files).size!==manifest.files.length||
     !manifest.files.includes('meta')||!manifest.files.every(k=>typeof k==='string'&&/^[a-z0-9_-]+$/i.test(k)&&/^[0-9a-f]{64}$/.test(manifest.hashes?.[k])))
    throw Error(name+' 快照清单身份、文件或日期不符');
  const cache=new Map();
  archives[name]={manifest,read:async key=>{
    if(cache.has(key))return cache.get(key);
    if(!manifest.files.includes(key))throw Error('Unknown file '+key);
    const bytes=await fs.readFile(path.join(folder,key+'.json'));
    if(sha256(bytes)!==manifest.hashes[key])throw Error('Snapshot checksum mismatch');
    const value=JSON.parse(bytes);cache.set(key,value);return value;
  }};
}
const bm=await archives.broad.read('meta'), hm=await archives.history.read('meta'),mm=await archives.market.read('meta');
for(const [name,meta] of [['broad',bm],['history',hm],['market',mm]]){
  const {manifest}=archives[name];
  if(meta.version!==manifest.version||meta.target_start!==manifest.target_start||meta.target_end!==manifest.target_end)
    throw Error(name+' 快照 meta 与清单版本或日期不符');
}
if(bm.history_version!==hm.version||mm.history_version!==hm.version||bm.target_end!==hm.target_end||mm.target_end!==hm.target_end)throw Error('Snapshot references differ');
const end=bm.target_end;
const first=new Date(end+'T00:00:00Z'), day=first.getUTCDate();first.setUTCDate(1);first.setUTCMonth(first.getUTCMonth()-3);
first.setUTCDate(Math.min(day,new Date(Date.UTC(first.getUTCFullYear(),first.getUTCMonth()+1,0)).getUTCDate()));
const start=first.toISOString().slice(0,10);
for(const code of featured){
  const file=path.join(local,'price-cache/prices/eastmoney/v1',code+'.json');
  try{priceFiles.set(code,JSON.parse(pin(file)));}
  catch{throw Error('缺少或无法读取 '+code+' 的东方财富已核验缓存');}
}
pin(workerFile,16*1024*1024);
pin(fileURLToPath(import.meta.url));
pin(fileURLToPath(new URL('./snapshot-store.mjs',import.meta.url)));
const inputFingerprint=sha256(JSON.stringify([...captured.values()]));
function checkInputs(){
  for(const [file,hash] of captured)if(sha256(readFileSync(file))!==hash)throw Error('导出期间输入已改变 '+path.basename(file));
}
if(hasVerifiedInput(root,selected,inputFingerprint)){
  checkInputs();
  if(readSelectedSnapshot(root)?.id!==selected.id)throw Error('之前选择的快照已改变，请重新导出');
  return {status:'unchanged',id:selected.id,targetEnd:selected.targetEnd,...selected.summary};
}
const oldMeta=existsSync(path.join(previousDir,'meta.js'))?unpackModule(root,path.join(previousDir,'meta.js'),false):null;
if(oldMeta?.end>end)throw Error('目标日期早于当前离线快照，请显式恢复已有版本');
const worker=(await import(pathToFileURL(workerFile).href+'?sha='+captured.get(workerFile))).default;
const env={BUCKET:{async get(key){
  let obj;
  const latest=/^(broad|market|history)-latest\.json$/.exec(key);
  const file=/^(broad|market|history)\/([\w-]+)\/([\w-]+)\.json$/.exec(key);
  const price=/^prices\/eastmoney\/v1\/(\d{6})\.json$/.exec(key);
  if(latest)obj=archives[latest[1]].manifest;
  else if(file&&archives[file[1]].manifest.version===file[2])obj=await archives[file[1]].read(file[3]);
  else if(price&&featured.includes(price[1]))obj=priceFiles.get(price[1]);
  else return null;
  const body=JSON.stringify(obj);return {size:Buffer.byteLength(body),text:async()=>body};
}},PRICE_FETCH:()=>{throw Error('External calls disabled for export');}};
async function request(route,params){
  const u=new URL('https://snapshot.invalid/api/v1/'+route);
  for(const [k,v] of Object.entries(params))u.searchParams.set(k,v);
  const response=await worker.fetch(new Request(u),env), result=await response.json();
  if(!response.ok||result.code!==0)throw Error(route+' failed '+response.status);
  return result.data;
}
const sizes={}, quotations={};
async function pack(name,object){
  const raw=Buffer.from(JSON.stringify(object)),compressed=deflateRawSync(raw,{level:9});
  if(raw.length>16*1024*1024)throw Error(name+' expands beyond allowed memory');
  sizes[name]={bytes:raw.length,compressed:compressed.length};
  blocks[name+'.js']=Buffer.from('module.exports='+JSON.stringify([raw.length,compressed.toString('base64')])+';\n');
}
const cal=(await archives.broad.read('calendar')).dates;
const fundCodes=new Set(), stockCatalog=(await archives.market.read('catalog')).items;
const stocks=featured.map(code=>stockCatalog.find(s=>s.stock_code===code));
for(const stock of stocks){
  if(!stock?.stock_listed_date)throw Error('Missing stock listing boundary');
  const details={};
  for(const period of ['1d','5d','20d','3m','6m','1y','2y','3y','5y','all']){
    const result=await request('market/stock/'+stock.stock_code,{period});
    result.etf_contributions.forEach(f=>fundCodes.add(f.etf_code));
    delete result.top_etfs;
    details[period]=result;
  }
  // Store symbol periods independently to bound memory and re-use shared rows.
  const base=details.all;
  const series=base.series.map(r=>[r.trade_date,r.flow,r.valid_etfs,r.expected_etfs]);
  const funds=Object.fromEntries(base.etf_contributions.map(f=>[f.etf_code,f]));
  for(const result of Object.values(details)){
    delete result.series;delete result.movement.trading_dates;
    result.etf_contributions=result.etf_contributions.map(f=>[f.etf_code,f.flow,f.valid_days,f.status]);
  }
  const prices=await request('prices/stock/'+stock.stock_code,{start:stock.stock_listed_date,end});
  const code=stock.stock_code,srcRaw=priceFiles.get(code);
  if(prices.provider!=='eastmoney'||prices.market_version!==mm.version||prices.history_version!==hm.version||
     prices.calendar_version!==bm.version||prices.target_end!==end||prices.adjustment!=='none'||prices.price_unit!=='元/股'||
     prices.stock_code!==code||prices.listed_date!==stock.stock_listed_date||!prices.series?.length||
     !dateOK(srcRaw.checked_through)||srcRaw.checked_through>end||srcRaw.checked_through!==prices.source_checked_through)
    throw Error(code+' 缺少通过日期、证券身份和来源核验的收盘价');
  const sourceDates=new Set(srcRaw.series.map(row=>row[0]));
  const actualEnd=srcRaw.series.at(-1)?.[0];
  const previousStock=oldMeta?.stocks?.find(s=>s.stock_code===code);
  if(previousStock){
    const previous=unpackModule(root,path.join(previousDir,'s'+code+'.js'));
    if(!Array.isArray(previous.prices)||!Array.isArray(previous.excluded)||
       previousStock.source_checked_through>srcRaw.checked_through||
       previous.prices.some(r=>!sourceDates.has(r[0]))||previous.excluded.some(day=>!sourceDates.has(day)))
      throw Error(code+' 的价格查询日期或已取得的历史日期出现回退，保留上一快照');
  }
  quotations[code]={source_checked_through:srcRaw.checked_through,price_available_end:actualEnd,
    price_pending:srcRaw.checked_through<end||actualEnd<end};
  const dates=new Set(prices.trading_dates);
  const excluded=srcRaw.series.map(r=>r[0]).filter(d=>!dates.has(d));
  await pack('s'+stock.stock_code,{details,series,funds,prices:prices.series.map(r=>[r.trade_date,r.close]),excluded});
}
const indices={}, offAll=await archives.broad.read('index-all');
for(const code of ['all',...bm.indexes.map(i=>i.code)]){
  const idx=await archives.broad.read('index-'+code);
  idx.members.forEach(f=>fundCodes.add(f.fund_code));
  indices[code]={index:idx.index,rows:idx.rows.filter(r=>r[0]>=start&&r[0]<=end),
    members:idx.members.map(f=>[f.fund_code,f.fund_name,f.listed_date,f.delisted_date,f.index_code]),
    offCodes:idx.off_exchange.members.map(f=>f.fund_code)};
}
const off=[];
for(const file of offAll.off_exchange.files){
  const range=offAll.off_exchange.file_ranges[file];if(range&&(range.end<start||range.start>end))continue;
  for(const r of (await archives.broad.read(file)).reports)if(r.period_end>=start&&r.period_end<=end)off.push(r);
}
const funds={},catalog=(await archives.history.read('catalog')).items;
for(const f of catalog.filter(f=>fundCodes.has(f.fund_code))){
  const rows=(await archives.history.read(f.file)).funds[f.fund_code];
  if(!Array.isArray(rows))throw Error('Missing history for '+f.fund_code);
  funds[f.fund_code]={name:f.fund_name,listed:f.listed_date,delisted:f.delisted_date,rows:rows.filter(r=>r[0]>=start&&r[0]<=end)};
}
await pack('funds',funds);await pack('off',off);await pack('indices',indices);await pack('calendar',cal);
const meta={broad:{...bm,target_start:start,offline:true},history:{...hm,target_start:start,offline:true},market:mm,
  start,end,stocks:stocks.map(({stock_code,stock_name,stock_listed_date,industry_name})=>({stock_code,stock_name,stock_listed_date,industry_name,...quotations[stock_code]})),
  offNotes:offAll.off_exchange.notes,indices:bm.indexes.length,broadFunds:bm.total_funds,funds:Object.keys(funds).length,reports:off.length};
delete meta.broad.mapping_checks;delete meta.broad.off_exchange;
blocks['meta.js']=Buffer.from('module.exports='+JSON.stringify(meta)+';\n');
return publishSnapshot(root,{blocks,inputFingerprint,targetEnd:end,expectedId:selected?.id??null,checkInputs,
  summary:{start,end,indices:meta.indices,groupFunds:meta.broadFunds,fundHistories:meta.funds,reports:off.length,stocks:featured,quotations,sizes,
    sourceVersions:{market:mm.version,history:hm.version,broad:bm.version}}});
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{
    const args=process.argv.slice(2);
    let result;
    if(!args.length)result=await exportSnapshot();
    else if(args.length===2&&args[0]==='--restore')result=restoreSnapshot(defaultRoot,args[1]);
    else if(args.length===1&&args[0]==='--recover-lock')result=recoverLock(defaultRoot);
    else throw Error('用法: node scripts/export-snapshot.mjs [--restore 版本ID | --recover-lock]');
    console.log(JSON.stringify(result));
  }catch(error){console.error(JSON.stringify({status:'failed',reason:error.message}));process.exitCode=1;}
}
