import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {exportSnapshot} from '../scripts/export-snapshot.mjs';
import {BLOCKS,readSelectedSnapshot,publishSnapshot,restoreSnapshot,sha256} from '../scripts/snapshot-store.mjs';

function temp(t){
  const base=fs.realpathSync(os.tmpdir());
  const root=fs.mkdtempSync(path.join(base,'etf-native-snapshot-'));
  t.after(()=>{if(path.dirname(root)===base&&path.basename(root).startsWith('etf-native-snapshot-'))fs.rmSync(root,{force:true,recursive:true});});
  return root;
}
function write(file,value){fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,JSON.stringify(value));}
const read=file=>JSON.parse(fs.readFileSync(file,'utf8'));
const target='2026-11-10',previous='2026-11-09',start='2020-01-01';
function fixture(t){
  const root=temp(t),local=path.join(root,'local'),workerFile=path.join(root,'worker.mjs');
  const versions=Object.fromEntries(['history','broad','market'].map((n,i)=>[n,'20261110100000-'+String(i+1).repeat(32)]));
  const stocks=['300750','600519'].map(code=>({stock_code:code,stock_name:code,stock_listed_date:start}));
  const pricePath=code=>path.join(local,'price-cache/prices/eastmoney/v1',code+'.json');
  function archive(n,docs){
    const d=path.join(local,n+'-snapshots',versions[n]),hashes={};
    for(const [k,v] of Object.entries(docs)){
      write(path.join(d,k+'.json'),v);hashes[k]=sha256(JSON.stringify(v));
    }
    write(path.join(d,'manifest.json'),{version:versions[n],target_start:start,target_end:target,files:Object.keys(docs),hashes});
  }
  const meta=n=>({version:versions[n],target_start:start,target_end:target,history_version:versions.history});
  archive('market',{meta:meta('market'),catalog:{items:stocks}});
  archive('history',{meta:meta('history'),catalog:{items:[]}});
  archive('broad',{meta:{...meta('broad'),indexes:[],total_funds:0},calendar:{dates:[start,previous,target]},
    'index-all':{index:{},members:[],rows:[],off_exchange:{members:[],files:[],file_ranges:{},notes:[]}}});
  write(path.join(local,'local-view.json'),Object.fromEntries(Object.entries(versions).map(([n,v])=>[n+'_version',v])));
  for(const s of stocks)write(pricePath(s.stock_code),{checked_through:target,series:[[start,12],[previous,15],[target,16]]});
  const callsFile=path.join(root,'test-worker-calls');
  fs.writeFileSync(callsFile,'');
  fs.writeFileSync(workerFile,`import {appendFileSync} from 'node:fs';
const versions=${JSON.stringify(versions)};
export default {async fetch(req,env){
 appendFileSync(${JSON.stringify(callsFile)},'x');
 const url=new URL(req.url),[key,code]=url.pathname.split('/').slice(-3).filter((_,i)=>i!==1);
 let data={movement:{trading_dates:[]},period:url.searchParams.get('period'),series:[],etf_contributions:[]};
 if(key==='prices'){
 const obj=await env.BUCKET.get('prices/eastmoney/v1/'+code+'.json'),raw=JSON.parse(await obj.text());
 data={provider:'eastmoney',market_version:versions.market,history_version:versions.history,calendar_version:versions.broad,
   adjustment:'none',price_unit:'元/股',stock_code:code,listed_date:'${start}',target_end:'${target}',source_checked_through:raw.checked_through,
   series:raw.series.map(([trade_date,close])=>({trade_date,close})),trading_dates:raw.series.map(r=>r[0])};
 }return Response.json({code:0,data});}};`);
  return {root,local,workerFile,versions,pricePath,workerCalls:()=>fs.statSync(callsFile).size};
}

test('writes one immutable release, verifies seven files and skips a repeat',async t=>{
  const f=fixture(t);assert.equal(readSelectedSnapshot(f.root),null);
  const a=await exportSnapshot(f),v=readSelectedSnapshot(f.root);
  assert.equal(a.status,'published');assert.equal(a.id,v.id);assert.deepEqual(Object.keys(v.files),BLOCKS);
  assert.equal(v.targetEnd,target);assert.equal(v.summary.quotations['600519'].price_pending,false);
  const ptr=path.join(f.root,'.snapshot-store/latest.json'),mtime=fs.statSync(ptr).mtimeMs;
  assert.equal((await exportSnapshot(f)).status,'unchanged');assert.equal(fs.statSync(ptr).mtimeMs,mtime);
  const file=f.pricePath('300750'),p=read(file);p.unused_input_field='irrelevant to export';write(file,p);
  const before=f.workerCalls();
  assert.equal((await exportSnapshot(f)).id,a.id);assert.ok(f.workerCalls()>before);
  const afterComparison=f.workerCalls();
  assert.equal((await exportSnapshot(f)).id,a.id);assert.equal(f.workerCalls(),afterComparison);
  assert.equal(fs.statSync(ptr).mtimeMs,mtime);
  const receiptPath=path.join(f.root,'.snapshot-store/verified-input.json');
  const receipt=read(receiptPath);
  write(receiptPath,{...receipt,checksum:'0'.repeat(64)});
  assert.equal((await exportSnapshot(f)).id,a.id);assert.ok(f.workerCalls()>afterComparison);
  const afterRepair=f.workerCalls();await exportSnapshot(f);assert.equal(f.workerCalls(),afterRepair);
  assert.equal(read(receiptPath).checksum,receipt.checksum);
  assert.equal(fs.statSync(ptr).mtimeMs,mtime);
  assert.ok(!fs.existsSync(path.join(f.root,'src/lib/snapshot')));
});

test('allows a checked stale tail, preserves true source dates, rejects lost prices and query cutoff regression',async t=>{
  const f=fixture(t),file=f.pricePath('600519'),price=read(file);
  price.series.pop();price.checked_through=previous;write(file,price);
  const a=await exportSnapshot(f);
  assert.deepEqual(a.quotations['600519'],{source_checked_through:previous,price_available_end:previous,price_pending:true});
  price.checked_through=target;write(file,price);
  const b=await exportSnapshot(f);assert.equal(b.quotations['600519'].price_pending,true);assert.notEqual(a.id,b.id);
  price.checked_through=previous;write(file,price);
  await assert.rejects(exportSnapshot(f),/回退/);
  price.checked_through=target;price.series.shift();write(file,price);
  await assert.rejects(exportSnapshot(f),/回退/);
  assert.equal(readSelectedSnapshot(f.root).id,b.id);
});

test('missing cache and inconsistent source manifest or cross-reference leave the selected release intact',async t=>{
  const f=fixture(t),a=await exportSnapshot(f),file=f.pricePath('300750'),price=fs.readFileSync(file);
  fs.unlinkSync(file);await assert.rejects(exportSnapshot(f),/300750/);fs.writeFileSync(file,price);
  const dir=path.join(f.local,'market-snapshots',f.versions.market);
  const manifest=read(path.join(dir,'manifest.json')),meta=read(path.join(dir,'meta.json'));
  write(path.join(dir,'manifest.json'),{...manifest,target_end:previous});
  await assert.rejects(exportSnapshot(f),/清单版本或日期/);
  meta.history_version=f.versions.market;write(path.join(dir,'meta.json'),meta);
  manifest.hashes.meta=sha256(JSON.stringify(meta));write(path.join(dir,'manifest.json'),manifest);
  await assert.rejects(exportSnapshot(f),/references differ/);
  assert.equal(readSelectedSnapshot(f.root).id,a.id);
});

test('failed publish, competing publish and damaged pointer or block cannot silently select a partial release',async t=>{
  const f=fixture(t),a=await exportSnapshot(f),original=readSelectedSnapshot(f.root);
  const blocks=Object.fromEntries(BLOCKS.map(name=>[name,fs.readFileSync(path.join(original.dir,name))]));
  blocks['meta.js']=Buffer.concat([blocks['meta.js'],Buffer.from('\n')]);
  const next={blocks,inputFingerprint:'a'.repeat(64),targetEnd:target,summary:original.summary,expectedId:a.id};
  assert.throws(()=>publishSnapshot(f.root,{...next,checkInputs(){throw Error('source changed');}}),/source changed/);
  assert.equal(readSelectedSnapshot(f.root).id,a.id);
  const b=publishSnapshot(f.root,next);
  assert.throws(()=>publishSnapshot(f.root,{...next,inputFingerprint:'b'.repeat(64)}),/已改变/);
  assert.ok(!fs.existsSync(path.join(f.root,'.snapshot-store/verified-input.json')),'a stale exporter cannot write a receipt for the new selection');
  blocks['meta.js']=Buffer.concat([blocks['meta.js'],Buffer.from('\n')]);
  assert.throws(()=>publishSnapshot(f.root,next),/已改变/);
  assert.equal(readSelectedSnapshot(f.root).id,b.id);
  const ptr=path.join(f.root,'.snapshot-store/latest.json');
  fs.unlinkSync(ptr);assert.throws(()=>readSelectedSnapshot(f.root),/缺少 latest/);
  write(ptr,{schema:1,id:'../wrong'});assert.throws(()=>readSelectedSnapshot(f.root),/损坏/);
  assert.equal(restoreSnapshot(f.root,a.id).id,a.id);
  fs.appendFileSync(path.join(original.dir,'off.js'),'corrupt');assert.throws(()=>readSelectedSnapshot(f.root),/摘要不匹配/);
  assert.equal(restoreSnapshot(f.root,b.id).id,b.id);
  assert.ok(fs.readdirSync(path.join(f.root,'.snapshot-store/versions')).every(name=>/^[0-9a-f]{64}$/.test(name)));
});
