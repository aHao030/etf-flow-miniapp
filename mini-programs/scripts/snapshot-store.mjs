import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {inflateRawSync} from 'node:zlib';

export const BLOCKS=['calendar.js','funds.js','indices.js','meta.js','off.js','s300750.js','s600519.js'];
export const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
const HASH=/^[0-9a-f]{64}$/;
const directory=root=>path.join(root,'.snapshot-store');
const safeDate=d=>typeof d==='string'&&/^\d{4}-\d\d-\d\d$/.test(d)&&Number.isFinite(Date.parse(d))&&new Date(d).toISOString().slice(0,10)===d;

function safe(root,file) {
  const rel=path.relative(root,file);
  if(!rel||rel==='..'||rel.startsWith('..'+path.sep)||path.isAbsolute(rel))throw Error('快照路径越界');
  let current=root;
  for(const part of rel.split(path.sep)){
    current=path.join(current,part);
    const stat=fs.lstatSync(current,{throwIfNoEntry:false});
    if(stat?.isSymbolicLink())throw Error('快照路径含符号链接或联接点');
  }
  return file;
}
function read(root,file,limit=2*1024*1024){
  const stat=fs.statSync(safe(root,file));
  if(!stat.isFile()||stat.size>limit)throw Error('快照文件类型或大小异常 '+path.basename(file));
  return fs.readFileSync(file);
}
function readVersion(root,id){
  if(!HASH.test(id))throw Error('快照版本标识无效');
  const dir=path.join(directory(root),'versions',id);
  const body=read(root,path.join(dir,'manifest.json'),256*1024);
  if(sha256(body)!==id)throw Error('快照清单摘要不匹配');
  const m=JSON.parse(body);
  if(m.schema!==1||!safeDate(m.targetEnd)||!HASH.test(m.inputFingerprint)||!m.files||
     JSON.stringify(Object.keys(m.files).sort())!==JSON.stringify(BLOCKS))throw Error('快照清单格式无效');
  for(const name of BLOCKS){
    if(sha256(read(root,path.join(dir,name)))!==m.files[name])throw Error('快照块摘要不匹配 '+name);
  }
  return {id,dir,...m};
}
export function readSelectedSnapshot(root){
  const store=directory(root), pointer=safe(root,path.join(store,'latest.json'));
  if(!fs.existsSync(pointer)){
    const versions=safe(root,path.join(store,'versions'));
    if(fs.existsSync(versions)&&fs.readdirSync(versions).some(name=>HASH.test(name)))throw Error('已有快照版本但缺少 latest.json，请显式恢复已验证版本');
    return null;
  }
  const p=JSON.parse(read(root,pointer,4096));
  if(p.schema!==1||!HASH.test(p.id))throw Error('快照指针损坏');
  return readVersion(root,p.id);
}

// The lock covers only the final compare-and-switch. Readers pin an immutable
// directory, and can safely finish a build after the active pointer changes.
function lock(root,fn){
  const store=safe(root,directory(root));fs.mkdirSync(store,{recursive:true});
  const file=safe(root,path.join(store,'.publish.lock'));
  try{fs.writeFileSync(file,JSON.stringify({pid:process.pid,startedAt:new Date().toISOString()}),{flag:'wx'});}
  catch(e){if(e.code==='EEXIST')throw Error('另一导出正在提交；若原进程已退出，运行 --recover-lock');throw e;}
  try{return fn();}finally{fs.unlinkSync(file);}
}
export function recoverLock(root){
  const file=safe(root,path.join(directory(root),'.publish.lock'));
  const owner=JSON.parse(read(root,file,4096));
  if(!Number.isSafeInteger(owner.pid)||owner.pid<=0)throw Error('发布锁没有有效进程号，请人工核验');
  try{process.kill(owner.pid,0);throw Error('发布锁所属进程仍在运行');}catch(e){if(e.code!=='ESRCH')throw e;}
  fs.renameSync(file,file+'.abandoned-'+randomUUID());
  return {status:'lock_recovered',pid:owner.pid};
}
function atomicStoreJSON(root,name,value){
  const dest=safe(root,path.join(directory(root),name)), tmp=dest+'.'+randomUUID()+'.tmp';
  try{
    fs.writeFileSync(tmp,JSON.stringify(value)+'\n',{flag:'wx'});
    fs.renameSync(tmp,safe(root,dest));
  }finally{if(fs.existsSync(tmp))fs.unlinkSync(tmp);}
}
function switchPointer(root,id,previous){
  atomicStoreJSON(root,'latest.json',{schema:1,id,previous});
}
const receiptName='verified-input.json';
function inputReceipt(id,inputFingerprint){
  const binding={schema:1,id,inputFingerprint};
  return {...binding,checksum:sha256(JSON.stringify(binding))};
}
export function hasVerifiedInput(root,selected,inputFingerprint){
  if(!selected)return false;
  if(selected.inputFingerprint===inputFingerprint)return true;
  try{
    const receipt=JSON.parse(read(root,path.join(directory(root),receiptName),4096));
    return receipt.schema===1&&receipt.id===selected.id&&receipt.inputFingerprint===inputFingerprint&&
      receipt.checksum===inputReceipt(selected.id,inputFingerprint).checksum;
  }catch{return false;} // A missing or damaged auxiliary receipt requires a full comparison.
}
export function restoreSnapshot(root,id){
  return lock(root,()=>{
    const v=readVersion(root,id);
    let previous=null;
    try{previous=readSelectedSnapshot(root)?.id??null;}catch{/* Explicit repair may replace a broken pointer. */}
    if(previous!==id)switchPointer(root,id,previous);
    return {status:previous===id?'unchanged':'restored',id,previous,targetEnd:v.targetEnd};
  });
}
export function publishSnapshot(root,{blocks,inputFingerprint,targetEnd,summary,expectedId,checkInputs}){
  if(!HASH.test(inputFingerprint)||!safeDate(targetEnd)||Object.keys(blocks).sort().join()!==BLOCKS.join())throw Error('待发布快照不完整');
  if(Object.values(blocks).reduce((total,bytes)=>total+Buffer.byteLength(bytes),0)>2*1024*1024)throw Error('快照数据块总大小超过小程序主包上限');
  const files=Object.fromEntries(BLOCKS.map(name=>[name,sha256(blocks[name])]));
  const current=readSelectedSnapshot(root);
  if(current?.targetEnd===targetEnd&&BLOCKS.every(name=>current.files[name]===files[name])){
    return lock(root,()=>{
      const active=readSelectedSnapshot(root);
      if(!active||active.id!==expectedId||active.id!==current.id)throw Error('之前选择的快照已改变，请重新导出');
      checkInputs?.();
      if(!hasVerifiedInput(root,active,inputFingerprint)){
        atomicStoreJSON(root,receiptName,inputReceipt(active.id,inputFingerprint));
      }
      return {status:'unchanged',id:active.id,targetEnd,...active.summary};
    });
  }
  const body=Buffer.from(JSON.stringify({schema:1,targetEnd,inputFingerprint,files,summary})+'\n');
  const id=sha256(body),base=safe(root,path.join(directory(root),'versions'));
  fs.mkdirSync(base,{recursive:true});
  const stage=fs.mkdtempSync(path.join(base,'.stage-'));
  try{
    for(const name of BLOCKS){
      const file=path.join(stage,name);
      fs.writeFileSync(file,blocks[name],{flag:'wx'});
      if(sha256(read(root,file))!==files[name])throw Error('暂存快照写入校验失败 '+name);
    }
    fs.writeFileSync(path.join(stage,'manifest.json'),body,{flag:'wx'});
    return lock(root,()=>{
      const previous=readSelectedSnapshot(root);
      if(previous?.id===id){checkInputs?.();return {status:'unchanged',id,previous:id,targetEnd,...summary};}
      if((previous?.id??null)!==expectedId)throw Error('之前选择的快照已改变，请重新导出');
      checkInputs?.();
      const version=safe(root,path.join(base,id));
      if(!fs.existsSync(version))fs.renameSync(stage,version);
      readVersion(root,id);
      switchPointer(root,id,previous?.id??null);
      return {status:'published',id,previous:previous?.id??null,targetEnd,...summary};
    });
  }finally{
    // Remove only the explicitly known files in this process's staging directory.
    if(fs.existsSync(stage)){
      for(const name of [...BLOCKS,'manifest.json']){
        const file=safe(root,path.join(stage,name));if(fs.existsSync(file))fs.unlinkSync(file);
      }
      fs.rmdirSync(stage);
    }
  }
}

export function unpackModule(root,file,packed=true){
  const source=read(root,file).toString('utf8');
  if(!source.startsWith('module.exports=')||!source.trimEnd().endsWith(';'))throw Error('旧快照数据块格式不符');
  const value=JSON.parse(source.trimEnd().slice('module.exports='.length,-1));
  if(!packed)return value;
  if(!Array.isArray(value)||!Number.isSafeInteger(value[0])||value[0]<1||value[0]>16*1024*1024||typeof value[1]!=='string')throw Error('旧快照压缩块无效');
  const raw=inflateRawSync(Buffer.from(value[1],'base64'),{maxOutputLength:value[0]});
  if(raw.length!==value[0])throw Error('旧快照解压长度错误');
  return JSON.parse(raw);
}
