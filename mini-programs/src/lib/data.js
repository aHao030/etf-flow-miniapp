'use strict';
const config=require('./config');
const platform=require('./platform');
const offline=require('./offline');
const allowed=/^(?:broad\/(?:meta|calendar|(?:index|off)\/(?:all|\d{6}))|history\/(?:meta|etf\/\d{6}(?:\/chart)?)|market\/(?:meta|stocks|stock\/\d{6})|prices\/stock\/\d{6})$/;
let session='';
function sourceInfo(){
  const m=offline.meta, snapshot=config.mode==='snapshot';
  return {mode:config.mode,label:snapshot?'离线快照':config.mode==='local'?'本机完整库':'在线完整库',
    target_start:snapshot?m.start:'',target_end:snapshot?m.end:'',stocks:snapshot?m.stocks:[],
    indices:snapshot?m.indices:null,broadFunds:snapshot?m.broadFunds:null,reports:snapshot?m.reports:null,paired:!!session};
}
async function pair(code){
  if(config.mode!=='live'||!/^\d{8}$/.test(code))throw Error('请输入本机显示的 8 位连接码。');
  const r=await platform.request({url:config.baseUrl+'/pair',method:'POST',data:{code},timeout:15000});
  if(r.statusCode!==200||typeof r.data?.session!=='string')throw Error('连接码失效或输入有误。');
  session=r.data.session;
}
async function get(route,params={}){
  if(!allowed.test(route))throw Error('查询路径非法');
  if(config.mode==='snapshot')return offline.query(route,params);
  if(config.mode==='live'&&!session)throw Error('请在底部「数据说明」输入本机连接码，再回到此页重试。');
  const qs=Object.keys(params).filter(k=>params[k]!=null).map(k=>encodeURIComponent(k)+'='+encodeURIComponent(params[k])).join('&');
  let r;
  const requestSession=session,header={'Cache-Control':'no-cache'};
  if(requestSession)header.Authorization='Bearer '+requestSession;
  try{r=await platform.request({url:config.baseUrl+'/'+route+(qs?'?'+qs:''),method:'GET',timeout:30000,header});}
  catch(e){throw Error(config.mode==='local'?'本机数据服务连接失败。请在同一台电脑打开 ETF 资金观察，再在开发者工具重试。':'数据服务暂不可达，请检查网络及平台请求域名配置。');}
  if(config.mode==='live'&&r.statusCode===401){
    if(session!==requestSession)throw Error('连接状态已更新，请重试。');
    session='';throw Error('连接已到期，请在「数据说明」重新连接。');
  }
  if(config.mode==='live'&&session!==requestSession)throw Error('连接状态已更新，请重试。');
  if(r.statusCode!==200||r.data?.code!==0||!r.data.data)throw Error(r.statusCode===403?'数据服务拒绝访问。':(typeof r.data?.message==='string'?r.data.message:'数据暂不可用，请重试。'));
  return r.data.data;
}
module.exports={get,sourceInfo,pair};
