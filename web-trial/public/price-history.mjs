import {renderTimeChart, exactAmount} from './charts.mjs';
import {latestOnly,revealSection} from './logic.mjs';

const $=id=>document.getElementById(id), guard=latestOnly(), cache=new Map();
let stock=null, data=null, page=1, cleanup=null;
const validDate=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&Number.isFinite(Date.parse(value+'T00:00:00Z'))&&new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value;
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(cls)n.className=cls;return n;};
const firstDate=()=>validDate(stock?.stock_listed_date)?(stock.stock_listed_date<'1990-01-01'?'1990-01-01':stock.stock_listed_date):'1990-01-01';
function clear(message=''){
  guard.next();data=null;page=1;if(cleanup)cleanup();cleanup=null;
  $('price-history-chart').replaceChildren();$('price-history-body').replaceChildren();$('price-history-status').replaceChildren(node('span',message));
  $('price-history-prev').disabled=true;$('price-history-next').disabled=true;$('price-history-page').textContent='第 1 页';
}
export function beginPriceHistoryStock(code){
  if(stock?.stock_code===code)return;
  stock=null;clear();$('price-history-content').hidden=true;$('price-history-open').disabled=true;
}
export function setPriceHistoryStock(value){
  const end=value?.trade_date||value?.movement?.window_end;
  if(!/^\d{6}$/.test(value?.stock_code||'')||!validDate(end))return;
  const changed=stock?.stock_code!==value.stock_code||stock?.end!==end;
  stock={stock_code:value.stock_code,stock_name:value.stock_name||value.stock_code,stock_listed_date:validDate(value.stock_listed_date)?value.stock_listed_date:null,end};
  $('price-history-open').disabled=false;
  if(changed){clear();$('price-history-content').hidden=true;$('price-history-start').value=firstDate();$('price-history-end').value=end;}
  for(const id of ['price-history-start','price-history-end']){$(id).min=firstDate();$(id).max=end;}
}
function renderTable(){
  const body=$('price-history-body');body.replaceChildren();if(!data)return;
  const byDate=new Map(data.series.map(row=>[row.trade_date,row.close])),dates=data.trading_dates.slice().reverse(),pages=Math.max(1,Math.ceil(dates.length/50));page=Math.min(page,pages);
  for(const day of dates.slice((page-1)*50,page*50)){const row=node('tr');row.append(node('td',day),node('td',byDate.has(day)?exactAmount(byDate.get(day),'元/股',false,2):'—','num'));body.append(row);}
  $('price-history-page').textContent='第 '+page+' / '+pages+' 页 · '+dates.length+' 个开市日';$('price-history-prev').disabled=page<=1;$('price-history-next').disabled=page>=pages;
}
function render(){
  if(!data)return;
  if(cleanup)cleanup();
  cleanup=renderTimeChart($('price-history-chart'),data.series.map(row=>({date:row.trade_date,value:row.close})),{
    instrument:data.stock_code,start:data.start,end:data.end,tradingDates:data.trading_dates,preserveHistoricalSessions:true,kind:'line',unit:'元/股',signed:false,directional:false,decimals:2,label:'不复权日收盘价',
    note:'按来源日历中已核验的开市日展示；停牌或来源缺失留空，休市日不占位置。'
  });
  renderTable();
  const listed=data.stock_listed_date||stock.stock_listed_date,actual=data.series.length?data.series[0].trade_date+' 至 '+data.series.at(-1).trade_date:'暂无可用记录',missing=data.trading_dates.length-data.series.length;
  const excluded=Number.isSafeInteger(data.source_calendar_excluded_count)&&data.source_calendar_excluded_count>0?data.source_calendar_excluded_count:0;
  const text='来源：'+(data.source_label||'腾讯不复权日K')+'。'+(listed?'已核验上市日 '+listed:'上市日期尚未核验，查询从 1990 年起的来源可用记录')+'；所选范围 '+data.start+' 至 '+data.end+'；实际股价 '+actual+'，共 '+data.series.length+' 条。'+(missing>0?'有 '+missing+' 个开市日没有成交收盘记录，可能为停牌或来源缺失。':'')+(excluded?'另有 '+excluded+' 条源日期与当前交易日历不一致，待核验暂未绘图。':'')+'不复权价格会因除权除息跳变。此图独立于 ETF 资金历史。';
  const status=$('price-history-status');status.replaceChildren(node('span',text));
  if(data.status!=='ready'){const retry=node('button','重新读取股价');retry.type='button';retry.addEventListener('click',()=>load(true));status.append(' ',retry);}
}
async function load(force=false){
  if(!stock)return;
  const start=$('price-history-start').value,end=$('price-history-end').value,current=stock;
  if(!validDate(start)||!validDate(end)||start<firstDate()||start>end||end>stock.end){clear('请选择 '+firstDate()+' 至 '+stock.end+' 内的有效日期范围。');return;}
  clear('正在读取 '+start+' 至 '+end+' 的真实收盘价…');$('price-history-content').hidden=false;
  const sequence=guard.next(),key=[stock.stock_code,start,end].join('|');
  try{
    let result=force?null:cache.get(key);
    if(!result){
      const response=await fetch('/api/v1/prices/stock/'+stock.stock_code+'?'+new URLSearchParams({start,end}),{headers:{Accept:'application/json'}});
      let body;try{body=await response.json();}catch{throw Error('股价响应无法读取，请稍后重试');}
      if(!response.ok||body.code!==0)throw Error(body.message||'股价暂不可用');result=body.data;
      if(result?.stock_code!==current.stock_code||result.start!==start||result.end!==end||result.adjustment!=='none'||result.price_unit!=='元/股'||!Array.isArray(result.series)||result.series.length>12000||!Array.isArray(result.trading_dates)||result.trading_dates.length>12000)throw Error('股价身份、日期范围或交易日历未通过核验');
      let previous='';const days=new Set();
      for(const day of result.trading_dates){if(!validDate(day)||day<start||day>end||day<=previous)throw Error('股价交易日历未通过核验');days.add(day);previous=day;}
      previous='';for(const row of result.series){if(!days.has(row.trade_date)||row.trade_date<=previous||!Number.isFinite(row.close)||row.close<=0)throw Error('股价记录未通过核验');previous=row.trade_date;}
      if(result.stock_listed_date!=null&&!validDate(result.stock_listed_date))throw Error('上市日期未通过核验');
      if(current.stock_listed_date&&result.stock_listed_date&&current.stock_listed_date!==result.stock_listed_date)throw Error('上市日期已变化，请刷新数据');
      if(result.series.length&&result.status==='ready'){cache.set(key,result);if(cache.size>4)cache.delete(cache.keys().next().value);}
    }
    if(!guard.current(sequence)||stock?.stock_code!==current.stock_code||stock?.end!==current.end)return;
    data=result;render();
  }catch(error){
    if(!guard.current(sequence)||stock?.stock_code!==current.stock_code)return;
    const box=$('price-history-status');box.replaceChildren(node('span',(error?.message||'股价暂不可用')+'；ETF 资金数据不受影响。'));
    const retry=node('button','重试');retry.type='button';retry.addEventListener('click',()=>load(true));box.append(' ',retry);
  }
}
export function refreshPriceHistory(){cache.clear();if(stock&&!$('price-history-content').hidden)load(true);loadPriceCoverage();}
async function loadPriceCoverage(){
  const box=$('stock-price-coverage');if(!box)return;
  try{
    const response=await fetch('/local/price-coverage',{headers:{Accept:'application/json'}});if(!response.ok)throw Error();
    const result=await response.json(),s=result?.summary;
    if(result?.schema!==1||result.kind!=='listed-stock-price-coverage'||!s||!['stocks','stocks_with_data','stocks_from_listing','rows'].every(k=>Number.isSafeInteger(s[k])&&s[k]>=0)||!validDate(result.target_end))throw Error();
    box.textContent=(result.provider==='eastmoney'?'东方财富':'腾讯')+'个股行情采集：已有行情 '+s.stocks_with_data+' / '+s.stocks+' 只，最早记录到达上市日 '+s.stocks_from_listing+' 只，共 '+s.rows.toLocaleString('zh-CN')+' 条；目标日 '+result.target_end+'。'+(result.universe_complete===false?'北交所目录尚未核验，当前范围不能视为全市场完整覆盖。':'')+' 起止到达不等于中间日期完整。';box.hidden=false;
  }catch{box.hidden=true;box.textContent='';}
}
$('price-history-open')?.addEventListener('click',()=>{if(!stock)return;$('price-history-start').value=firstDate();$('price-history-end').value=stock.end;load();});
$('price-history-apply')?.addEventListener('click',()=>load());
$('price-history-prev')?.addEventListener('click',()=>{if(page>1){page--;renderTable();revealSection('price-history-rows-heading');}});
$('price-history-next')?.addEventListener('click',()=>{if(data&&page*50<data.trading_dates.length){page++;renderTable();revealSection('price-history-rows-heading');}});
loadPriceCoverage();
