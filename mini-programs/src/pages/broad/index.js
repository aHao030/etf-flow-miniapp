const { get, sourceInfo } = require('../../lib/data');
const { nav, shareRoute } = require('../../lib/platform');
const { money, number, periodStart } = require('../../lib/format');
const { chartModel } = require('../../lib/chart');
const { fundDirections, fundMatcher } = require('../../lib/fund-list');

const PAGE_SIZE = 10;
const PERIODS = [{id:'1',name:'1个月'}, {id:'3',name:'3个月'}, {id:'12',name:'1年'}, {id:'60',name:'5年'}, {id:'all',name:'全部'}];
const SECTIONS = [{id:'chart',name:'走势'}, {id:'members',name:'所属 ETF'}, {id:'off',name:'场外报告'}, {id:'daily',name:'逐日'}];
const UNITS = ['auto','wan','yi'];
const finite = Number.isFinite;
const tone = value => value > 0 ? 'pos' : value < 0 ? 'neg' : '';
const count = value => Number.isInteger(value) && value >= 0 ? String(value) : '待核验';
const signedPercent = value => (value > 0 ? '+' : '') + number(value, 2) + '%';

function validDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(value + 'T00:00:00Z');
  return finite(date.getTime()) && date.toISOString().slice(0,10) === value;
}
function validRange(start,end,meta) {
  return validDate(start) && validDate(end) && start <= end && start >= meta.target_start && end <= meta.target_end;
}
function presetStart(meta,period) {
  return period === 'all' ? meta.target_start : periodStart(meta.target_end,Number(period),meta.target_start);
}
function availablePeriods(meta) {
  if(!meta.offline)return PERIODS;
  const seen=new Set();
  return PERIODS.filter(period=>{
    if(period.id!=='1' && period.id!=='3')return false;
    const start=presetStart(meta,period.id);
    if(seen.has(start))return false;
    seen.add(start);return true;
  });
}
function checkResult(result,meta,code,start,end) {
  if (!result || result.version !== meta.version || (meta.history_version && result.history_version!==meta.history_version) || !result.index || result.index.code !== code || result.start !== start || result.end !== end) {
    throw Error('快照或查询范围已变化，请刷新后重试。');
  }
  if (!Array.isArray(result.members) || !Array.isArray(result.series) || !Array.isArray(result.trading_dates) || result.series.length !== result.trading_dates.length) {
    throw Error('指数资金与交易日历不一致，请重试。');
  }
  let previous = '';
  for (let i=0; i<result.series.length; i++) {
    const row=result.series[i], day=row && row.trade_date;
    if (!validDate(day) || day < start || day > end || day <= previous || day !== result.trading_dates[i]) throw Error('交易日序列与所选日期不一致。');
    previous=day;
  }
}
function benchmark(result) {
  const rows=result.series.filter(row=>finite(row.index_close) && row.index_close>0);
  const name=(result.index.benchmark_name || result.index.name || '指数') + (result.index.code==='all'?'（参考基准）':'');
  if (!rows.length) return {name,quote:'区间暂无可核验收盘点位',span:'',change:'',tone:''};
  const first=rows[0], last=rows[rows.length-1];
  if (rows.length===1) return {name,quote:number(last.index_close,2)+' 点',span:last.trade_date+' · 仅有一个收盘日',change:'',tone:''};
  const change=(last.index_close/first.index_close-1)*100;
  return {name,quote:number(last.index_close,2)+' 点',span:first.trade_date+' 至 '+last.trade_date,change:signedPercent(change),tone:tone(change)};
}
function overview(result,unit) {
  const rows=result.series, observed=rows.filter(row=>finite(row.observed_flow));
  const eligible=rows.filter(row=>row.expected_funds>0 || row.unknown_lifecycle_funds>0);
  const complete=eligible.length>0 && eligible.every(row=>finite(row.estimated_flow));
  const total=observed.length?observed.reduce((sum,row)=>sum+row.observed_flow,0):null;
  const last=rows[rows.length-1], lastObserved=observed[observed.length-1];
  let ending='所选期间无交易日';
  if (last) {
    if (last.expected_funds===0) ending=last.unknown_lifecycle_funds?'成员存续期待核验':'无已核验的应有成员';
    else if (last.valid_funds===0 && last.expected_funds>0) ending='应有 '+last.expected_funds+' 只，当日暂无估算';
    else ending='当日可估算 '+count(last.valid_funds)+' / '+count(last.expected_funds)+' 只';
    ending=last.trade_date+' · '+ending;
  }
  return {
    amount:money(total,unit),tone:tone(total),direction:total>0?'净流入':total<0?'净流出':'',
    start:result.start,end:result.end,recent:lastObserved?lastObserved.trade_date:'暂无',
    coverage:observed.length+' / '+rows.length+' 个交易日有值',
    complete:complete?money(eligible.reduce((sum,row)=>sum+row.estimated_flow,0),unit):rows.length?'资料不足':'无交易日',
    ending,memberCount:result.members.length,partial:!complete && observed.length>0,
    lastDate:last?.trade_date||'',lastAmount:finite(last?.observed_flow)?money(last.observed_flow,unit):'暂无估算',
    lastTone:tone(last?.observed_flow)
  };
}
function pageCount(total) { return Math.max(1,Math.ceil(total/PAGE_SIZE)); }
function memberList(result,query,direction,page,unit) {
  const matchesFund=fundMatcher(query,direction);
  const matches=[];
  for(const row of result.members){
    const scoped=row.period_start===result.start && row.period_end===result.end;
    const value=scoped && finite(row.period_observed_flow)?row.period_observed_flow:null;
    if(matchesFund(row.fund_code,row.fund_name,value))matches.push({code:row.fund_code,name:row.fund_name||row.fund_code,value,
      coverage:scoped?count(row.period_valid_days)+' / '+count(row.period_expected_days)+' 日有值':'所选期间覆盖待核验'});
  }
  matches.sort((a,b)=>a.value===null&&b.value!==null?1:b.value===null&&a.value!==null?-1:Math.abs(b.value)-Math.abs(a.value)||String(a.code).localeCompare(String(b.code)));
  const pages=pageCount(matches.length), current=Math.max(1,Math.min(page,pages));
  const visible=matches.slice((current-1)*PAGE_SIZE,current*PAGE_SIZE).map(row=>({...row,amount:money(row.value,unit),tone:tone(row.value)}));
  return {memberRows:visible,memberPage:current,memberPages:pages,memberTotal:matches.length};
}
function dailyList(result,page,unit) {
  const rows=result.series,pages=pageCount(rows.length),current=Math.max(1,Math.min(page,pages));
  const end=Math.max(0,rows.length-(current-1)*PAGE_SIZE);
  const visible=rows.slice(Math.max(0,end-PAGE_SIZE),end).reverse().map(row=>({
    date:row.trade_date,amount:money(row.observed_flow,unit),tone:tone(row.observed_flow),
    complete:finite(row.estimated_flow)?money(row.estimated_flow,unit):'资料不足',
    coverage:count(row.valid_funds)+' / '+count(row.expected_funds)+' 只可估算',
    unknown:row.unknown_lifecycle_funds?row.unknown_lifecycle_funds+' 只成员的存续期待核验':'',
    close:finite(row.index_close)?number(row.index_close,2)+' 点':'—'
  }));
  return {dailyRows:visible,dailyPage:current,dailyPages:pages,dailyTotal:rows.length};
}
function offList(off) {
  if (!off || !Array.isArray(off.reports) || !Number.isInteger(off.total) || off.total<0 || !Number.isInteger(off.page_size) || off.page_size<=0 || !Number.isInteger(off.page) || off.page<1) throw Error('场外报告页暂不可读，请重试。');
  const members=new Map((off.members||[]).map(member=>[member.fund_code,member]));
  const shares=value=>finite(value)?number(value,2)+' 份':'—';
  return {
    offRows:off.reports.map((row,i)=>{
      const member=members.get(row.fund_code)||{};
      return {key:String(off.page)+'-'+i,code:row.fund_code,name:row.fund_name||member.fund_name||row.fund_code,shareClass:row.share_class||'报告总份额',
        dates:(row.period_start||'起日待核验')+' 至 '+(row.period_end||'期末待核验'),
        bought:shares(row.subscription_shares),sold:shares(row.redemption_shares),net:shares(row.net_subscription_shares),ending:shares(row.end_shares),tone:tone(row.net_subscription_shares)};
    }),
    offPage:off.page,offPages:Math.max(1,Math.ceil(off.total/off.page_size)),offTotal:off.total,offBusy:false,offError:''
  };
}

Page({
  data: {
    periods:PERIODS,sections:SECTIONS,period:'3',section:'chart',unit:'auto',unitIndex:0,unitNames:['自动单位','万元','亿元'],
    indexNames:[],indexCodes:[],indexChoice:0,code:'000300',indexName:'沪深300',start:'',end:'',minDate:'',maxDate:'',
    dateFiltersOpen:false,coverageOpen:false,dateNotice:'',query:'',busy:true,catalogBusy:true,error:'',ready:false,summary:null,chart:null,quote:null,
    directions:fundDirections,memberDirection:'all',memberRows:[],memberPage:1,memberPages:1,memberTotal:0,dailyRows:[],dailyPage:1,dailyPages:1,dailyTotal:0,
    offRows:[],offPage:1,offPages:1,offTotal:0,offBusy:false,offError:''
  },
  onLoad(options = {}) {
    this._request=0;this._offRequest=0;
    const entry={};
    if (/^(all|\d{6})$/.test(options.code || '')) {entry.code=options.code;this._entryCode=options.code;}
    if (validDate(options.start) && validDate(options.end)) {
      entry.start=options.start;entry.end=options.end;entry.period='';
    }
    this.setData(entry);
    return this.refresh();
  },
  onShareAppMessage() {
    const result=this._result;
    if(!result)return {title:'宽基指数资金观察',path:'/pages/broad/index'};
    return shareRoute(result.index.name+' · 估算净申赎','/pages/broad/index',{code:result.index.code,start:result.start,end:result.end});
  },
  onHide() {this._wasHidden=true;},
  onShow() {
    if(!this._wasHidden)return;
    this._wasHidden=false;
    if(sourceInfo().mode!=='snapshot')return this.refresh({foreground:true});
  },
  onUnload() {this._request++;this._offRequest++;},
  clearResult() {
    this._result=null;this._offRequest++;
    this.setData({ready:false,summary:null,quote:null,chart:null,chartDay:null,memberRows:[],dailyRows:[],offRows:[],offBusy:false,offError:'',error:'',dateNotice:''});
  },
  async refresh(options={}) {
    const returnPage=options.foreground===true ? this.data.memberPage : 1;
    const oldMeta=this._meta, originalStart=this.data.start, originalEnd=this.data.end;
    const prior=this._applied, dirty=prior && (originalStart!==prior.start || originalEnd!==prior.end);
    const keepDraft=options.foreground===true && dirty && !this.data.error;
    const dates=keepDraft?prior:{start:originalStart,end:originalEnd};
    if(oldMeta && (!validDate(dates.start)||!validDate(dates.end)||dates.start>dates.end))return this.invalidRange();
    const requestedPeriod=dirty&&!keepDraft?'':this.data.period;
    const ticket=++this._request;
    this.clearResult();this.setData({busy:true,catalogBusy:true});
    try {
      const meta=await get('broad/meta');
      if (ticket!==this._request)return;
      if (!meta || !meta.version || !validDate(meta.target_start) || !validDate(meta.target_end) || meta.target_start>meta.target_end || !Array.isArray(meta.indexes) || !meta.indexes.length) throw Error('指数目录或覆盖日期缺失。');
      this._meta=meta;
      const options=[{code:'all',name:'全部已核验宽基'}].concat(meta.indexes.filter(item=>/^\d{6}$/.test(item.code)));
      const choice=Math.max(0,options.findIndex(item=>item.code===this.data.code)), item=options[choice];
      const periods=availablePeriods(meta);
      const period=requestedPeriod && !periods.some(item=>item.id===requestedPeriod)?periods[periods.length-1].id:requestedPeriod;
      const start=period?presetStart(meta,period):dates.start, end=period?meta.target_end:dates.end;
      this.setData({indexCodes:options.map(x=>x.code),indexNames:options.map(x=>x.name||x.code),indexChoice:choice,code:item.code,indexName:item.name||item.code,
        minDate:meta.target_start,maxDate:meta.target_end,...(keepDraft?{}:{start,end}),period,periods,catalogBusy:false});
      if(this._entryCode && !options.some(option=>option.code===this._entryCode)){
        this._entryCode=null;
        this.setData({busy:false,error:'当前数据源未收录链接中的指数，请重新选择指数。'});
        return;
      }
      this._entryCode=null;
      await this.fetchRange({start,end},returnPage);
    } catch (error) {
      if(ticket===this._request){this._meta=null;this.setData({busy:false,catalogBusy:false,error:error.message||'无法读取指数目录，请重试。'});}
    }
  },
  invalidRange() {
    this._request++;this.clearResult();
    this.setData({busy:false,catalogBusy:false,period:'',error:'请选择 '+this._meta.target_start+' 至 '+this._meta.target_end+' 之间的日期，开始日期应早于或等于结束日期。'});
  },
  async fetchRange(range=this.data,memberPage=1) {
    if(!this._meta)return this.refresh();
    const {code}=this.data, {start,end}=range, meta=this._meta;
    if(!validRange(start,end,meta))return this.invalidRange();
    this._applied={start,end};
    const ticket=++this._request;
    this.clearResult();this.setData({busy:true});
    try {
      const result=await get('broad/index/'+code,{start,end,off_page_size:PAGE_SIZE});
      if(ticket!==this._request)return;
      checkResult(result,meta,code,start,end);
      this._result=result;
      this.setData({busy:false,ready:true,quote:benchmark(result),memberPage,dailyPage:1});
      this.renderAmounts();
      this.updateDateNotice();
      this._offPageSize=PAGE_SIZE;this._offWanted=1;
      try {
        this.setData(offList(result.off_exchange));this._offPageSize=result.off_exchange.page_size;
      } catch(error) {this.setData({offPage:1,offPages:1,offTotal:0,offError:error.message});}
    } catch(error) {
      if(ticket===this._request){this.clearResult();this.setData({busy:false,error:error.message||'无法读取所选期间，请重试。'});}
    }
  },
  renderAmounts() {
    const result=this._result;if(!result)return;
    const {unit,query,memberPage,dailyPage}=this.data;
    const rows=result.series.map(row=>({date:row.trade_date,value:row.observed_flow,overlay:row.index_close}));
    this.setData(Object.assign({
      summary:overview(result,unit),
      chart:chartModel(rows,{scope:[result.index.code,result.start,result.end].join('/'),kind:'bar',unit:'元',label:'当日 ETF 估算净申赎小计',overlayLabel:this.data.quote.name,overlayUnit:'点',currency:true,moneyUnit:unit},reader=>{this._readDay=reader;}),
      chartDay:this.data.chartDay && this._readDay ? this._readDay(this.data.chartDay.date) : null
    },memberList(result,query,this.data.memberDirection,memberPage,unit),dailyList(result,dailyPage,unit)));
  },
  changeIndex(event) {
    const choice=Number(event.detail.value), code=this.data.indexCodes[choice];
    if(!code||this.data.catalogBusy)return;
    const applied=this._applied;
    const dirty=applied && (this.data.start!==applied.start || this.data.end!==applied.end);
    this.setData({indexChoice:choice,code,indexName:this.data.indexNames[choice],query:'',memberDirection:'all',period:dirty?'':this.data.period});
    return this.fetchRange();
  },
  inspectDay(event) {
    if(this.data.chart && this._readDay)this.setData({chartDay:this._readDay(event.detail.date)});
  },
  changePeriod(event) {
    const id=event.currentTarget.dataset.id;
    if(!this._meta||this.data.catalogBusy||!this.data.periods.some(p=>p.id===id))return;
    this.setData({period:id,start:presetStart(this._meta,id),end:this._meta.target_end});
    return this.fetchRange();
  },
  updateDateNotice() {
    const applied=this._applied, {start,end}=this.data;
    const changed=applied && (start!==applied.start || end!==applied.end);
    this.setData({dateNotice:changed?'日期待应用。'+(this._result?'当前结果为 '+this._result.start+' 至 '+this._result.end+'。':''):''});
  },
  toggleDateFilters() {this.setData({dateFiltersOpen:!this.data.dateFiltersOpen});},
  toggleCoverage() {this.setData({coverageOpen:!this.data.coverageOpen});},
  changeDate(event) {
    const field=event.currentTarget.dataset.field;
    if(field!=='start' && field!=='end')return;
    this.setData({[field]:event.detail.value});this.updateDateNotice();
  },
  applyDates() {
    if(this.data.catalogBusy)return;
    const applied=this._applied;
    if(!applied || this.data.start!==applied.start || this.data.end!==applied.end)this.setData({period:''});
    return this.fetchRange();
  },
  changeUnit(event) {
    const index=Number(event.detail.value);if(!UNITS[index])return;
    this.setData({unitIndex:index,unit:UNITS[index]});this.renderAmounts();
  },
  changeSection(event) {
    const section=event.currentTarget.dataset.id;
    if(SECTIONS.some(item=>item.id===section))this.setData({section});
  },
  searchMembers(event) {
    this.filterMembers(String(event.detail.value||'').slice(0,50),this.data.memberDirection);
  },
  selectMemberDirection(event) {
    const direction=event.currentTarget.dataset.id;
    if(fundDirections.some(item=>item.key===direction))this.filterMembers(this.data.query,direction);
  },
  resetMembers() {this.filterMembers('','all');},
  filterMembers(query,memberDirection) {
    if(this._result)this.setData({query,memberDirection,...memberList(this._result,query,memberDirection,1,this.data.unit)});
  },
  memberPage(event) {
    if(this._result)this.setData(memberList(this._result,this.data.query,this.data.memberDirection,this.data.memberPage+Number(event.currentTarget.dataset.step),this.data.unit));
  },
  dailyPage(event) {
    if(this._result)this.setData(dailyList(this._result,this.data.dailyPage+Number(event.currentTarget.dataset.step),this.data.unit));
  },
  openFund(event) {
    const result=this._result;if(!result)return;
    const code=String(event.currentTarget.dataset.code),member=result.members.find(row=>row.fund_code===code);
    if(!member || !/^\d{6}$/.test(code))return;
    const parameters={code,from:'broad',name:member.fund_name||code,start:result.start,end:result.end,unit:this.data.unit};
    const version=result.history_version || this._meta.history_version;
    if(version)parameters.history_version=version;
    nav('/pages/fund/index?'+Object.keys(parameters).map(key=>key+'='+encodeURIComponent(parameters[key])).join('&'));
  },
  reportPage(event) {
    const page=this.data.offPage+Number(event.currentTarget.dataset.step);
    if(!this.data.offBusy && page>=1 && page<=this.data.offPages)return this.fetchReports(page);
  },
  retryReports() {return this.fetchReports(this._offWanted||1);},
  async fetchReports(page) {
    const result=this._result;if(!result)return;
    const ticket=++this._offRequest, code=result.index.code, {start,end}=result;
    this._offWanted=page;this.setData({offBusy:true,offError:'',offRows:[]});
    try {
      const data=await get('broad/off/'+code,{start,end,page,page_size:this._offPageSize||PAGE_SIZE});
      if(ticket!==this._offRequest || this._result!==result)return;
      if(data.version!==result.version || data.start!==start || data.end!==end || data.page!==page)throw Error('场外披露的快照或期间已变化，请刷新指数。');
      this.setData(offList(data));
    } catch(error) {
      if(ticket===this._offRequest && this._result===result)this.setData({offBusy:false,offError:error.message||'场外记录暂不可用。'});
    }
  }
});
