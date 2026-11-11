import {renderTimeChart,exactAmount} from './charts.mjs';
import {money, latestOnly, onMoneyUnitChange, revealSection} from './logic.mjs';
const $=id=>document.getElementById(id);
const guard=latestOnly(), chartGuard=latestOnly(),offGuard=latestOnly();
let meta=null, indexCode='000300', selected=null, period=3, metric='flow', data=null, offPage=1, customRange=null;
let lastChart=null, chartCleanup=null, loading=false, dailyPage=1, memberPage=1;
const memberPageSize=20;
let rangeMessage='', rangeRejected=false;
function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function tone(value){return value>0?'positive':value<0?'negative':'';}
async function api(path,params={}){
  const response=await fetch('/api/v1/'+path+'?'+new URLSearchParams(params),{headers:{Accept:'application/json'}});
  let body;try{body=await response.json();}catch{throw Error('数据暂未能读取，请刷新重试');}
  if(!response.ok||body.code!==0)throw Error(body.message||'数据读取失败');return body.data;
}
function show(){
  for(const name of ['history','stock','broad']){$(name+'-view').hidden=name!=='broad';$(name+'-tab')?.classList.toggle('active',name==='broad');$(name+'-tab')?.setAttribute('aria-pressed',String(name==='broad'));}
  if(!meta&&!loading)load();
}
function clearChart(){lastChart=null;if(chartCleanup)chartCleanup();chartCleanup=null;$('broad-chart').replaceChildren();}
function renderCachedChart(){if(!lastChart)return;if(chartCleanup)chartCleanup();chartCleanup=renderTimeChart($('broad-chart'),lastChart.rows,lastChart.options);}
function selectedIndex(){return indexCode==='all'?{code:'all',name:'全部已核验宽基',available_start:meta.target_start}:meta.indexes.find(x=>x.code===indexCode);}
function startDate(){
  if(customRange)return customRange.start;
  if(period===0)return meta.target_start;
  const d=new Date(meta.target_end+'T00:00:00Z'),day=d.getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-period);
  d.setUTCDate(Math.min(day,new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate()));
  return d.toISOString().slice(0,10)<meta.target_start?meta.target_start:d.toISOString().slice(0,10);
}
function endDate(){return customRange?.end||meta.target_end;}
function validDate(value){const date=new Date(value+'T00:00:00Z');return /^\d{4}-\d{2}-\d{2}$/.test(value)&&Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===value;}
function visibleDates(){
  return {start:$('broad-start').value,end:$('broad-end').value};
}
function allowedRange({start,end}){
  return validDate(start) && validDate(end) && start<=end && start>=meta.target_start && end<=meta.target_end;
}
function sameRange(a,b){return a.start===b.start && a.end===b.end;}
function appliedDates(){return {start:startDate(),end:endDate()};}
function renderDateNotice(){
  const notice=$('broad-status'), dates=visibleDates();
  if(rangeRejected && meta){
    notice.textContent=allowedRange(dates)?'日期已修正，请点击应用。':'日期范围无效：请选择 '+meta.target_start+' 至 '+meta.target_end+'，且开始日期不晚于结束日期。';
  }else if(meta && !sameRange(dates,appliedDates())){
    notice.textContent='日期待应用。'+(data?'当前结果为 '+data.start+' 至 '+data.end+'。':rangeMessage)+'切换指数或刷新也会应用所填日期。';
  }else{
    notice.textContent=rangeMessage;
  }
}
function setRangeMessage(message){rangeMessage=message;renderDateNotice();}
function syncDates({keepInput=false}={}){
  for(const id of ['broad-start','broad-end']){$(id).min=meta.target_start;$(id).max=meta.target_end;}
  if(!keepInput){$('broad-start').value=startDate();$('broad-end').value=endDate();}
}
function renderFilters(){
  const select=$('broad-index');select.replaceChildren();
  for(const item of [{code:'all',name:'全部已核验宽基',fund_count:meta.total?.expected_funds},...meta.indexes]){
    const option=el('option',item.name+(item.code==='all'?'':' · '+(item.fund_count||0)+' 只 ETF'));option.value=item.code;select.append(option);
  }
  select.value=indexCode;
}
function periodStats(){
  const series=data?.series||[],observed=series.filter(row=>Number.isFinite(row.observed_flow)),eligible=series.filter(row=>row.expected_funds>0||row.unknown_lifecycle_funds>0),complete=eligible.filter(row=>Number.isFinite(row.estimated_flow));
  const sum=(rows,key)=>{const total=rows.reduce((value,row)=>value+row[key],0);return rows.length&&Number.isFinite(total)?total:null;};
  return {observed,complete,observedTotal:sum(observed,'observed_flow'),completeTotal:eligible.length&&complete.length===eligible.length?sum(complete,'estimated_flow'):null};
}
function renderTotal(message='正在读取期间资金…',busy=!data){
  const box=$('broad-total');box.replaceChildren();box.setAttribute('aria-busy',String(busy));
  const main=el('div',null,'broad-total-main'),context=el('div',null,'broad-total-context'),name=meta?selectedIndex()?.name:'所选指数';
  const stats=data?periodStats():null;
  const direction=stats?.observedTotal>0?'净流入':stats?.observedTotal<0?'净流出':'';
  main.append(el('h2',name+' · 期间估算净申赎小计'+(direction?' · '+direction:'')));
  if(!data){main.append(el('strong','—','broad-total-value'));context.append(el('p',message));box.append(main,context);return;}
  main.append(el('strong',money(stats.observedTotal),'broad-total-value '+tone(stats.observedTotal)));
  const tail=data.series.at(-1),latest=stats.observed.at(-1);
  context.append(el('p',data.start+' 至 '+data.end,'broad-total-range'));
  const coverage=stats.completeTotal!==null?'成员齐全':'仅计已披露记录';
  context.append(el('p','有值 '+stats.observed.length+' / '+data.series.length+' 个交易日 · '+coverage,'broad-total-completeness'));
  const end=el('p',null,'ledger-last');
  if(tail){
    end.append(el('span','期末 '+tail.trade_date),el('strong',Number.isFinite(tail.observed_flow)?money(tail.observed_flow):'当日暂无估算',tone(tail.observed_flow)));
    if(latest && latest.trade_date!==tail.trade_date)end.append(el('small','最近有效资金日 '+latest.trade_date));
  }else{end.append(el('span','所选期间无交易日'));}
  context.append(end);
  box.append(main,context);
}
function memberPeriod(row){return data&&row.period_start===data.start&&row.period_end===data.end;}
function memberAmount(row){return memberPeriod(row)&&Number.isFinite(row.period_observed_flow)?row.period_observed_flow:null;}
function currentMembers(){
  const query=$('broad-search').value.trim(),sort=$('broad-members-sort').value||'magnitude';
  const rows=(data?.members||[]).filter(r=>!query||r.fund_code.includes(query)||(r.fund_name||'').includes(query));
  rows.sort((a,b)=>{
    if(sort==='name')return (a.fund_name||a.fund_code).localeCompare(b.fund_name||b.fund_code,'zh-CN')||a.fund_code.localeCompare(b.fund_code);
    const av=memberAmount(a),bv=memberAmount(b);if(av===null||bv===null)return av===bv?a.fund_code.localeCompare(b.fund_code):av===null?1:-1;
    return (sort==='inflow'?bv-av:sort==='outflow'?av-bv:Math.abs(bv)-Math.abs(av))||a.fund_code.localeCompare(b.fund_code);
  });return rows;
}
function renderSelection(){
  const member=data?.members.find(row=>row.fund_code===selected),back=$('broad-return-index');back.hidden=!member;back.disabled=!data;
  const action=$('broad-open-history');
  action.textContent=member?'查看基金历史':'查看所属 ETF';
  action.disabled=!data;
  action.setAttribute('aria-controls',member?'history-view':'broad-list');
  $('broad-selection-state').hidden=!member;
  $('broad-selection-state').textContent=member?'正在查看 '+member.fund_name+' '+member.fund_code+'；上方合计仍为 '+selectedIndex().name+' 整个指数。':'';
}
function renderList(emptyMessage='正在读取基金明细…'){
  const body=$('broad-members-body');body.replaceChildren();$('broad-members-prev').disabled=true;$('broad-members-next').disabled=true;renderSelection();
  if(!data){$('broad-members-page').textContent=emptyMessage;return;}
  const members=currentMembers(),pages=Math.max(1,Math.ceil(members.length/memberPageSize));memberPage=Math.min(memberPage,pages);
  for(const row of members.slice((memberPage-1)*memberPageSize,memberPage*memberPageSize)){
    const matches=memberPeriod(row),value=memberAmount(row),tr=el('tr'),name=el('td'),amount=el('td',null,'num '+tone(value)),coverage=el('td'),ending=el('td',null,'num');
    tr.dataset.fundCode=row.fund_code;tr.classList.toggle('selected',selected===row.fund_code);
    const open=el('button',null,'table-link member-link');
    open.type='button';
    open.setAttribute('aria-label','查看 '+(row.fund_name||'ETF')+' '+row.fund_code+' 的走势');
    open.setAttribute('aria-controls','broad-chart');
    open.setAttribute('aria-pressed',String(selected===row.fund_code));
    open.append(el('strong',row.fund_name||'名称待核验'),el('small',row.fund_code));
    open.addEventListener('click',()=>{
      selected=row.fund_code;renderList();renderMetrics();loadChart();revealSection('broad-heading');
    });
    name.append(open);
    amount.append(el('strong',money(value)),el('small',matches&&Number.isFinite(row.period_complete_flow)?'完整期间估算':'已有记录估算'));
    coverage.append(el('span',matches?(row.period_valid_days??'—')+' / '+(row.period_expected_days??'未知')+' 日':'待核验'),el('small','有值 / 应有交易日'));
    ending.append(el('strong',money(matches?row.end_date_flow:null),tone(matches?row.end_date_flow:null)),el('small',matches&&row.end_date?row.end_date:'期末日期待核验'));
    tr.append(name,amount,coverage,ending);body.append(tr);
  }
  if(!members.length){const row=el('tr'),cell=el('td',data.members.length?'没有匹配的 ETF，请修改搜索条件。':'这个指数暂未取得可核验的 ETF 映射，可先查看指数走势。','note');cell.colSpan=4;row.append(cell);body.append(row);}
  $('broad-members-page').textContent='第 '+memberPage+' / '+pages+' 页 · '+members.length+' 只 ETF · 每页 '+memberPageSize+' 只';$('broad-members-prev').disabled=memberPage<=1;$('broad-members-next').disabled=memberPage>=pages;
}
function endMemberCoverage(day){
  if(!day)return ['期末可估算 ETF','无交易日'];
  const label=day.trade_date+' · 可估算 ETF';
  if(!Number.isInteger(day.valid_funds)||!Number.isInteger(day.expected_funds))return [label,'覆盖待核验'];
  if(day.expected_funds===0)return [label,day.unknown_lifecycle_funds?'存续期待核验':'无应有成员'];
  if(day.valid_funds===0)return [day.trade_date+' · 应有 '+day.expected_funds+' 只 ETF','当日暂无估算'];
  return [label,day.valid_funds+' / '+day.expected_funds+' 只'];
}
function summary(){
  renderTotal();const box=$('broad-summary');box.replaceChildren();const latest=data?.series.at(-1),stats=periodStats();
  for(const [label,value,amount] of [
    ['完整期间估算合计',stats.completeTotal===null?(data.series.length?'资料不足':'无交易日'):money(stats.completeTotal),stats.completeTotal],
    ['有资金估算的交易日',stats.observed.length+' / '+data.series.length+' 日',null],
    endMemberCoverage(latest),
  ]){const item=el('div',null,'history-stat');item.append(el('span',label),el('strong',value,tone(amount)));box.append(item);}
  $('broad-asof').textContent='数据目标日 '+meta.target_end;
  setRangeMessage('');
  $('broad-notes').textContent=[...(meta.notes||[]),'资金口径为跟踪指数的 ETF 净申赎，无法识别持有人，也不代表成分股实际买卖金额。份额变化 × 前日净值为净申赎估算；缺失不计为零，完整合计需所有应有成员齐全。指数早期数据可早于 ETF 存续期。'].join(' ');
}
function renderDaily(){
  const body=$('broad-daily-body');body.replaceChildren();
  $('broad-daily-prev').disabled=true;$('broad-daily-next').disabled=true;
  if(!data){$('broad-daily-heading').textContent='指数每日资金明细';$('broad-daily-state').textContent='选择范围后显示逐日明细。';$('broad-daily-page').textContent='第 1 页';return;}
  const rows=data.series.slice().sort((a,b)=>b.trade_date.localeCompare(a.trade_date)),pages=Math.max(1,Math.ceil(rows.length/50));dailyPage=Math.min(dailyPage,pages);
  $('broad-daily-heading').textContent=selectedIndex().name+' · 每日资金明细';
  $('broad-daily-state').textContent=data.start+' 至 '+data.end+' · 共 '+rows.length+' 个交易日，按日期倒序，每页 50 条。仅展示所选指数整体；— 表示缺失或未满足完整条件，不是零。指数收盘采用 '+(data.index.benchmark_name||data.index.name)+(indexCode==='all'?' 参考基准':'')+'。';
  const count=value=>value==null?'—':new Intl.NumberFormat('zh-CN',{maximumFractionDigits:0}).format(value);
  for(const row of rows.slice((dailyPage-1)*50,dailyPage*50)){
    const tr=el('tr');tr.append(el('td',row.trade_date),el('td',money(row.observed_flow),'num '+tone(row.observed_flow)),el('td',money(row.estimated_flow),'num '+tone(row.estimated_flow)),el('td',count(row.valid_funds),'num'),el('td',count(row.expected_funds),'num'),el('td',count(row.unknown_lifecycle_funds),'num'),el('td',exactAmount(row.index_close,'点',false,2),'num'));body.append(tr);
  }
  $('broad-daily-page').textContent='第 '+dailyPage+' / '+pages+' 页 · '+rows.length+' 个交易日';$('broad-daily-prev').disabled=dailyPage<=1;$('broad-daily-next').disabled=dailyPage>=pages;
}
function renderOffExchange(){
  const box=$('broad-offshore-table');box.replaceChildren();const off=data?.off_exchange||{},reports=Array.isArray(off.reports)?off.reports:[],total=off.total??reports.length,pageSize=off.page_size||20;
  const countLabel = total ? '有 '+total+' 条已核验报告。' : '暂无已核验报告，可扩大日期范围。';
  $('broad-offshore-state').textContent=data.start+' 至 '+data.end+'，按报告期末筛选，'+countLabel+'申赎单位为份，与场内资金金额不可直接相加。';
  const notes = el('details',null,'method-details');
  notes.append(el('summary','披露口径与覆盖范围'),el('p',(off.notes||[]).join(' ') || '场外数据按基金披露期间统计。','note'));
  if(!reports.length){box.append(notes);return;}
  const table=el('table'),head=el('thead'),header=el('tr');
  for(const label of ['场外基金 / 份额类别','披露期间','申购份额','赎回份额','净申购份额','来源'])header.append(el('th',label));head.append(header);table.append(head);
  const body=el('tbody');offPage=off.page||1;
  for(const report of reports){
    const member=off.members?.find(x=>x.fund_code===report.fund_code),row=el('tr'),name=el('td');name.append(el('strong',(report.fund_name||member?.fund_name||'基金')+' '+report.fund_code),el('small',report.share_class||'报告总份额（未拆分类别）'));
    if(report.merged_codes?.length)name.append(el('small','同族份额：'+(Array.isArray(report.merged_codes)?report.merged_codes.join('、'):report.merged_codes)));
    row.append(name,el('td',(report.period_start||'—')+' 至 '+(report.period_end||'—')));
    for(const field of ['subscription_shares','redemption_shares','net_subscription_shares'])row.append(el('td',report[field]==null?'—':exactAmount(report[field],'份',false,2),field==='net_subscription_shares'?tone(report[field]):''));
    const source=el('td');if(typeof report.source_url==='string'&&/^https?:\/\//.test(report.source_url)){const link=el('a','原始披露');link.href=report.source_url;link.target='_blank';link.rel='noopener noreferrer';source.append(link);}else source.textContent='来源待补';row.append(source);body.append(row);
  }
  table.append(body);box.append(table);
  const nav=el('div',null,'broad-report-pager'),previous=el('button','上一页'),next=el('button','下一页');previous.type=next.type='button';previous.disabled=offPage===1;next.disabled=offPage*pageSize>=total;
  previous.addEventListener('click',()=>loadOffExchange(offPage-1));next.addEventListener('click',()=>loadOffExchange(offPage+1));nav.append(previous,el('span',offPage+' / '+Math.ceil(total/pageSize)),next);box.append(nav,notes);
}
async function loadOffExchange(page){
  if(!data)return;const current=data,code=indexCode,sequence=offGuard.next();$('broad-offshore-table').replaceChildren();$('broad-offshore-state').textContent='正在读取第 '+page+' 页披露记录…';
  try{const result=await api('broad/off/'+code,{start:current.start,end:current.end,page,page_size:20});if(!offGuard.current(sequence)||data!==current||indexCode!==code)return;if(result.version!==meta.version||result.start!==current.start||result.end!==current.end)throw Error('场外报告版本或日期范围已变化，请刷新数据');data.off_exchange=result;renderOffExchange();if(!$('broad-view').hidden)revealSection('broad-offshore-title');}
  catch(error){if(!offGuard.current(sequence)||data!==current)return;$('broad-offshore-state').textContent=error.message;const retry=el('button','重新读取披露记录');retry.type='button';retry.addEventListener('click',()=>loadOffExchange(page));$('broad-offshore-table').append(retry);}
}
async function load(){
  const datesAtRequest=visibleDates(),sequence=guard.next();
  loading=true;chartGuard.next();offGuard.next();data=null;clearChart();renderDaily();renderList();renderTotal('正在读取指数与 ETF 对应关系…');$('broad-summary').replaceChildren();setRangeMessage('正在读取指数与 ETF 对应关系…');
  try{
    const result=await api('broad/meta');if(!guard.current(sequence))return;
    if(!Array.isArray(result.indexes)||!result.indexes.length)throw Error('尚无可用指数数据');
    meta=result;if(indexCode!=='all'&&!meta.indexes.some(x=>x.code===indexCode))indexCode=meta.indexes[0].code;
    renderFilters();syncDates({keepInput:!sameRange(visibleDates(),datesAtRequest)});
    if(!allowedRange(appliedDates())){rejectRange();return;}
    await loadIndex();
  }catch(error){if(!guard.current(sequence))return;meta=null;data=null;$('broad-summary').replaceChildren();renderList('基金明细暂不可用，请刷新重试。');renderTotal(error.message+'，请刷新重试。',false);clearChart();setRangeMessage(error.message);$('broad-chart-state').textContent='旧图已隐藏，请点击顶部刷新数据重试。';}
  finally{if(guard.current(sequence))loading=false;}
}
async function loadIndex(){
  if(!meta)return;const sequence=chartGuard.next(),code=indexCode,start=startDate(),end=endDate();
  setRangeMessage('正在读取 '+start+' 至 '+end+' 的期间资金…');
  $('broad-index-quote').replaceChildren();$('broad-legend').textContent='';
  $('broad-heading').textContent=selectedIndex().name+' · 正在读取期间资金';$('broad-subtitle').textContent='所选范围 '+start+' 至 '+end;
  offGuard.next();$('broad-offshore-table').replaceChildren();$('broad-offshore-state').textContent='正在读取所选指数的场外披露记录…';
  data=null;dailyPage=1;memberPage=1;renderDaily();renderList();renderTotal('正在读取 '+start+' 至 '+end+' 的期间资金…');$('broad-summary').replaceChildren();
  clearChart();$('broad-chart-state').textContent='正在加载完整交易日范围…';$('broad-open-history').disabled=true;
  try{
    const result=await api('broad/index/'+code,{start,end});
    if(!chartGuard.current(sequence))return;if(result.version!==meta.version)throw Error('数据版本已更新，请点击顶部刷新数据');
    if(result.index?.code!==code||result.start!==start||result.end!==end||!Array.isArray(result.series)||!Array.isArray(result.members)||result.series.some((row,i)=>!validDate(row.trade_date)||row.trade_date<start||row.trade_date>end||i>0&&row.trade_date<=result.series[i-1].trade_date))throw Error('返回数据与所选指数或日期范围不一致，请刷新重试');
    data=result;offPage=1;if(selected&&!data.members.some(r=>r.fund_code===selected))selected=null;
    summary();renderList();renderMetrics();renderDaily();renderOffExchange();await loadChart();
  }catch(error){if(!chartGuard.current(sequence))return;data=null;renderList('基金明细暂不可用，请刷新重试。');renderDaily();renderTotal(error.message+'，请刷新重试。',false);$('broad-summary').replaceChildren();clearChart();$('broad-chart-state').textContent=error.message;setRangeMessage(error.message);}
}
function renderMetrics(){metrics.hidden=!selected;for(const control of metrics.children){control.disabled=!selected&&control.dataset.metric!=='flow';const active=control.dataset.metric===metric;control.classList.toggle('active',active);control.setAttribute('aria-pressed',String(active));}}
function renderIndexQuote(series, name) {
  const quote = $('broad-index-quote');
  quote.replaceChildren();
  const closes = series.filter(row => Number.isFinite(row.index_close) && row.index_close > 0);
  if (!closes.length) {
    quote.textContent = name + ' · 所选期间暂无收盘点位';
    return;
  }
  const first = closes[0], last = closes.at(-1);
  quote.append(el('span', name + ' · ' + last.trade_date + ' 收盘 '), el('strong', exactAmount(last.index_close,'点',false,2)));
  if (closes.length === 1) {
    quote.append(el('span', '区间内仅有一个收盘日', 'quote-period'));
    return;
  }
  const change = (last.index_close / first.index_close - 1) * 100;
  const period = el('span', first.trade_date + ' 至 ' + last.trade_date + ' 首末收盘涨跌 ', 'quote-period');
  period.append(el('b', (change > 0 ? '+' : '') + change.toFixed(2) + '%', tone(change)));
  quote.append(period);
}
async function loadChart(){
  if(!data||!meta)return;const sequence=chartGuard.next(),current=data,code=selected,member=current.members.find(r=>r.fund_code===code),index=selectedIndex();
  renderSelection();$('broad-heading').textContent=member?(member.fund_name+' '+code):index.name+' · 整体资金与走势';
  const benchmarkName=current.index.benchmark_name||current.index.name;
  renderIndexQuote(current.series, indexCode === 'all' ? benchmarkName + '（参考基准）' : benchmarkName);
  $('broad-subtitle').textContent=member?'上市日期 '+(member.listed_date||'待核验'):'';
  clearChart();$('broad-chart-state').textContent='正在整理资金与指数走势…';
  try{
    let rows,label='当日 ETF 估算净申赎小计',notes={},kind='bar',unit='元';
    if(member){
      const historyMeta=await api('history/meta');if(!chartGuard.current(sequence))return;
      const start=current.start<historyMeta.target_start?historyMeta.target_start:current.start,end=current.end>historyMeta.target_end?historyMeta.target_end:current.end;
      const history=start<=end?await api('history/etf/'+code+'/chart',{start,end}):{series:[],version:historyMeta.version};
      if(history.version!==historyMeta.version)throw Error('ETF 历史版本已变化，请刷新数据');
      if(meta.history_version&&history.version!==meta.history_version)throw Error('指数汇总与 ETF 历史版本不同，请完成数据更新后刷新');
      const field=metric==='flow'?'estimated_flow':metric==='shares'?'shares':'unit_nav';rows=history.series.map(r=>({date:r.trade_date,value:r[field]}));label=metric==='flow'?'当日估算净申赎':metric==='shares'?'基金份额':'单位净值';unit=metric==='shares'?'份':'元';kind=metric==='flow'?'bar':'line';
    }else{
      rows=current.series.map(r=>({date:r.trade_date,value:r.observed_flow}));
      notes=Object.fromEntries(current.series.map(r=>[r.trade_date,r.expected_funds===0?'当日尚无可核验的存续 ETF':('当日可估算 '+r.valid_funds+' / '+r.expected_funds+' 只'+(r.estimated_flow==null?' · 仅为已有记录小计':' · 已核验成员齐全')+(r.unknown_lifecycle_funds?' · '+r.unknown_lifecycle_funds+' 只成员的上市区间待核验':''))]));
    }
    if(!chartGuard.current(sequence))return;
    const benchmark=current.index.benchmark_name||current.index.name;
    lastChart={rows,options:{start:current.start,end:current.end,tradingDates:current.trading_dates,label,unit,kind,signed:metric==='flow',directional:metric==='flow',decimals:metric==='nav'?8:2,pointNotes:notes,overlay:{label:benchmark+(indexCode==='all'?'（参考基准）':''),unit:'点',rows:current.series.map(r=>({date:r.trade_date,value:r.index_close}))}}};renderCachedChart();
    $('broad-legend').textContent=(metric==='flow'?'红色：资金流入　绿色：资金流出':'灰色虚线：'+label)+'　黑线：'+benchmark+'（右轴，点）';
    $('broad-chart-state').textContent=(!member?'ETF 估算净申赎小计，不含成分股实际成交。':'')+'缺失交易日留空。';
  }catch(error){if(chartGuard.current(sequence)){clearChart();$('broad-chart-state').textContent=error.message;}}
}
const toolbar=el('div',null,'broad-chart-toolbar'),periods=el('div',null,'period-control'),metrics=el('div',null,'chart-mode');
periods.setAttribute('role','group');periods.setAttribute('aria-label','宽基监测周期');metrics.hidden=true;metrics.setAttribute('role','group');metrics.setAttribute('aria-label','基金图表指标');
function renderPeriods(){
  $('broad-date-trigger').textContent=customRange&&!rangeRejected?'自选期间':'自选日期';
  for(const button of periods.children){
    const active=!customRange && !rangeRejected && Number(button.dataset.months)===period;
    button.classList.toggle('active',active);
    button.setAttribute('aria-pressed',String(active));
  }
}
for(const [months,name] of [[1,'1个月'],[3,'3个月'],[12,'1年'],[60,'5年'],[0,'全部历史']]){
  const button=el('button',name);
  button.type='button';button.dataset.months=String(months);
  button.addEventListener('click',()=>{
    period=months;customRange=null;rangeRejected=false;
    $('broad-custom-range').open=false;
    renderPeriods();if(meta)syncDates();loadIndex();
  });
  periods.append(button);
}
renderPeriods();
for(const [key,name] of [['flow','估算净申赎'],['shares','份额'],['nav','净值']]){const b=el('button',name);b.type='button';b.dataset.metric=key;b.addEventListener('click',()=>{metric=key;renderMetrics();loadChart();});metrics.append(b);}
toolbar.append(periods,metrics);$('broad-toolbar').append(toolbar);
$('broad-tab').addEventListener('click',show);
$('history-back')?.addEventListener('click',()=>{
  if($('history-back').dataset.returnView==='stock'){
    $('stock-tab').click();
    ($('stock-fund-history')||$('stock-tab')).focus();
  }else{
    show();
    ($('broad-open-history').disabled?$('broad-index'):$('broad-open-history')).focus();
  }
});
$('refresh').addEventListener('click',()=>{
  if(meta){if(commitVisibleDates())load();}
  else if(!$('broad-view').hidden)load();
});
$('broad-index').addEventListener('change',()=>{
  indexCode=$('broad-index').value;selected=null;metric='flow';memberPage=1;$('broad-search').value='';
  if(commitVisibleDates())loadIndex();
});
$('broad-search').addEventListener('input',()=>{memberPage=1;renderList();});
$('broad-members-sort').addEventListener('change',()=>{memberPage=1;renderList();});
$('broad-members-prev').addEventListener('click',()=>{if(memberPage>1){memberPage--;renderList();revealSection('broad-list-title');}});
$('broad-members-next').addEventListener('click',()=>{if(data&&memberPage*memberPageSize<currentMembers().length){memberPage++;renderList();revealSection('broad-list-title');}});
$('broad-return-index').addEventListener('click',()=>{if(!data)return;selected=null;metric='flow';renderList();renderMetrics();loadChart();});
function rejectRange(){
  guard.next();chartGuard.next();offGuard.next();
  $('broad-custom-range').open=true;
  rangeRejected=true;loading=false;data=null;selected=null;metric='flow';dailyPage=memberPage=offPage=1;
  clearChart();renderPeriods();renderDaily();renderList('请先选择有效日期范围。');renderMetrics();renderTotal('请修改日期后查询。',false);
  for(const id of ['broad-summary','broad-index-quote','broad-offshore-table'])$(id).replaceChildren();
  for(const id of ['broad-legend','broad-subtitle','broad-chart-state','broad-offshore-state'])$(id).textContent='';
  $('broad-heading').textContent=selectedIndex().name+' · 等待有效日期';
  setRangeMessage('');
}
function commitVisibleDates(){
  if(!meta)return false;
  const dates=visibleDates();
  if(!allowedRange(dates)){rejectRange();return false;}
  if(!sameRange(dates,appliedDates()))customRange=dates;
  rangeRejected=false;renderPeriods();
  return true;
}
for(const id of ['broad-start','broad-end'])$(id).addEventListener('input',renderDateNotice);
$('broad-apply').addEventListener('click',()=>{
  if(commitVisibleDates()){
    $('broad-custom-range').open=false;
    $('broad-date-trigger').focus();
    loadIndex();
  }
});
$('broad-open-history').addEventListener('click',()=>{
  if(!data)return;
  const member=data.members.find(row=>row.fund_code===selected);
  if(!member){revealSection('broad-list-title');return;}
  document.dispatchEvent(new CustomEvent('etf-open-history',{detail:{
    ...member,return_view:'broad',range_start:data.start,range_end:data.end
  }}));
});
$('broad-section-links')?.addEventListener('click',event=>{
  if(event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)return;
  const link=event.target.closest?.('a[href^="#"]');
  if(!link)return;
  const targetId=link.getAttribute('href').slice(1),target=$(targetId);
  if(!target)return;
  event.preventDefault();
  const disclosure=target.closest('details');
  if(disclosure)disclosure.open=true;
  revealSection(targetId);
});
$('broad-daily-prev').addEventListener('click',()=>{if(dailyPage>1){dailyPage--;renderDaily();revealSection('broad-daily-heading');}});
$('broad-daily-next').addEventListener('click',()=>{if(data&&dailyPage*50<data.series.length){dailyPage++;renderDaily();revealSection('broad-daily-heading');}});
onMoneyUnitChange(()=>{if(data){summary();renderList();renderDaily();}renderCachedChart();});
if(!$('broad-view').hidden)show();
