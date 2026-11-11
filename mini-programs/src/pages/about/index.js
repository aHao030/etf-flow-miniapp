const api=require('../../lib/data');
const config=require('../../lib/config');
const updates=require('../../lib/updates');

function validDate(value){
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;
  const t=Date.parse(value+'T00:00:00Z');
  return Number.isFinite(t)&&new Date(t).toISOString().slice(0,10)===value;
}

Page({
  data:{info:{},code:'',message:'',busy:false,appVersion:config.appVersion||'版本号待确认',
    buildId:/^[a-f0-9]{12}$/i.test(config.buildId||'')?config.buildId:'尚未提供',
    update:updates.snapshot(),dataDate:'尚未取得',dateRetry:false},
  onShow(){
    this._visible=true;
    this.setData({busy:!!this._pairing});
    if(this._stopListening)this._stopListening();
    this._stopListening=updates.subscribe(update=>this.setData({update}));
    return this.refreshSource();
  },
  onHide(){this.stopListening();},
  onUnload(){this.stopListening();},
  stopListening(){
    this._visible=false;this._dateRequest=(this._dateRequest||0)+1;
    if(this._stopListening){this._stopListening();this._stopListening=null;}
  },
  applyReady(){updates.applyReady();},
  async refreshSource(){
    const id=this._dateRequest=(this._dateRequest||0)+1;
    const info=api.sourceInfo();
    this.setData({info,dateRetry:false});
    if(info.mode==='snapshot'){
      this.setData({dataDate:validDate(info.target_end)?info.target_end:'尚未取得'});return;
    }
    if(info.mode==='live'&&!info.paired){this.setData({dataDate:'连接后显示'});return;}
    this.setData({dataDate:'正在读取…'});
    try{
      const meta=await api.get('market/meta');
      if(!this._visible||id!==this._dateRequest)return;
      const day=validDate(meta?.target_end)?meta.target_end:meta?.latest_date;
      if(!validDate(day))throw Error('missing date');
      this.setData({dataDate:day});
    }catch(_){
      if(this._visible&&id===this._dateRequest)this.setData({dataDate:'暂未取得',dateRetry:true,info:api.sourceInfo()});
    }
  },
  inputCode(e){this.setData({code:String(e.detail.value||'')});},
  async connect(){
    if(this._pairing)return;
    this._pairing=true;this.setData({busy:true,message:''});
    try{
      await api.pair(this.data.code);
      if(this._visible){this.setData({code:'',message:'已连接。返回原页面后点击重试。'});await this.refreshSource();}
    }catch(e){if(this._visible)this.setData({message:e.message||'连接失败，请重试。'});}
    finally{this._pairing=false;if(this._visible)this.setData({busy:false});}
  }
});
