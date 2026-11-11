import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash} from 'node:crypto';
import {deflateRawSync} from 'node:zlib';
import {readPlatforms,recordPlatform} from '../scripts/platform-ledger.mjs';

const exec=promisify(execFile), uri=new URL('../scripts/platform-ledger.mjs',import.meta.url).href;
const W='wx1234567890abcdef', X='aabbccddeeff001122334455';
function workspace(t){
  const parent=fs.realpathSync(os.tmpdir()),root=fs.mkdtempSync(path.join(parent,'etf-ledger-'));
  t.after(()=>{if(path.dirname(root)===parent&&path.basename(root).startsWith('etf-ledger-'))fs.rmSync(root,{recursive:true,force:true});});return root;
}
// Produce a real, single-file ZIP. Alternate stored and deflated data in fixtures.
function zip(appid,method){
  const name=Buffer.from('project.config.json'),raw=Buffer.from(JSON.stringify({appid}));
  let crc=-1;for(const byte of raw){crc^=byte;for(let bit=0;bit<8;bit++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}crc=(~crc)>>>0;
  const bytes=method===8?deflateRawSync(raw):raw,lh=Buffer.alloc(30),cd=Buffer.alloc(46),end=Buffer.alloc(22);
  lh.writeUInt32LE(0x04034b50);lh.writeUInt16LE(20,4);lh.writeUInt16LE(method,8);lh.writeUInt32LE(crc,14);lh.writeUInt32LE(bytes.length,18);lh.writeUInt32LE(raw.length,22);lh.writeUInt16LE(name.length,26);
  cd.writeUInt32LE(0x02014b50);cd.writeUInt16LE(20,4);cd.writeUInt16LE(20,6);cd.writeUInt16LE(method,10);cd.writeUInt32LE(crc,16);cd.writeUInt32LE(bytes.length,20);cd.writeUInt32LE(raw.length,24);cd.writeUInt16LE(name.length,28);
  end.writeUInt32LE(0x06054b50);end.writeUInt16LE(1,8);end.writeUInt16LE(1,10);end.writeUInt32LE(cd.length+name.length,12);end.writeUInt32LE(lh.length+name.length+bytes.length,16);
  return Buffer.concat([lh,name,bytes,cd,name,end]);
}
function release(root,n,wx=W,xhs=X){
  const id='1.1.0-snapshot-20261110-'+String(n).padStart(16,'0'),dir=path.join(root,'release/versions',id);
  fs.mkdirSync(dir,{recursive:true});const manifest={id,appVersion:'1.1.0',mode:'snapshot',platforms:{}};
  for(const [platform,appid,method] of [['wechat',wx,0],['xiaohongshu',xhs,8]]){
    const bytes=zip(appid,method),archive=platform+'.zip';fs.writeFileSync(path.join(dir,archive),bytes);
    manifest.platforms[platform]={archive,sha256:createHash('sha256').update(bytes).digest('hex'),buildId:String(n).padStart(12,'0')};
  }
  fs.writeFileSync(path.join(dir,'manifest.json'),JSON.stringify(manifest));return {id,dir,manifest};
}
test('experience, review and active remain independent per host; only a previous published release can be a rollback target',t=>{
  const root=workspace(t),a=release(root,1).id,b=release(root,2).id,c=release(root,3).id;
  const set=(platform,id,state,note)=>recordPlatform(root,{platform,id,state,note});
  assert.equal(readPlatforms(root).platforms.wechat.active,null);assert(!fs.existsSync(path.join(root,'release/platform-ledger')));
  set('wechat',a,'trial');set('wechat',a,'in_review');set('wechat',a,'approved');assert.equal(readPlatforms(root).platforms.wechat.active,null);
  set('wechat',a,'published');set('wechat',b,'in_review');set('xiaohongshu',b,'rejected','审核未通过');
  let v=readPlatforms(root).platforms;assert.equal(v.wechat.active,a);assert.equal(v.wechat.review.id,b);assert.equal(v.xiaohongshu.active,null);
  set('wechat',c,'trial');assert.equal(readPlatforms(root).platforms.wechat.review.id,b);
  assert.throws(()=>set('wechat',c,'rolled_back'),/上线记录/);set('wechat',b,'published');set('wechat',c,'in_review');
  set('wechat',a,'rolled_back');assert.equal(set('wechat',a,'rolled_back').changed,false);
  v=readPlatforms(root).platforms;assert.equal(v.wechat.active,a);assert.equal(v.wechat.review.id,c);assert.equal(v.wechat.history.at(-1).state,'rolled_back');assert.equal(v.xiaohongshu.history.length,1);
});
test('checks the actual archive identity and checksum, refuses visitor/empty/mismatched AppID and never stores auth notes',t=>{
  const root=workspace(t),valid=release(root,1),tourist=release(root,2,'touristappid',''),other=release(root,3,'wx0000000000000000');
  const set=(r,platform='wechat',note)=>recordPlatform(root,{platform,id:r.id,state:'published',note});
  assert.throws(()=>set(tourist),/真实 AppID/);assert.throws(()=>set(tourist,'xiaohongshu'),/真实 AppID/);
  assert.throws(()=>set(valid,'wechat','{"service_token":"example"}'),/认证信息/);assert.throws(()=>set({id:'../other'}),/标识/);
  set(valid);assert.equal(set(valid).changed,false);assert.throws(()=>set(other),/另一个 AppID/);
  valid.manifest.platforms.xiaohongshu.appId='ffffffffffffffffffffffff';fs.writeFileSync(path.join(valid.dir,'manifest.json'),JSON.stringify(valid.manifest));
  assert.throws(()=>set(valid,'xiaohongshu'),/清单 AppID/);
  fs.appendFileSync(path.join(valid.dir,'xiaohongshu.zip'),'x');assert.throws(()=>set(valid,'xiaohongshu'),/SHA256/);
  assert.equal(readPlatforms(root).platforms.wechat.history.length,1);assert.equal(readPlatforms(root).platforms.xiaohongshu.history.length,0);
});
test('simultaneous processes commit an identical action once; interrupted staging files remain invisible',async t=>{
  const root=workspace(t),r=release(root,1),dir=path.join(root,'release/platform-ledger/wechat');fs.mkdirSync(dir,{recursive:true});
  fs.writeFileSync(path.join(dir,'.pending-simulated-crash'),'partial');
  const script=`import {recordPlatform} from ${JSON.stringify(uri)};const r=recordPlatform(process.argv[1],{platform:'wechat',id:process.argv[2],state:'published',note:'operator-confirmed'});console.log(r.changed)`;
  const results=await Promise.all(Array.from({length:6},()=>exec(process.execPath,['--input-type=module','-e',script,root,r.id])));
  assert.equal(results.filter(x=>x.stdout.trim()==='true').length,1);const view=readPlatforms(root).platforms.wechat;
  assert.equal(view.history.length,1);assert.equal(view.history[0].seq,1);assert.equal(view.active,r.id);
  fs.writeFileSync(path.join(dir,'00000001.json'),fs.readFileSync(path.join(dir,'00000001.json'),'utf8').replace('operator-confirmed','altered-after-write'));
  assert.throws(()=>readPlatforms(root),/指纹/);
});
