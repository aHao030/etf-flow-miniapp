import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {build,options} from '../build.mjs';
import {runInNewContext} from 'node:vm';

function fixture(t) {
  const temp=fs.realpathSync(os.tmpdir());
  const root=fs.mkdtempSync(path.join(temp,'etf-native-build-'));
  t.after(()=>{if(path.dirname(root)===temp&&path.basename(root).startsWith('etf-native-build-'))fs.rmSync(root,{recursive:true,force:true});});
  function write(name,body){const dest=path.join(root,name);fs.mkdirSync(path.dirname(dest),{recursive:true});fs.writeFileSync(dest,body);}
  write('version.json',JSON.stringify({appVersion:'1.1.0'}));
  write('src/lib/updates.js','module.exports={};');
  write('src/lib/fund-list.js','module.exports={};');
  const pages=['broad','stock','fund','about'].map(n=>'pages/'+n+'/index'),components=['flow-chart','source-note'].map(n=>'components/'+n+'/index');
  const icon=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9oITJNsAAAAASUVORK5CYII=','base64');
  const tabs=['broad','stock','about'].map(n=>({pagePath:'pages/'+n+'/index',iconPath:'assets/'+n+'.png',selectedIconPath:'assets/'+n+'-active.png'}));
  write('src/app.json',JSON.stringify({pages,usingComponents:{'source-note':'/components/source-note/index'},tabBar:{list:tabs}}));
  write('src/app.js','App({});');write('src/app.wxss','.page{}');
  for(const item of tabs){write('src/'+item.iconPath,icon);write('src/'+item.selectedIconPath,icon);}
  for(const stem of [...pages,...components]){
    const component=stem.startsWith('components/');
    write('src/'+stem+'.js',component?'Component({methods:{open(){}}});':'Page({open(){}});');
    write('src/'+stem+'.json',JSON.stringify(component?{component:true}:{usingComponents:{'flow-chart':'../../components/flow-chart/index'}}));
    write('src/'+stem+'.wxml','<view wx:if="{{show}}" bindtap="open">{{data}}</view>');write('src/'+stem+'.wxss','.page{}');
  }
  for(const name of ['data','offline','chart','format','vendor/tiny-inflate',...['meta','funds','calendar','indices','off','s300750','s600519'].map(n=>'snapshot/'+n)])write('src/lib/'+name+'.js',name==='data'?"require('./config');require('./offline');":'module.exports={};');
  write('src/lib/vendor/LICENSE','Test licence');write('src/lib/platform.js',"const host = wx;\nconst platformName = 'wechat';");
  write('src/unrelated.json','not in the source allowlist');
  return {root,write,read:n=>fs.readFileSync(path.join(root,n),'utf8')};
}

test('builds four native pages, two components and six icons for each host using the allowed files',t=>{
  const {root,read}=fixture(t);const reports=build(root,options());assert.equal(reports.length,2);
  assert(reports.every(r=>r.bytes<2*1024*1024&&r.pages===4&&r.components===2));
  assert.match(read('dist/xiaohongshu/pages/stock/index.xhsml'),/xhs:if=/);assert.match(read('dist/xiaohongshu/lib/platform.js'),/const host = xhs;/);
  assert.match(read('dist/wechat/pages/stock/index.wxml'),/wx:if=/);assert(fs.existsSync(path.join(root,'dist/xiaohongshu/app.css')));
  assert.equal(JSON.parse(read('dist/wechat/project.config.json')).appid,'touristappid');assert.equal(JSON.parse(read('dist/xiaohongshu/project.config.json')).appid,'');
  assert(!fs.existsSync(path.join(root,'dist/wechat/unrelated.json')));assert(build(root).every(r=>r.changed===0));
});

test('configuration supports local/live and explicit AppIDs while rejecting embedded credentials and private owner site',t=>{
  for(const url of ['http://example.org/api/v1','https://u:p@example.org/api/v1','https://example.org/api/v1?token=nope','https://example.org/api/v1#fragment','https://example.org/not-api','https://etf-stock-flow-observer-1031.sweetowlet132.chatgpt.site/api/v1'])assert.throws(()=>options(['--mode','live','--base-url',url]));
  assert.throws(()=>options(['--service-token','nope']));assert.throws(()=>options(['--mode','live']));assert.throws(()=>options(['--mode','local','--base-url','https://example.org/api/v1']));
  const {root,read}=fixture(t);build(root,options(['--mode','local']));assert.match(read('dist/wechat/lib/config.js'),/127\.0\.0\.1:4173\/api\/v1/);
  build(root,options(['--mode','live','--base-url','https://api.example.org/api/v1','--wechat-appid','wx0123456789abcdef','--xhs-appid','xhs1234']));
  assert.equal(JSON.parse(read('dist/xiaohongshu/project.config.json')).appid,'xhs1234');assert.equal(JSON.parse(read('dist/wechat/project.config.json')).appid,'wx0123456789abcdef');assert.match(read('dist/wechat/lib/config.js'),/https:\/\/api\.example\.org\/api\/v1/);
});

for(const mode of ['local','live'])test('switching snapshot to '+mode+' removes offline assets and ties both build IDs to program changes',t=>{
  const {root,read,write}=fixture(t);
  const hosts=['wechat','xiaohongshu'];
  const opts=options(mode==='local'?['--mode','local']:['--mode','live','--base-url','https://api.example.org/api/v1']);
  const fundMarker='fixture-private-fund-rows', archiveMarker='fixture-compressed-close-series';
  write('src/lib/snapshot/funds.js','module.exports="'+fundMarker+'";');
  write('src/lib/snapshot/s600519.js','module.exports="'+archiveMarker+'";');
  function exported(file){
    const box={module:{exports:{}}};
    runInNewContext(read(file),box,{filename:file});
    return box.module.exports;
  }
  const receipt=host=>JSON.parse(read('dist/.generated-'+host+'.json')).files;
  const config=host=>exported('dist/'+host+'/lib/config.js');
  build(root);
  const snapshots=hosts.map(receipt);
  for(const host of hosts){
    assert.ok(read('dist/'+host+'/lib/snapshot/funds.js').includes(fundMarker));
    assert.ok(read('dist/'+host+'/lib/snapshot/s600519.js').includes(archiveMarker));
  }

  build(root,opts);
  const onlineReceipts=hosts.map(receipt),onlineIds=hosts.map(host=>config(host).buildId);
  for(const [index,host] of hosts.entries()){
    const removed=Object.keys(snapshots[index]).filter(name=>/^lib\/(snapshot|vendor)\//.test(name));
    assert.ok(removed.includes('lib/vendor/tiny-inflate.js') && removed.includes('lib/snapshot/meta.js'));
    for(const name of removed){
      assert.ok(!fs.existsSync(path.join(root,'dist',host,name)),host+' still contains '+name);
      assert.ok(!Object.hasOwn(onlineReceipts[index],name));
    }
    for(const name of Object.keys(onlineReceipts[index])){
      const bytes=fs.readFileSync(path.join(root,'dist',host,name));
      assert.ok(!bytes.includes(fundMarker) && !bytes.includes(archiveMarker),host+' leaked an offline marker in '+name);
    }
    const adapter=exported('dist/'+host+'/lib/offline.js');
    assert.equal(Object.keys(adapter.meta).length,0);assert.throws(()=>adapter.query('broad/meta'));
  }

  write('src/lib/snapshot/funds.js','module.exports="changed-fund-rows";');
  write('src/lib/snapshot/s600519.js','module.exports="changed-close-series";');
  write('src/lib/snapshot/meta.js','module.exports={end:"2026-11-11"};');
  assert.ok(build(root,opts).every(result=>result.changed===0));
  assert.deepEqual(hosts.map(receipt),onlineReceipts);
  assert.deepEqual(hosts.map(host=>config(host).buildId),onlineIds);

  write('src/pages/broad/index.js','Page({open(){return "updated";}});');
  assert.ok(build(root,opts).every(result=>result.changed>0));
  for(const [index,host] of hosts.entries()){
    assert.notEqual(config(host).buildId,onlineIds[index]);
    assert.equal(config(host).appVersion,'1.1.0','appVersion is the declared release version');
    assert.notEqual(receipt(host)['pages/broad/index.js'],onlineReceipts[index]['pages/broad/index.js']);
  }
});

test('keeps IDE AppID and unrelated files, and preflights manual edits before writing either host tree',t=>{
  const {root,read,write}=fixture(t);build(root);write('dist/xiaohongshu/notes.txt','keep this file');
  const project=JSON.parse(read('dist/xiaohongshu/project.config.json'));project.appid='edited_in_ide';project.setting.editorPreferences={tabSize:4};write('dist/xiaohongshu/project.config.json',JSON.stringify(project));
  build(root);assert.equal(read('dist/xiaohongshu/notes.txt'),'keep this file');const preserved=JSON.parse(read('dist/xiaohongshu/project.config.json'));assert.equal(preserved.appid,'edited_in_ide');assert.equal(preserved.setting.editorPreferences.tabSize,4);
  write('dist/xiaohongshu/pages/stock/index.js','// developer changed this file');const old=read('dist/wechat/lib/config.js');
  assert.throws(()=>build(root,options(['--mode','local'])),/未覆盖/);assert.equal(read('dist/wechat/lib/config.js'),old);assert.equal(read('dist/xiaohongshu/pages/stock/index.js'),'// developer changed this file');
});

test('rejects missing module and dangling UI handler before creating a package',t=>{
  const {root,write}=fixture(t);write('src/lib/data.js',"require('./missing');");assert.throws(()=>build(root),/模块无法解析/);assert(!fs.existsSync(path.join(root,'dist')));
  write('src/lib/data.js',"require('./config');");write('src/pages/stock/index.wxml','<button bindtap="missingHandler">查看</button>');assert.throws(()=>build(root),/模板事件未实现/);
});
