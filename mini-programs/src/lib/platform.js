'use strict';
// The build substitutes the host namespace. These APIs are shared by both native hosts.
const host = wx;
const platformName = 'wechat';
function request(options) {
  return new Promise((resolve,reject)=>host.request(Object.assign({},options,{success:resolve,fail:reject})));
}
function storageGet(key){try{return host.getStorageSync(key);}catch(e){return undefined;}}
function storageSet(key,value){host.setStorageSync(key,value);}
function nav(url){host.navigateTo({url});}
function back(){host.navigateBack({delta:1,fail:()=>host.switchTab({url:'/pages/broad/index'})});}
function shareRoute(title,path,fields){
  const query=Object.keys(fields).map(k=>encodeURIComponent(k)+'='+encodeURIComponent(fields[k])).join('&');
  return platformName==='xiaohongshu'?{title,path,query}:{title,path:query?path+'?'+query:path};
}
function getUpdateManager(){
  try {
    if(typeof host.getUpdateManager!=='function')return null;
    if(typeof host.canIUse==='function'&&host.canIUse('getUpdateManager')===false)return null;
    return host.getUpdateManager();
  } catch(_) {return null;}
}
function confirmRestart(){
  return new Promise(resolve=>{
    if(typeof host.showModal!=='function'){resolve(false);return;}
    try {
      host.showModal({title:'新版已就绪',content:'现在重启会结束当前查看。你也可以稍后在「数据说明」启用新版。',
        showCancel:true,confirmText:'立即重启',cancelText:'稍后',
        success:result=>resolve(result&&result.confirm===true),fail:()=>resolve(false)});
    } catch(_) {resolve(false);}
  });
}
module.exports={request,storageGet,storageSet,nav,back,shareRoute,platformName,getUpdateManager,confirmRestart};
