const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../src',name),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));

function setup(platform='wechat',options={}){
  const events={},dialogs=[],counts={gets:0,applied:0,requests:0,transitions:[]};
  const manager={onCheckForUpdate:fn=>{events.check=fn;},onUpdateReady:fn=>{events.ready=fn;},
    onUpdateFailed:fn=>{events.fail=fn;},applyUpdate:()=>{counts.applied++;if(options.throwOnApply)throw Error('native failure');}};
  const host={getUpdateManager:()=>{counts.gets++;return options.partial?{applyUpdate(){}}:manager;},showModal:dialog=>dialogs.push(dialog)};
  if(options.unsupported)host.canIUse=()=>false;
  if(options.noModal)delete host.showModal;
  const files={};let app,page;
  function load(file,text,extra){
    const module={exports:{}};
    vm.runInNewContext(text,{module,require:k=>{if(!Object.hasOwn(files,k))throw Error('Unexpected require '+k);return files[k];},...extra},{filename:file});
    return module.exports;
  }
  let bridge=read('lib/platform.js');if(platform==='xiaohongshu')bridge=bridge.replace('const host = wx;','const host = xhs;').replace("const platformName = 'wechat';","const platformName = 'xiaohongshu';");
  files['./platform']=load('lib/platform.js',bridge,{[platform==='wechat'?'wx':'xhs']:host});
  const updates=load('lib/updates.js',read('lib/updates.js'));
  files['./lib/updates']=updates;files['../../lib/updates']=updates;
  files['../../lib/config']=options.config||{appVersion:'1.1.0',buildId:'cafe00001234'};
  const info=options.info||{mode:'snapshot',target_end:'2026-11-10',paired:false};
  files['../../lib/data']={sourceInfo:()=>info,get:(...args)=>{counts.requests++;return options.get(...args);},pair:async()=>{info.paired=true;}};
  load('app.js',read('app.js'),{App:obj=>app=obj});load('pages/about/index.js',read('pages/about/index.js'),{Page:obj=>page=obj});
  const about={...page,data:structuredClone(page.data),setData(value){Object.assign(this.data,value);}};
  updates.subscribe(state=>counts.transitions.push(state.status));
  return {app,about,host,events,manager,dialogs,counts,updates,info};
}

for(const platform of ['wechat','xiaohongshu']){
  test(platform+' waits for consent and permits deferred restart from the version page',async()=>{
    const {app,about,events,dialogs,counts}=setup(platform);
    app.onLaunch();app.onLaunch();assert.equal(counts.gets,1);
    events.check({hasUpdate:false});assert.equal(counts.transitions.at(-1),'current');
    events.check({hasUpdate:true});assert.equal(counts.transitions.at(-1),'downloading');
    events.ready();assert.equal(dialogs.length,0,'foreground presentation waits for onShow');
    app.onShow();assert.equal(dialogs.length,1);assert.equal(dialogs[0].showCancel,true);assert.equal(counts.applied,0);
    dialogs[0].success({confirm:false,cancel:true});await tick();events.ready();app.onHide();app.onShow();
    assert.equal(dialogs.length,1,'defer does not nag on each tab/foreground');assert.equal(counts.applied,0);
    await about.onShow();assert.equal(about.data.update.canRestart,true);assert.equal(about.data.appVersion,'1.1.0');
    assert.equal(about.data.buildId,'cafe00001234');assert.equal(about.data.dataDate,'2026-11-10');assert.equal(counts.requests,0);
    about.applyReady();about.applyReady();assert.equal(counts.applied,1);assert.equal(about.data.update.status,'applying');
  });
}
test('explicit positive modal confirmation applies exactly once; stale confirmation after download failure never applies',async()=>{
  for(const fail of [false,true]){
    const {app,about,events,dialogs,counts,updates}=setup();app.onLaunch();app.onShow();await about.onShow();
    events.ready();if(fail)events.fail();dialogs[0].success({confirm:true});await tick();assert.equal(counts.applied,fail?0:1);
    if(fail){assert.equal(about.data.update.canRestart,false);assert.match(about.data.update.message,/检查网络.*重新打开/);}
    about.onHide();const before=about.data.update.status;events.check({hasUpdate:true});assert.equal(about.data.update.status,before);
    assert.equal(updates.snapshot().status,fail?'downloading':'applying');about.onUnload();
  }
});
test('unavailable methods and failed apply provide a recovery instruction and do not force another restart',async()=>{
  for(const options of [{unsupported:true},{partial:true}]){
    const {app,about,counts}=setup('xiaohongshu',options);app.onLaunch();app.onShow();await about.onShow();
    assert.equal(about.data.update.status,'unsupported');assert.match(about.data.update.message,/重新打开/);about.applyReady();assert.equal(counts.applied,0);
  }
  const {app,about,events,counts}=setup('wechat',{noModal:true,throwOnApply:true});app.onLaunch();app.onShow();events.ready();await tick();
  assert.equal(counts.applied,0);await about.onShow();about.applyReady();assert.equal(about.data.update.status,'applyFailed');
  assert.match(about.data.update.message,/重新打开/);about.applyReady();assert.equal(counts.applied,1);
});
test('online/local market dates come from verified metadata, ignore stale reads and remain independent of the package version',async()=>{
  const pending=[];const {app,about,counts}=setup('wechat',{info:{mode:'live',paired:false},
    get:route=>{assert.equal(route,'market/meta');return new Promise(resolve=>pending.push(resolve));}});
  app.onLaunch();await about.onShow();assert.equal(about.data.dataDate,'连接后显示');assert.equal(counts.requests,0);
  about.inputCode({detail:{value:'12345678'}});const connect=about.connect();await tick();assert.equal(counts.requests,1);
  about.onHide();const fresh=about.onShow();pending[1]({target_end:'2026-11-11'});await fresh;
  pending[0]({target_end:'2026-11-01'});await connect;assert.equal(about.data.dataDate,'2026-11-11');assert.equal(about.data.appVersion,'1.1.0');
  const invalid=about.refreshSource();pending[2]({latest_date:'2026-02-31'});await invalid;
  assert.equal(about.data.dateRetry,true);assert.equal(about.data.dataDate,'暂未取得');
  const retry=about.refreshSource();pending[3]({latest_date:'2026-11-12'});await retry;assert.equal(about.data.dataDate,'2026-11-12');
});
