'use strict';
const platform=require('./platform');

const messages={
  waiting:'等待平台检查更新。重新打开小程序时，平台会检查已发布的版本。',
  current:'本次检查未发现新版本。',
  downloading:'平台正在下载新版，可以继续查看行情。',
  ready:'新版已下载。准备好时可以重启小程序。',
  applying:'正在重新启动小程序…',
  failed:'新版下载失败。请检查网络，退出小程序后重新打开。',
  applyFailed:'新版暂未启用。请退出小程序后重新打开。',
  unsupported:'当前环境未提供应用内更新。请退出小程序后重新打开，或升级手机上的客户端。'
};
let manager=null, started=false, visible=false, offered=false, generation=0, phase='waiting';
const observers=new Set();

function snapshot(){return {status:phase,message:messages[phase],canRestart:phase==='ready'};}
function publish(next){
  if(phase===next)return;
  phase=next;
  for(const fn of observers)fn(snapshot());
}
function subscribe(fn){observers.add(fn);fn(snapshot());return ()=>observers.delete(fn);}
function applyReady(){
  if(phase!=='ready'||!manager)return;
  generation++;
  publish('applying');
  try{
    const result=manager.applyUpdate();
    if(result&&typeof result.catch==='function')result.catch(()=>{if(phase==='applying')publish('applyFailed');});
  }catch(_){publish('applyFailed');}
}
function offerWhenVisible(){
  if(!visible||offered||phase!=='ready')return;
  offered=true;
  const ticket=generation;
  platform.confirmRestart().then(confirmed=>{
    if(confirmed===true&&visible&&ticket===generation)applyReady();
  });
}
function start(){
  if(started)return;
  started=true;
  const m=platform.getUpdateManager();
  if(!m||typeof m.onUpdateReady!=='function'||typeof m.applyUpdate!=='function'){publish('unsupported');return;}
  manager=m;
  try{
    if(typeof m.onCheckForUpdate==='function')m.onCheckForUpdate(result=>{
      if(manager!==m||phase==='ready'||phase==='applying'||!result||typeof result.hasUpdate!=='boolean')return;
      publish(result.hasUpdate?'downloading':'current');
    });
    if(typeof m.onUpdateFailed==='function')m.onUpdateFailed(()=>{
      if(manager!==m)return;
      generation++;offered=false;publish('failed');
    });
    m.onUpdateReady(()=>{
      if(manager!==m||phase==='ready'||phase==='applying')return;
      generation++;offered=false;publish('ready');offerWhenVisible();
    });
  }catch(_){manager=null;generation++;publish('unsupported');}
}
function foreground(){visible=true;start();offerWhenVisible();}
function background(){visible=false;}
module.exports={start,foreground,background,subscribe,snapshot,applyReady};
