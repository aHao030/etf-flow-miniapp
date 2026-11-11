const api = require('../../lib/data');
const platform = require('../../lib/platform');
const {money, number} = require('../../lib/format');
const {chartModel} = require('../../lib/chart');

const PAGE_SIZE = 20;
const WATCH_KEY = 'etf-stock-watch-v1';
const PERIODS = [
  ['1d','单日'], ['5d','5日'], ['20d','20日'], ['3m','3个月'], ['6m','6个月'],
  ['1y','1年'], ['2y','2年'], ['3y','3年'], ['5y','5年'], ['all','全部历史']
].map(([key,label]) => ({key,label}));
const UNITS = [{key:'auto',label:'自动'}, {key:'wan',label:'万元'}, {key:'yi',label:'亿元'}];
const codeOK = value => typeof value === 'string' && /^\d{6}$/.test(value);
const tone = n => n > 0 ? 'pos' : n < 0 ? 'neg' : '';
const dateOK = d => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d) &&
  Number.isFinite(Date.parse(d+'T00:00:00Z')) && new Date(d+'T00:00:00Z').toISOString().slice(0,10) === d;
const eventValue = e => e.currentTarget.dataset.value;
const readableError = e => e && e.message ? String(e.message).slice(0,160) : '数据暂时无法读取，请重试。';
function readWatch() {
  try {
    const raw = platform.storageGet(WATCH_KEY);
    const saved = typeof raw === 'string' ? JSON.parse(raw) : raw;
    return Array.isArray(saved) ? saved.filter(r => r && codeOK(r.code) && typeof r.name === 'string').slice(0,100) : [];
  } catch (_) { return []; }
}
function datesValid(dates,start,end) {
  return Array.isArray(dates) && dates.length <= 12000 && dates.every((d,i) => dateOK(d) &&
    d >= start && d <= end && (i === 0 || d > dates[i-1]));
}
function stockRows(stock,code,period) {
  const m=stock && stock.movement;
  if (!m || stock.stock_code !== code || m.period !== period || !dateOK(m.window_start) || !dateOK(m.window_end) ||
      m.window_end !== stock.trade_date || !datesValid(m.trading_dates,m.window_start,m.window_end) ||
      !Array.isArray(stock.series) || stock.series.length > 12000 || !Array.isArray(stock.etf_contributions)) {
    throw Error('股票或资金日期范围未通过核验，请重新读取。');
  }
  const dates=new Set(m.trading_dates), byDate=new Map();
  for (const r of stock.series) {
    if (!r || !dates.has(r.trade_date) || byDate.has(r.trade_date) || !(r.flow === null || Number.isFinite(r.flow))) {
      throw Error('逐日资金记录未通过核验。');
    }
    byDate.set(r.trade_date,r.flow);
  }
  return m.trading_dates.map(date => ({date,value:byDate.has(date)?byDate.get(date):null}));
}
function marketIdentity(meta) {
  if (!meta || typeof meta.version!=='string' || !meta.version || typeof meta.history_version!=='string' || !meta.history_version || !dateOK(meta.target_end)) {
    throw Error('无法核验市场数据版本，请刷新。');
  }
  return {market:meta.version,history:meta.history_version,end:meta.target_end};
}
function sameMarket(a,b) {return a.market===b.market && a.history===b.history && a.end===b.end;}
function checkedPrices(d,code,start,end,listed,expected) {
  if(expected && (!d || d.market_version!==expected.market || d.history_version!==expected.history || d.target_end!==expected.end)){
    throw Error('资金与行情来自不同版本，请刷新个股后重试。');
  }
  if (!d || d.stock_code !== code || d.start !== start || d.end !== end || d.price_unit !== '元/股' ||
      d.adjustment !== 'none' || !datesValid(d.trading_dates,start,end) || !Array.isArray(d.series) ||
      d.series.length > 12000 || (listed && d.stock_listed_date !== listed)) {
    throw Error('股票或价格日期范围未通过核验，请重新读取。');
  }
  const dates = new Set(d.trading_dates), closes = new Map();
  let previous = '';
  for (const row of d.series) {
    if (!row || !dates.has(row.trade_date) || row.trade_date <= previous || !Number.isFinite(row.close) || row.close <= 0) {
      throw Error('逐日收盘记录未通过核验。');
    }
    previous=row.trade_date; closes.set(row.trade_date,row.close);
  }
  return {data:d,closes};
}
function pageOf(rows,page,reverse=false) {
  const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));
  const current=Math.min(pages,Math.max(1,page));
  const from=(current-1)*PAGE_SIZE, count=Math.min(PAGE_SIZE,rows.length-from);
  const items=Array.from({length:count},(_,i) => rows[reverse?rows.length-1-from-i:from+i]);
  return {items,current,pages,total:rows.length};
}
function priceLabel(result) {
  const d=result.data, first=d.series[0], last=d.series[d.series.length-1];
  const source=d.source_label || '公开日行情';
  const excluded=Number.isSafeInteger(d.source_calendar_excluded_count)?d.source_calendar_excluded_count:0;
  return source+' · '+(first?first.trade_date+' 至 '+last.trade_date+' · '+d.series.length+' 日有价':'所选范围暂无可绘制的收盘记录')+
    (d.trading_dates.length>d.series.length?'，'+(d.trading_dates.length-d.series.length)+' 个开市日缺价':'')+
    (excluded?'，另有 '+excluded+' 个来源日期待日历核验':'')+'。';
}

Page({
  data:{
    view:'search', query:'', searchState:'idle', searchMessage:'', searchRows:[], searchPage:1, searchMore:false,
    watch:[], sampleStocks:[], watchError:'', code:'', name:'', watched:false, period:'20d', periods:PERIODS, unit:'auto', units:UNITS,
    state:'idle', error:'', chart:null, priceState:'idle', priceNote:'', dailyOpen:false, fullOpen:false,
    daily:{items:[],current:1,pages:1}, funds:{items:[],current:1,pages:1}, top:[],
    lifeOpen:false, lifeState:'idle', lifeNote:'', lifeChart:null, lifeFrom:'', lifeTo:'', lifeDailyOpen:false,
    lifeDaily:{items:[],current:1,pages:1}
  },
  onLoad(options={}) {
    this._stockToken=0;this._searchToken=0;this._quoteToken=0;this._lifeToken=0;
    this._timer=null;this._stock=null;this._rows=[];this._funds=[];this._quotes=null;this._life=null;
    const info=api.sourceInfo();
    const sampleStocks=Array.isArray(info.stocks)?info.stocks.filter(row=>row&&codeOK(row.stock_code))
      .map(row=>({code:row.stock_code,name:row.stock_name||row.stock_code})):[];
    this.setData({watch:readWatch(),sampleStocks});
    if (codeOK(options.code)) {
      if(PERIODS.some(item=>item.key===options.period))this.setData({period:options.period});
      return this.openStock(options.code);
    }
  },
  onShareAppMessage() {
    if(this.data.view==='search'||!codeOK(this.data.code))return {title:'个股 ETF 资金观察',path:'/pages/stock/index'};
    return platform.shareRoute(this.data.name+' · ETF 持仓估算贡献','/pages/stock/index',{code:this.data.code,period:this.data.period});
  },
  onHide() {this._wasHidden=true;},
  onShow() {
    if(!this._wasHidden)return;
    this._wasHidden=false;
    this.setData({watch:readWatch()});
    if(api.sourceInfo().mode==='snapshot')return;
    if(this.data.view==='detail')return this.retryStock();
    if(this.data.query.trim())return this.submitSearch();
  },
  onUnload() {
    clearTimeout(this._timer);this._stockToken++;this._searchToken++;this._quoteToken++;this._lifeToken++;
  },
  inputSearch(e) {
    clearTimeout(this._timer);this._searchToken++;
    const query=String(e.detail.value || '').slice(0,50);
    this.setData({query,searchState:query.trim()?'loading':'idle',searchRows:[],searchPage:1,searchMore:false,searchMessage:''});
    if (query.trim()) this._timer=setTimeout(() => this.search(1),250);
  },
  submitSearch() {clearTimeout(this._timer);return this.search(1);},
  clearSearch() {this.inputSearch({detail:{value:''}});},
  changeSearchPage(e) {return this.search(this.data.searchPage+Number(eventValue(e)));},
  async search(page) {
    const q=this.data.query.trim(), token=++this._searchToken;
    if (!q || !Number.isInteger(page) || page<1) return;
    this.setData({searchState:'loading',searchMessage:'',searchRows:[]});
    try {
      const d=await api.get('market/stocks',{q,page,page_size:PAGE_SIZE});
      if (token!==this._searchToken) return;
      if (!d || !Array.isArray(d.items)) throw Error('暂时无法读取股票目录。');
      const rows=d.items.filter(r=>r&&codeOK(r.stock_code)).map(r=>({code:r.stock_code,name:r.stock_name||r.stock_code}));
      this.setData({searchState:rows.length?'ready':'empty',searchRows:rows,searchPage:page,searchMore:!!d.has_more});
    } catch(e) {if(token===this._searchToken)this.setData({searchState:'error',searchMessage:readableError(e)});}
  },
  chooseStock(e) {return this.openStock(String(e.currentTarget.dataset.code || ''));},
  backToSearch() {
    this._stockToken++;this._quoteToken++;this._lifeToken++;
    this.setData({view:'search',watch:readWatch(),watchError:''});
    if(this.data.query.trim()&&this.data.searchState==='loading')this.search(1);
  },
  choosePeriod(e) {
    const period=eventValue(e); if(!PERIODS.some(x=>x.key===period)||period===this.data.period)return;
    this.setData({period});return this.openStock(this.data.code);
  },
  chooseUnit(e) {
    const unit=eventValue(e);if(!UNITS.some(x=>x.key===unit)||unit===this.data.unit)return;
    this.setData({unit});if(this._stock)this.renderStock();
  },
  retryStock() {return this.openStock(this.data.code,true);},
  async openStock(code,refreshing=false) {
    if(!codeOK(code))return;
    clearTimeout(this._timer);this._searchToken++;this._quoteToken++;this._lifeToken++;
    const token=++this._stockToken, period=this.data.period;
    if(!refreshing)this._restoreLife=null;
    else if(this.data.lifeOpen)this._restoreLife={
      from:this.data.lifeFrom,to:this.data.lifeTo,custom:!!this._lifeCustom,pending:this.data.lifeState==='changed'
    };
    const priorLife=this._restoreLife;
    this._stock=null;this._market=null;this._rows=[];this._quotes=null;this._funds=[];this._life=null;
    this.setData({view:'detail',code,name:code,state:'loading',error:'',chart:null,chartDay:null,lifeDay:null,priceState:'idle',priceNote:'',watchError:'',
      dailyOpen:false,fullOpen:false,lifeOpen:false,lifeState:'idle',lifeDailyOpen:false,lifeChart:null,lifeNote:'',
      daily:{items:[],current:1,pages:1},funds:{items:[],current:1,pages:1},top:[]});
    try {
      const online=api.sourceInfo().mode!=='snapshot';
      const market=online?marketIdentity(await api.get('market/meta')):null;
      if(token!==this._stockToken)return;
      const d=await api.get('market/stock/'+code,{period});
      if(token!==this._stockToken)return;
      const rows=stockRows(d,code,period);
      if(market){
        if(d.trade_date!==market.end)throw Error('市场交易日已更新，请刷新个股。');
        // Some existing market snapshots return only trade_date on stock detail.
        // Reading its manifest on both sides also detects a same-day republication.
        const after=marketIdentity(await api.get('market/meta'));
        if(token!==this._stockToken)return;
        if(!sameMarket(market,after))throw Error('读取期间市场快照已更新，请刷新个股。');
      }
      this._rows=rows;this._stock=d;this._market=market;
      this._funds=d.etf_contributions.filter(r=>r&&codeOK(r.etf_code)).slice().sort((a,b)=>{
        if(!Number.isFinite(a.flow))return Number.isFinite(b.flow)?1:0;
        if(!Number.isFinite(b.flow))return -1;
        return Math.abs(b.flow)-Math.abs(a.flow);
      });
      const listed=dateOK(d.stock_listed_date)?d.stock_listed_date:null;
      this.setData({state:'ready', name:d.stock_name||code, asOf:d.trade_date, listed:listed||'',
        lifeMin:listed||'1990-01-01',lifeFrom:listed||'1990-01-01',lifeTo:d.trade_date,
        watched:this.data.watch.some(r=>r.code===code)});
      this.renderStock();this.loadOverlay();
      if(priorLife){
        this._restoreLife=null;
        this._lifeCustom=priorLife.custom;
        this.setData({lifeOpen:true,lifeFrom:priorLife.custom?priorLife.from:this.data.lifeMin,lifeTo:priorLife.custom?priorLife.to:d.trade_date});
        if(priorLife.pending)this.setData({lifeState:'changed',lifeNote:'请应用新范围。'});
        else this.loadLife();
      }
    } catch(e) {if(token===this._stockToken)this.setData({state:'error',error:readableError(e)});}
  },
  renderStock() {
    const d=this._stock,m=d.movement,unit=this.data.unit;
    const last=this._rows[this._rows.length-1];
    let recent=null;for(let i=this._rows.length-1;i>=0;i--){if(Number.isFinite(this._rows[i].value)){recent=this._rows[i].date;break;}}
    const valid=this._funds.filter(r=>Number.isFinite(r.flow));const top=valid.slice(0,10);
    const max=top.length?Math.max(1,Math.abs(top[0].flow)):1;
    const endValue=last?.date===m.window_end?last.value:null;
    this.setData({net:money(m.observed_net_flow,unit),netClass:tone(m.observed_net_flow),
      endAmount:Number.isFinite(endValue)?money(endValue,unit):'暂无估算',endTone:tone(endValue),endDate:m.window_end,
      recentDate:!Number.isFinite(endValue)?recent:'',
      windowText:m.window_start+(m.start_exclusive?'（不含）':'')+' 至 '+m.window_end,
      coverageText:'有资金记录 '+this._rows.filter(r=>Number.isFinite(r.value)).length+' / '+this._rows.length+' 个交易日',
      fundCount:this._funds.length,validFundCount:valid.length,
      top:top.map(r=>({code:r.etf_code,name:r.etf_name||r.etf_code,amount:money(r.flow,unit),tone:tone(r.flow),width:100*Math.abs(r.flow)/max})),
      funds:this.fundPage(this.data.funds.current)});
    this.renderCombined();
  },
  renderCombined() {
    const unit=this.data.unit, quotes=this._quotes;
    const rows=this._rows.map(r=>({date:r.date,value:r.value,overlay:quotes&&quotes.closes.has(r.date)?quotes.closes.get(r.date):null}));
    this.setData({chart:chartModel(rows,{kind:'bar',unit:'元',label:'ETF 估算净流入',currency:true,moneyUnit:unit,
      overlayLabel:'不复权收盘价',overlayUnit:'元/股'},reader=>{this._readDay=reader;}),
      chartDay:this.data.chartDay && this._readDay ? this._readDay(this.data.chartDay.date) : null,daily:this.dailyPage(this.data.daily.current)});
  },
  inspectDay(event){if(this.data.chart && this._readDay)this.setData({chartDay:this._readDay(event.detail.date)});},
  inspectLifeDay(event){if(this.data.lifeChart && this._readLifeDay)this.setData({lifeDay:this._readLifeDay(event.detail.date)});},
  async loadOverlay() {
    if(!this._stock)return;
    const token=++this._quoteToken, d=this._stock, m=d.movement;
    if(!this._rows.length){this.setData({priceState:'empty',priceNote:'所选范围没有已核验交易日。'});return;}
    if(this._quotes){this._quotes=null;this.renderCombined();}
    this.setData({priceState:'loading',priceNote:'正在读取收盘价…'});
    try {
      const p=await api.get('prices/stock/'+d.stock_code,{start:m.window_start,end:m.window_end});
      if(token!==this._quoteToken||d!==this._stock)return;
      const quote=checkedPrices(p,d.stock_code,m.window_start,m.window_end,d.stock_listed_date,this._market);
      if(this._market){
        const sessions=p.trading_dates.filter(day=>!m.start_exclusive || day>m.window_start);
        if(sessions.length!==this._rows.length || sessions.some((day,i)=>day!==this._rows[i].date))throw Error('资金与股价的交易日历已变化，请刷新个股。');
      }
      this._quotes=quote;
      this.setData({priceState:p.series.length?'ready':'empty',priceNote:priceLabel(this._quotes)});this.renderCombined();
    } catch(e) {if(token===this._quoteToken&&d===this._stock){this._quotes=null;this.setData({priceState:'error',priceNote:readableError(e)});this.renderCombined();}}
  },
  dailyPage(page) {
    const p=pageOf(this._rows,page,true), quotes=this._quotes;
    p.items=p.items.map(r=>({date:r.date,amount:money(r.value,this.data.unit),tone:tone(r.value),close:quotes&&quotes.closes.has(r.date)?number(quotes.closes.get(r.date),2):'—'}));return p;
  },
  toggleDaily(){this.setData({dailyOpen:!this.data.dailyOpen});},
  changeDaily(e){this.setData({daily:this.dailyPage(this.data.daily.current+Number(eventValue(e)))});},
  fundPage(page) {
    const p=pageOf(this._funds,page);
    p.items=p.items.map(r=>({code:r.etf_code,name:r.etf_name||r.etf_code,amount:money(r.flow,this.data.unit),tone:tone(r.flow),
      weight:Number.isFinite(r.weight)?number(r.weight*100,2)+'%':'—',weightDate:r.weight_date||'未取得',validDays:r.valid_days??'—'}));return p;
  },
  toggleFunds(){this.setData({fullOpen:!this.data.fullOpen});},
  changeFunds(e){this.setData({funds:this.fundPage(this.data.funds.current+Number(eventValue(e)))});},
  openFund(e){
    const code=e.currentTarget.dataset.code;
    const fund=this._funds.find(row=>row.etf_code===code);
    if(!codeOK(code)||!fund||!this._stock)return;
    const m=this._stock.movement;
    let start=m.window_start;
    if(m.start_exclusive){
      const next=new Date(start+'T00:00:00Z');
      next.setUTCDate(next.getUTCDate()+1);start=next.toISOString().slice(0,10);
    }
    const params={code,from:'stock',name:fund.etf_name||code,start,end:m.window_end,unit:this.data.unit};
    if(this._market)params.history_version=this._market.history;
    platform.nav('/pages/fund/index?'+Object.keys(params).map(k=>k+'='+encodeURIComponent(params[k])).join('&'));
  },
  toggleWatch() {
    if(!this._stock)return;
    const code=this.data.code,watch=readWatch(),had=watch.some(r=>r.code===code);
    if(!had&&watch.length>=100){this.setData({watchError:'自选已满 100 只，请先移除一只。'});return;}
    const next=had?watch.filter(r=>r.code!==code):[{code,name:this.data.name},...watch];
    try {if(platform.storageSet(WATCH_KEY,next)===false)throw Error();this.setData({watch:next,watched:!had,watchError:''});}
    catch(_){this.setData({watchError:'设备未能保存自选，请重试。'});}
  },
  openLife() {if(!this._stock)return;this._lifeCustom=false;this.setData({lifeOpen:true,lifeFrom:this.data.lifeMin,lifeTo:this.data.asOf});return this.loadLife();},
  closeLife() {this._lifeToken++;this._life=null;this.setData({lifeOpen:false,lifeState:'idle',lifeChart:null,lifeDailyOpen:false});},
  setLifeDate(e) {
    const key=e.currentTarget.dataset.field;if(!['lifeFrom','lifeTo'].includes(key))return;
    this._lifeToken++;this._life=null;this._lifeCustom=true;this.setData({[key]:e.detail.value,lifeChart:null,lifeState:'changed',lifeNote:'请应用新范围。',lifeDailyOpen:false,lifeDaily:{items:[],current:1,pages:1}});
  },
  async loadLife() {
    if(!this._stock)return;
    const d=this._stock,code=d.stock_code,start=this.data.lifeFrom,end=this.data.lifeTo,token=++this._lifeToken;
    this._life=null;this.setData({lifeChart:null,lifeDailyOpen:false,lifeDaily:{items:[],current:1,pages:1}});
    if(!dateOK(start)||!dateOK(end)||start<this.data.lifeMin||end>this.data.asOf||start>end){this.setData({lifeState:'error',lifeNote:'请选择上市范围内的有效日期，开始日期须早于或等于结束日期。'});return;}
    this.setData({lifeState:'loading',lifeNote:'正在读取所选期间的收盘价…'});
    try {
      const p=await api.get('prices/stock/'+code,{start,end});if(token!==this._lifeToken||this._stock!==d)return;
      const result=checkedPrices(p,code,start,end,d.stock_listed_date,this._market);this._life=result;
      const rows=p.trading_dates.map(date=>({date,value:result.closes.has(date)?result.closes.get(date):null}));
      this.setData({lifeState:'ready',lifeNote:priceLabel(result),lifeChart:chartModel(rows,{kind:'line',unit:'元/股',label:'不复权收盘价',currency:false},reader=>{this._readLifeDay=reader;}),lifeDay:null,lifeDaily:this.lifePage(1)});
    } catch(e){if(token===this._lifeToken&&this._stock===d)this.setData({lifeState:'error',lifeNote:readableError(e)});}
  },
  lifePage(page) {
    const p=pageOf(this._life.data.trading_dates,page,true);
    p.items=p.items.map(date=>({date,close:this._life.closes.has(date)?number(this._life.closes.get(date),2):'—'}));return p;
  },
  toggleLifeDaily(){if(this._life)this.setData({lifeDailyOpen:!this.data.lifeDailyOpen});},
  changeLifeDaily(e){if(this._life)this.setData({lifeDaily:this.lifePage(this.data.lifeDaily.current+Number(eventValue(e)))});}
});
