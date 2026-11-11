/** Local records of operator-confirmed host actions. No network or host SDK calls. */
import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {inflateRawSync} from 'node:zlib';

const HOSTS=['wechat','xiaohongshu'];
export const PLATFORM_STATES=['trial','in_review','rejected','approved','published','rolled_back'];
const ID=/^(\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?)-(snapshot|local|live)-(\d{8}|online)-[0-9a-f]{16}$/;
const SHA=/^[0-9a-f]{64}$/,BUILD=/^[0-9a-f]{12}$/;
const digest=value=>createHash('sha256').update(value).digest('hex');
const empty=()=>({active:null,trial:null,review:null,appId:null,history:[]});

function safeFile(root,relative){
  const full=path.resolve(root,relative), rel=path.relative(root,full);
  if(!rel||rel==='..'||rel.startsWith('..'+path.sep)||path.isAbsolute(rel))throw Error('台账路径越界');
  let current=root;
  for(const part of rel.split(path.sep)){
    current=path.join(current,part);
    try{if(fs.lstatSync(current).isSymbolicLink())throw Error('台账与交付目录不接受符号链接');}
    catch(error){if(error.code!=='ENOENT')throw error;}
  }
  return full;
}
function jsonFile(filename,maxSize){
  const stat=fs.statSync(filename);
  if(!stat.isFile()||stat.size>maxSize)throw Error('台账或交付清单文件不合法');
  return JSON.parse(fs.readFileSync(filename,'utf8').replace(/^\uFEFF/,''));
}

// Read the root project configuration in place. The archives produced by the
// release tool are ordinary ZIPs; no entry is extracted onto the filesystem.
function archivedProject(zip){
  let end=-1;
  for(let i=zip.length-22;i>=Math.max(0,zip.length-65557);i--){
    if(zip.readUInt32LE(i)===0x06054b50&&i+22+zip.readUInt16LE(i+20)===zip.length){end=i;break;}
  }
  if(end<0||zip.readUInt32LE(end+4)!==0)throw Error('无法读取交付 ZIP 的中央目录');
  const count=zip.readUInt16LE(end+10),size=zip.readUInt32LE(end+12),start=zip.readUInt32LE(end+16);
  if(start+size!==end||count!==zip.readUInt16LE(end+8))throw Error('交付 ZIP 中央目录长度不一致');
  let at=start, project=null;
  for(let i=0;i<count;i++){
    if(at+46>end||zip.readUInt32LE(at)!==0x02014b50)throw Error('交付 ZIP 条目损坏');
    const nameSize=zip.readUInt16LE(at+28),next=at+46+nameSize+zip.readUInt16LE(at+30)+zip.readUInt16LE(at+32);
    if(next>end)throw Error('交付 ZIP 条目越界');
    const name=zip.toString('utf8',at+46,at+46+nameSize).replace(/\\/g,'/').replace(/^\.\//,'');
    if(name==='project.config.json'){
      if(project)throw Error('ZIP 包含重复项目配置');
      const method=zip.readUInt16LE(at+10),compressed=zip.readUInt32LE(at+20),length=zip.readUInt32LE(at+24),local=zip.readUInt32LE(at+42);
      if(length>1024*1024||local+30>start||zip.readUInt32LE(local)!==0x04034b50||zip.readUInt16LE(at+8)&1)throw Error('ZIP 项目配置不合法');
      const nameLength=zip.readUInt16LE(local+26),dataStart=local+30+nameLength+zip.readUInt16LE(local+28);
      const localName=zip.toString('utf8',local+30,local+30+nameLength).replace(/\\/g,'/').replace(/^\.\//,'');
      if(localName!==name||dataStart+compressed>start||zip.readUInt16LE(local+8)!==method)throw Error('ZIP 项目配置条目不一致');
      const bytes=zip.subarray(dataStart,dataStart+compressed);
      let raw;if(method===0)raw=bytes;else if(method===8)raw=inflateRawSync(bytes,{maxOutputLength:1024*1024});else throw Error('ZIP 项目配置压缩方式不支持');
      if(raw.length!==length)throw Error('ZIP 项目配置长度不一致');
      project=JSON.parse(raw.toString('utf8').replace(/^\uFEFF/,''));
    }
    at=next;
  }
  if(at!==end||!project||typeof project!=='object')throw Error('交付 ZIP 缺少项目根 project.config.json');
  return project;
}
function accountOK(platform,id){
  if(typeof id!=='string'||/^(touristappid|testappid|your[-_]?appid)$/i.test(id))return false;
  return platform==='wechat'?/^wx[0-9a-f]{16}$/i.test(id):/^[A-Za-z0-9_-]{24}$/.test(id);
}
function verifiedPackage(root,platform,id){
  const match=typeof id==='string'&&id.length<=128&&ID.exec(id);
  if(!match)throw Error('交付标识格式不合法');
  const base='release/versions/'+id+'/';
  const manifest=jsonFile(safeFile(root,base+'manifest.json'),1024*1024);
  if(manifest.id!==id||manifest.appVersion!==match[1]||manifest.mode!==match[2]||!HOSTS.every(host=>manifest.platforms?.[host]))throw Error('成对交付清单身份不一致');
  const pkg=manifest.platforms[platform],name=pkg.archive;
  if(typeof name!=='string'||name.length>128||name.startsWith('.')||/[\\/:*?"<>|\x00-\x1f]/.test(name)||!name.endsWith('.zip')||!SHA.test(pkg.sha256)||!BUILD.test(pkg.buildId))throw Error('平台包清单格式不合法');
  const filename=safeFile(root,base+name),stat=fs.statSync(filename);
  if(!stat.isFile()||stat.size>32*1024*1024)throw Error('平台程序包不合法');
  const zip=fs.readFileSync(filename);
  if(digest(zip)!==pkg.sha256)throw Error('平台程序包 SHA256 与交付清单不一致');
  const appId=archivedProject(zip).appid;
  if(!accountOK(platform,appId))throw Error('请使用平台后台登记的真实 AppID 重建对应程序包并重新生成交付版本，再登记实际平台操作');
  const declared=pkg.appId??pkg.appid;
  if(declared!=null&&declared!==appId)throw Error('交付清单 AppID 与包内 project.config.json 不一致');
  return {id,appVersion:manifest.appVersion,mode:manifest.mode,buildId:pkg.buildId,archiveSha256:pkg.sha256,appId};
}

function consume(view,event){
  if(view.appId&&view.appId!==event.appId)throw Error('该平台台账已绑定另一个 AppID');
  view.appId=event.appId;
  if(event.state==='trial')view.trial=event.id;
  if(event.state==='in_review'||event.state==='rejected'||event.state==='approved'){
    // A delayed decision for a different version does not clear a newer submission.
    if(event.state==='in_review'||!view.review||view.review.id===event.id||view.review.state!=='in_review')
      view.review={id:event.id,state:event.state,at:event.at};
  }
  if(event.state==='published'||event.state==='rolled_back'){
    if(event.state==='rolled_back'&&!view.history.some(row=>row.id===event.id&&row.state==='published'))throw Error('只能登记回退到本平台曾确认上线的版本');
    view.active=event.id;
    if(view.review?.id===event.id)view.review=null;
  }
  view.history.push(event);
}
function readOne(root,platform){
  const dir=safeFile(root,'release/platform-ledger/'+platform),view=empty();let previous=null;
  if(!fs.existsSync(dir))return {view,previous};
  const names=fs.readdirSync(dir).filter(name=>/^\d{8}\.json$/.test(name)).sort();
  for(const [index,name] of names.entries()){
    const file=safeFile(root,'release/platform-ledger/'+platform+'/'+name),size=fs.statSync(file).size;
    if(size>16*1024)throw Error('平台台账事件过大');
    const raw=fs.readFileSync(file),event=JSON.parse(raw.toString('utf8'));
    const {contentHash,...payload}=event;
    if(!SHA.test(contentHash)||digest(JSON.stringify(payload))!==contentHash)throw Error('平台台账事件指纹不一致');
    if(event.schema!==1||event.seq!==index+1||Number(name.slice(0,8))!==event.seq||event.previous!==previous||event.platform!==platform||!PLATFORM_STATES.includes(event.state)||!ID.test(event.id)||!accountOK(platform,event.appId)||!SHA.test(event.archiveSha256)||!BUILD.test(event.buildId))throw Error('平台台账序号、身份或内容指纹不一致');
    consume(view,event);previous=digest(raw);
  }
  return {view,previous};
}
export function readPlatforms(root){
  root=fs.realpathSync(root);
  return {schema:1,platforms:Object.fromEntries(HOSTS.map(host=>[host,readOne(root,host).view]))};
}
function already(view,id,state,note){
  if(state==='published')return view.active===id&&view.review?.id!==id;
  if(state==='rolled_back')return view.active===id;
  if(state==='trial')return view.trial===id;
  if(view.review?.id===id&&view.review.state===state)return true;
  // Keep a repeated delayed decision from adding another history row.
  const recent=view.history.findLast(e=>e.id===id&&e.state!=='trial');
  return recent?.state===state&&recent.note===note;
}
function syncDirectory(dir){
  let fd;
  try{fd=fs.openSync(dir,'r');fs.fsyncSync(fd);}catch(error){if(!['EACCES','EPERM','EINVAL','ENOTSUP','EBADF','EISDIR'].includes(error.code))throw error;}
  finally{if(fd!==undefined)fs.closeSync(fd);}
}
export function recordPlatform(root,{platform,id,state,note=''}){
  if(!HOSTS.includes(platform)||!PLATFORM_STATES.includes(state))throw Error('平台或平台操作状态不合法');
  if(typeof note!=='string'||note.length>1200||/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(note)||/\b(?:authorization["']?\s*[:：]|bearer\s+\S+|(?:service_token|access_token|client_secret|appsecret|api_key|password)["']?\s*[:=：])|-----BEGIN [A-Z ]*PRIVATE KEY-----/i.test(note))throw Error('备注须为简短说明，不能包含认证信息');
  note=note.trim();root=fs.realpathSync(root);
  const pkg=verifiedPackage(root,platform,id),dir=safeFile(root,'release/platform-ledger/'+platform);
  fs.mkdirSync(dir,{recursive:true});
  for(;;){
    const {view,previous}=readOne(root,platform);
    if(view.appId&&view.appId!==pkg.appId)throw Error('该平台台账已绑定另一个 AppID');
    const saved=view.history.find(e=>e.id===id);
    if(saved&&(saved.archiveSha256!==pkg.archiveSha256||saved.buildId!==pkg.buildId))throw Error('同一交付标识的已登记程序包发生变化');
    if(state==='rolled_back'&&!view.history.some(e=>e.id===id&&e.state==='published'))throw Error('回退目标尚无本平台实际上线记录');
    if(already(view,id,state,note))return {...readPlatforms(root),changed:false};
    const seq=view.history.length+1,event={schema:1,seq,previous,platform,state,...pkg,note,at:new Date().toISOString()};
    if(seq>99999999)throw Error('平台台账事件序号已用尽');
    event.contentHash=digest(JSON.stringify(event));
    const ordinal=String(seq).padStart(8,'0'),target=safeFile(root,'release/platform-ledger/'+platform+'/'+ordinal+'.json');
    const temp=path.join(dir,'.pending-'+randomUUID());let fd;
    try{
      fd=fs.openSync(temp,'wx');fs.writeFileSync(fd,JSON.stringify(event)+'\n');fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
      // Atomic compare-and-create: only one process wins a sequence number. On
      // failure the old history is still complete and unpublished temps are ignored.
      try{fs.linkSync(temp,target);}catch(error){if(error.code==='EEXIST')continue;throw error;}
      syncDirectory(dir);
    }finally{if(fd!==undefined)fs.closeSync(fd);try{fs.unlinkSync(temp);}catch(error){if(error.code!=='ENOENT')throw error;}}
    return {...readPlatforms(root),changed:true};
  }
}
