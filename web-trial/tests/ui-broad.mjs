import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setMoneyUnit} from '../public/logic.mjs';
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.attributes={};this.dataset={};this.value='';this.textContent='';this.scrollCalls=[];this.focusCalls=[];this.classList={toggle(){}};}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;this.textContent='';}before(node){this.beforeNode=node;}setAttribute(k,v){this.attributes[k]=v;}addEventListener(k,v){this.listeners[k]=v;}click(){if(!this.disabled)this.listeners.click?.({});}
 focus(options){this.focusCalls.push(options);document.activeElement=this;}scrollIntoView(options){this.scrollCalls.push(options);}
}
const nodes=new Map(),renderings=[],calls=[],pending=[],events=[];
globalThis.document={getElementById(id){if(id==='history-tab')return null;if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},createElement:tag=>new Node(tag),dispatchEvent(event){events.push(event);}};
globalThis.CustomEvent=class{constructor(type,{detail}){this.type=type;this.detail=detail;}};
document.getElementById('stock-tab').addEventListener('click',()=>{document.getElementById('broad-view').hidden=true;document.getElementById('history-view').hidden=true;document.getElementById('stock-view').hidden=false;});
globalThis.__broadRender=(container,rows,options)=>{renderings.push({rows,options});container.append(new Node('svg'));};
const response=data=>({ok:true,json:async()=>({code:0,data})});
let hold=false,version='v1',offVersion='v1',dense=false,many=false,fixtureFlow=10000;
let linkedHistory='h1',sourceMode='normal';
const sourcePending=[];
function historyMetadata(historyVersion=linkedHistory, day='2026-11-06') {
 return {version:historyVersion,target_start:'2021-01-01',target_end:'2026-11-09',generated_at:'2026-11-09T08:00:00Z',data_dates:{shares:{date:day,count:2},nav:{date:'2026-11-05',count:1},flow:{date:'2026-11-05',count:1}}};
}
const members=[{fund_code:'510300',fund_name:'沪深300ETF',estimated_flow:10000,flow_date:'2026-11-09',listed_date:'2012-05-28'},{fund_code:'159919',fund_name:'沪深300ETF深',estimated_flow:null}];
function indexResponse(u){
 const start=u.searchParams.get('start'),end=u.searchParams.get('end');
 let series=[{trade_date:'2026-11-06',observed_flow:null,estimated_flow:null,valid_funds:0,expected_funds:2,index_close:4000},{trade_date:'2026-11-09',observed_flow:fixtureFlow,estimated_flow:null,valid_funds:1,expected_funds:2,index_close:4100}];
 if(dense){series=[];for(let date=new Date(start+'T00:00:00Z');date.toISOString().slice(0,10)<=end;date.setUTCDate(date.getUTCDate()+1)){if(![0,6].includes(date.getUTCDay()))series.push({trade_date:date.toISOString().slice(0,10),observed_flow:10000,estimated_flow:null,valid_funds:1,expected_funds:2,unknown_lifecycle_funds:1,index_close:4100});}}
 series=series.filter(row=>row.trade_date>=start&&row.trade_date<=end);const valid=series.filter(row=>row.observed_flow!=null);
 const fixtures=many?Array.from({length:46},(_,i)=>({fund_code:String(510000+i),fund_name:'测试ETF'+i})):members;
 const scoped=fixtures.map((row,i)=>({...row,period_start:start,period_end:end,period_observed_flow:many?i===45?null:(i-22)*1000+valid.reduce((sum,row)=>sum+row.observed_flow,0)/45:i?null:valid.reduce((sum,row)=>sum+row.observed_flow,0),period_valid_days:i?0:valid.length,period_expected_days:i?null:series.length,end_date:end,end_date_flow:i?null:10000}));
 return {version,index:{code:u.pathname.split('/').at(-1),name:'沪深300',benchmark_name:'沪深300'},start,end,trading_dates:series.map(row=>row.trade_date),members:scoped,series,off_exchange:{members:[],notes:['份额口径'],reports:[{fund_code:'000051',fund_name:'联接A',period_start:'2026-07-01',period_end:'2026-09-30',subscription_shares:123,redemption_shares:null,net_subscription_shares:null,share_class:'A',source_url:'https://fund.example/report'}]}};
}
globalThis.fetch=async url=>{
 const u=new URL(url,'https://local.test');calls.push(u);
 if(u.pathname==='/api/v1/broad/meta')return response({version,history_version:linkedHistory,target_start:'1990-12-19',target_end:'2026-11-09',indexes:[{code:'000300',name:'沪深300',fund_count:2,available_end:'2026-11-06'},{code:'000001',name:'上证综指',fund_count:0,available_end:'2026-11-05'}],total:{observed_flow:10000,expected_funds:2},notes:['成员映射未必覆盖全市场']});
 if(u.pathname.includes('/broad/index/')){if(hold)return new Promise(resolve=>pending.push({u,resolve}));const result=indexResponse(u);Object.assign(result.off_exchange,{total:21,page:1,page_size:20});return response(result);}
 if(u.pathname.includes('/broad/off/'))return response({...indexResponse(u).off_exchange,version:offVersion,start:u.searchParams.get('start'),end:u.searchParams.get('end'),total:21,page:Number(u.searchParams.get('page')),page_size:20});
 if(u.pathname==='/api/v1/history/meta'){
  if(sourceMode==='deferred')return new Promise(resolve=>sourcePending.push(resolve));
  if(sourceMode==='failure')throw Error('来源日期暂不可读');
  return response(historyMetadata(sourceMode==='mismatch'?'unmatched':linkedHistory));
 }
 if(u.pathname.endsWith('/chart'))return response({version:'h1',series:[{trade_date:'2026-11-09',estimated_flow:10000,unit_nav:4.12345678,shares:100}]});
 throw Error('unexpected '+u.pathname);
};
let source=await readFile(new URL('../public/broad.mjs',import.meta.url),'utf8');
source=source.replace("import {renderTimeChart,exactAmount} from './charts.mjs';",'import {exactAmount} from '+JSON.stringify(new URL('../public/charts.mjs',import.meta.url).href)+';const renderTimeChart=globalThis.__broadRender;').replace("from './logic.mjs'",'from '+JSON.stringify(new URL('../public/logic.mjs',import.meta.url).href));
await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
const scrollCount=()=>[...nodes.values()].reduce((count,node)=>count+node.scrollCalls.length,0);
function expectRevealed(id,count){const heading=nodes.get(id);assert.equal(heading.scrollCalls.length,count);assert.equal(heading.focusCalls.length,count);assert.equal(document.activeElement,heading);assert.equal(heading.attributes.tabindex,'-1');assert.deepEqual(heading.focusCalls.at(-1),{preventScroll:true});assert.deepEqual(heading.scrollCalls.at(-1),{block:'start',behavior:'auto'});}
await settle();assert.equal(calls.filter(u=>u.pathname==='/api/v1/broad/meta').length,1,'visible default broad view loads once without a tab click');
assert.equal(nodes.get('broad-asof').textContent,'全目录ETF份额截至 2026-11-06（2只有记录） · 全目录净值截至 2026-11-05（1只有记录） · 指数行情截至 2026-11-06','top-level freshness uses the immutable source dates even when the query target is later');
assert.match(nodes.get('broad-heading').textContent,/沪深300.*整体资金/);
assert.equal(renderings.at(-1).rows[0].value,null);assert.equal(renderings.at(-1).rows[1].value,10000);
assert.equal(renderings.at(-1).options.overlay.unit,'点');assert.deepEqual(renderings.at(-1).options.tradingDates,['2026-11-06','2026-11-09']);
const comparison=nodes.get('broad-index-quote').children.at(-1);
assert.match(comparison.textContent,/2026-11-06 至 2026-11-09 首末收盘涨跌/);
assert.equal(comparison.children[0].textContent,'+2.50%');
assert.equal(nodes.get('broad-summary').children[0].children[1].textContent,'资料不足','partial members must not be labelled a complete total');
assert.match(nodes.get('broad-total').children[0].children[0].textContent,/沪深300.*期间估算净申赎小计.*净流入/);
assert.match(renderings.at(-1).options.label,/当日.*估算净申赎小计/);
const totalContext=nodes.get('broad-total').children[1].children;
assert.equal(totalContext[0].textContent,'2026-08-09 至 2026-11-09');
assert.match(totalContext[1].textContent,/有值 1 \/ 2 个交易日.*仅计已披露记录/);
assert.equal(totalContext[2].children[0].textContent,'期末 2026-11-09');
assert.equal(totalContext[2].children[1].textContent,nodes.get('broad-daily-body').children[0].children[1].textContent);
assert.equal(nodes.get('broad-status').textContent,'','ready does not repeat period coverage above the graph');assert.equal(nodes.get('broad-subtitle').textContent,'');assert.equal(nodes.get('broad-selection-state').hidden,true);assert.equal(nodes.get('broad-selection-state').textContent,'');assert.match(nodes.get('broad-chart-state').textContent,/ETF 估算净申赎小计，不含成分股实际成交/);
assert.equal(nodes.get('broad-daily-body').children[0].children[0].textContent,'2026-11-09');
assert.equal(nodes.get('broad-daily-body').children[0].children[2].textContent,'—');
assert.equal(nodes.get('broad-daily-body').children[1].children[1].textContent,'—','missing daily flow is not zero');
assert.match(nodes.get('broad-members-body').children[0].children[2].children[0].textContent,/1 \/ 2 日/);
assert.equal(nodes.get('broad-members-body').children[1].children[1].children[0].textContent,'—','missing member period data is never taken from latest amount');
assert.equal(nodes.get('broad-members-body').children[1].children[1].children[1].textContent,'本期暂无估算');
const ranks=nodes.get('broad-drivers');
const ranked=(side)=>ranks.children[side].children[1].children.map(item=>item.children[0]);
assert.deepEqual(ranked(0).map(button=>button.dataset.fundCode),['510300']);
assert.match(ranks.children[1].children[1].textContent,/无净流出/);
assert.match(nodes.get('broad-drivers-period').textContent,/1 \/ 2 只有期间估算.*1 只本期暂无金额/);
assert.equal(nodes.get('broad-offshore-table').children[0].children[1].children[0].children[3].textContent,'—','missing reported redemption is not zero');
assert.match(nodes.get('broad-offshore-state').textContent,/不可直接相加/);
const unitCalls=calls.length,unitRenders=renderings.length,unitScrolls=scrollCount();setMoneyUnit('yi');
assert.equal(calls.length,unitCalls,'unit changes never refetch broad/history data');assert.equal(renderings.length,unitRenders+1);
assert.equal(scrollCount(),unitScrolls,'unit redraw never moves focus or scroll');
assert.match(nodes.get('broad-total').children[0].children[1].textContent,/亿元/);assert.match(nodes.get('broad-members-body').children[0].children[1].children[0].textContent,/亿元/);assert.equal(renderings.at(-1).options.overlay.unit,'点');setMoneyUnit('auto');
nodes.get('history-back').click();assert.equal(nodes.get('broad-view').hidden,false);assert.equal(nodes.get('history-view').hidden,true);
nodes.get('broad-offshore-table').children[1].children[2].click();await settle();assert(calls.some(u=>u.pathname.includes('/broad/off/')&&u.searchParams.get('page')==='2'),'report pagination fetches a bounded server page');
assert.equal(nodes.get('broad-offshore-table').children[1].children[1].textContent,'2 / 2');
expectRevealed('broad-offshore-title',1);const offScrolls=scrollCount();
offVersion='changed';nodes.get('broad-offshore-table').children[1].children[0].click();await settle();
assert.match(nodes.get('broad-offshore-state').textContent,/版本或日期范围已变化/);
assert.equal(scrollCount(),offScrolls,'failed pagination must not move focus');
assert.equal(nodes.get('broad-offshore-table').children[0].tag,'button','a mismatched page exposes retry, never stale financial rows');offVersion='v1';
const totalBeforeFund=nodes.get('broad-total').children[0].children[1].textContent;
const firstRow=nodes.get('broad-members-body').children[0],fundButton=firstRow.children[0].children[0];
assert.equal(firstRow.children.length,4);
assert.equal(fundButton.tag,'button');
assert.equal(fundButton.children[0].textContent,'沪深300ETF');
assert.equal(fundButton.children[1].textContent,'510300');
assert.match(fundButton.attributes['aria-label'],/沪深300ETF 510300/);
assert.equal(nodes.get('broad-open-history').textContent,'查看所属 ETF');
fundButton.click();await settle();
assert.match(nodes.get('broad-heading').textContent,/510300/);
assert.equal(document.activeElement,nodes.get('broad-heading'));
assert.equal(nodes.get('broad-open-history').textContent,'查看基金历史');
assert.equal(nodes.get('broad-open-history').disabled,false);assert.equal(nodes.get('broad-return-index').hidden,false);assert.equal(nodes.get('broad-total').children[0].children[1].textContent,totalBeforeFund,'fund drilldown never changes the independent index total');
assert.equal(nodes.get('broad-selection-state').hidden,false);assert.match(nodes.get('broad-selection-state').textContent,/上方合计仍为 沪深300 整个指数/);
nodes.get('broad-open-history').click();assert.equal(events.at(-1).detail.return_view,'broad');
assert.equal(events.at(-1).detail.range_start,'2026-08-09');assert.equal(events.at(-1).detail.range_end,'2026-11-09');
nodes.get('broad-return-index').click();await settle();assert.match(nodes.get('broad-heading').textContent,/整体资金/);assert.equal(nodes.get('broad-return-index').hidden,true);
nodes.get('history-back').dataset.returnView='stock';nodes.get('history-back').click();assert.equal(nodes.get('stock-view').hidden,false);assert.equal(nodes.get('broad-view').hidden,true);nodes.get('history-back').dataset.returnView='broad';nodes.get('history-back').click();
nodes.get('broad-members-body').children[0].children[0].children[0].click();await settle();
const before=calls.length;nodes.get('broad-start').value='2026-11-10';nodes.get('broad-end').value='2026-11-09';nodes.get('broad-apply').click();await settle();assert.equal(calls.length,before);assert.equal(nodes.get('broad-chart').children.length,0);assert.match(nodes.get('broad-status').textContent,/日期范围无效/);
for(const id of ['broad-legend','broad-subtitle','broad-chart-state','broad-offshore-state','broad-selection-state'])assert.equal(nodes.get(id).textContent,'',id+' clears stale text on invalid range');
for(const id of ['broad-index-quote','broad-offshore-table','broad-summary','broad-daily-body','broad-members-body'])assert.equal(nodes.get(id).children.length,0,id+' clears stale rows on invalid range');
assert.equal(nodes.get('broad-open-history').disabled,true);assert.equal(nodes.get('broad-return-index').disabled,true);assert.equal(nodes.get('broad-return-index').hidden,true);assert.equal(nodes.get('broad-selection-state').hidden,true);assert.equal(nodes.get('broad-total').attributes['aria-busy'],'false');
const invalidScrolls=scrollCount();setMoneyUnit('yi');setMoneyUnit('auto');assert.equal(scrollCount(),invalidScrolls);assert.equal(nodes.get('broad-chart').children.length,0,'unit redraw cannot restore invalid-range data');assert.match(nodes.get('broad-status').textContent,/日期范围无效/);
nodes.get('broad-start').value='2026-11-07';nodes.get('broad-end').value='2026-11-09';nodes.get('broad-apply').click();assert.equal(nodes.get('broad-total').attributes['aria-busy'],'true');await settle();assert(calls.some(u=>u.searchParams.get('start')==='2026-11-07'));assert.equal(nodes.get('broad-status').textContent,'');assert.equal(nodes.get('broad-total').attributes['aria-busy'],'false');assert(nodes.get('broad-chart').children.length>0);assert.equal(nodes.get('broad-return-index').hidden,true,'recovered range returns to the whole index');assert.equal(nodes.get('broad-open-history').disabled,false);assert.equal(nodes.get('broad-open-history').textContent,'查看所属 ETF');assert.match(nodes.get('broad-offshore-state').textContent,/不可直接相加/);
hold=true;nodes.get('broad-index').value='000001';nodes.get('broad-index').listeners.change();nodes.get('broad-index').value='000300';nodes.get('broad-index').listeners.change();
assert.match(nodes.get('broad-status').textContent,/正在读取 2026-11-07 至 2026-11-09/);assert.equal(nodes.get('broad-index-quote').children.length,0,'loading clears the previous period quote');assert.equal(nodes.get('broad-legend').textContent,'');
assert.equal(nodes.get('broad-total').children[0].children[1].textContent,'—','loading clears the previous period total immediately');nodes.get('stock-tab').click();
const loadingRenders=renderings.length,loadingCalls=calls.length;setMoneyUnit('yi');assert.equal(renderings.length,loadingRenders,'unit change while loading cannot restore stale chart');assert.equal(calls.length,loadingCalls);setMoneyUnit('auto');
pending[1].resolve(response(indexResponse(pending[1].u)));await settle();const rendered=renderings.length;pending[0].resolve(response({...indexResponse(pending[0].u),index:{name:'过期指数'}}));await settle();assert.equal(renderings.length,rendered);assert.equal(nodes.get('stock-view').hidden,false,'late broad responses cannot take navigation away from stocks');assert.equal(nodes.get('broad-view').hidden,true);nodes.get('broad-tab').click();
hold=false;dense=true;
const periods=nodes.get('broad-toolbar').children[0].children[0].children, totals=[];
for(const [position,start] of [[0,'2026-10-09'],[1,'2026-08-09'],[2,'2025-11-09']]){periods[position].click();assert.equal(nodes.get('broad-status').textContent,'正在读取 '+start+' 至 2026-11-09 的期间资金…','selected range is reflected before the response arrives');await settle();assert.equal(calls.filter(u=>u.pathname.includes('/broad/index/')).at(-1).searchParams.get('start'),start);totals.push(nodes.get('broad-total').children[0].children[1].textContent);assert.equal(nodes.get('broad-members-body').children[0].children[1].children[0].textContent,totals.at(-1),'member amounts use same selected period');}
assert.equal(new Set(totals).size,3,'1m/3m/1y must render distinct period totals while last quote is unchanged');
assert.equal(nodes.get('broad-daily-body').children.length,50);const firstDate=nodes.get('broad-daily-body').children[0].children[0].textContent;
const pagingCalls=calls.length;nodes.get('broad-daily-next').click();assert.equal(calls.length,pagingCalls);assert.equal(nodes.get('broad-daily-body').children.length,50);assert(nodes.get('broad-daily-body').children[0].children[0].textContent<firstDate);assert.match(nodes.get('broad-daily-page').textContent,/第 2/);
expectRevealed('broad-daily-heading',1);const dailyScrolls=scrollCount();setMoneyUnit('yi');assert.match(nodes.get('broad-daily-body').children[0].children[1].textContent,/亿元/);assert.equal(calls.length,pagingCalls);assert.match(nodes.get('broad-daily-page').textContent,/第 2/);setMoneyUnit('auto');assert.equal(scrollCount(),dailyScrolls);
nodes.get('broad-daily-prev').click();expectRevealed('broad-daily-heading',2);assert.equal(nodes.get('broad-daily-body').children[0].children[0].textContent,firstDate);nodes.get('broad-daily-prev').click();assert.equal(nodes.get('broad-daily-heading').scrollCalls.length,2,'disabled pagination does not scroll');
periods[0].click();await settle();assert.match(nodes.get('broad-daily-page').textContent,/第 1/);assert(nodes.get('broad-daily-body').children.length<50);assert.equal(nodes.get('broad-daily-next').disabled,true);
many=true;nodes.get('broad-apply').click();await settle();assert.equal(nodes.get('broad-members-body').children.length,20);assert.match(nodes.get('broad-members-page').textContent,/46 只 ETF/);
const beforeMembers=calls.length,mainTotal=nodes.get('broad-total').children[0].children[1].textContent;nodes.get('broad-members-next').click();assert.equal(calls.length,beforeMembers);assert.match(nodes.get('broad-members-page').textContent,/第 2/);assert.equal(nodes.get('broad-total').children[0].children[1].textContent,mainTotal);
assert.deepEqual(ranked(0).map(button=>button.dataset.fundCode),['510044','510043','510042']);
assert.deepEqual(ranked(1).map(button=>button.dataset.fundCode),['510000','510001','510002']);
const positiveMeter=ranked(0)[0].children[2],negativeMeter=ranked(1)[0].children[2];
assert.equal(positiveMeter.max,negativeMeter.max);assert(positiveMeter.value>negativeMeter.value,'bars compare absolute flows on one scale');
expectRevealed('broad-list-title',1);const memberScrolls=scrollCount();setMoneyUnit('yi');setMoneyUnit('auto');assert.equal(scrollCount(),memberScrolls);assert.match(nodes.get('broad-members-page').textContent,/第 2/);nodes.get('broad-members-prev').click();expectRevealed('broad-list-title',2);
nodes.get('broad-members-sort').value='outflow';nodes.get('broad-members-sort').listeners.change();assert.match(nodes.get('broad-members-page').textContent,/第 1/);assert.equal(nodes.get('broad-members-body').children[0].dataset.fundCode,'510000');assert.equal(calls.length,beforeMembers);
nodes.get('broad-search').value='510045';nodes.get('broad-search').listeners.input();assert.equal(nodes.get('broad-members-body').children.length,1);assert.equal(nodes.get('broad-members-body').children[0].children[1].children[0].textContent,'—');assert.equal(nodes.get('broad-members-next').disabled,true);
assert.equal(ranked(0)[0].dataset.fundCode,'510044','rankings always cover the whole selected index');
nodes.get('broad-search').value='不存在';nodes.get('broad-search').listeners.input();assert.match(nodes.get('broad-members-body').children[0].children[0].textContent,/没有匹配/);assert.equal(nodes.get('broad-total').children[0].children[1].textContent,mainTotal,'member search never filters the index aggregate');nodes.get('broad-search').value='';nodes.get('broad-search').listeners.input();
hold=true;nodes.get('broad-apply').click();const cancelled=pending.at(-1);nodes.get('broad-start').value='2026-11-10';nodes.get('broad-apply').click();cancelled.resolve(response(indexResponse(cancelled.u)));await settle();assert.match(nodes.get('broad-status').textContent,/日期范围无效/);assert.equal(nodes.get('broad-chart').children.length,0,'a late valid request cannot resurrect data after invalid dates');assert.equal(nodes.get('broad-total').attributes['aria-busy'],'false');hold=false;periods[0].click();await settle();assert.equal(nodes.get('broad-status').textContent,'');
function editDates(start,end){
  nodes.get('broad-start').value=start;nodes.get('broad-start').listeners.input();
  nodes.get('broad-end').value=end;nodes.get('broad-end').listeners.input();
}
function expectRange(index,start,end){
  const request=calls.filter(url=>url.pathname.includes('/broad/index/')).at(-1);
  assert.equal(request.pathname,'/api/v1/broad/index/'+index);
  assert.equal(request.searchParams.get('start'),start);
  assert.equal(request.searchParams.get('end'),end);
  assert.equal(nodes.get('broad-start').value,start);
  assert.equal(nodes.get('broad-end').value,end);
  assert(nodes.get('broad-total').children[1].children[0].textContent.includes(start+' 至 '+end));
  assert.equal(nodes.get('broad-status').textContent,'');
}
const openMembers=nodes.get('broad-open-history');
const beforeSectionJump=calls.length,beforeEvents=events.length;
openMembers.click();
assert.equal(document.activeElement,nodes.get('broad-list-title'));
assert.equal(events.length,beforeEvents);
for(const targetId of ['broad-heading','broad-list-title','broad-offshore-title','broad-daily-heading']){
  const heading=document.getElementById(targetId);
  const disclosure=targetId==='broad-offshore-title'||targetId==='broad-daily-heading'?{open:false}:null;
  heading.closest=selector=>selector==='details'?disclosure:null;
  const link={getAttribute:()=> '#'+targetId};
  const event={target:{closest:()=>link},preventDefault(){this.defaultPrevented=true;}};
  nodes.get('broad-section-links').listeners.click(event);
  assert.equal(event.defaultPrevented,true);
  if(disclosure)assert.equal(disclosure.open,true);
  assert.equal(document.activeElement,heading);
}
assert.equal(calls.length,beforeSectionJump,'section navigation uses already-loaded results');
dense=false;many=false;
const beforeDraft=calls.length;
editDates('2026-11-06','2026-11-09');
assert.equal(calls.length,beforeDraft,'typing keeps explicit Apply semantics');
assert.match(nodes.get('broad-status').textContent,/日期待应用.*当前结果为 2026-10-09 至 2026-11-09/);
setMoneyUnit('yi');setMoneyUnit('auto');
assert.match(nodes.get('broad-status').textContent,/日期待应用/,'a display-unit change preserves the pending reminder');
nodes.get('broad-index').value='000001';nodes.get('broad-index').listeners.change();await settle();
expectRange('000001','2026-11-06','2026-11-09');
assert(periods.every(button=>button.attributes['aria-pressed']==='false'),'a custom date range has no shortcut selected');

editDates('2026-11-09','2026-11-06');
const beforeInvalidSwitch=calls.length;
nodes.get('broad-index').value='000300';nodes.get('broad-index').listeners.change();await settle();
nodes.get('refresh').click();await settle();
assert.equal(calls.length,beforeInvalidSwitch);
assert.match(nodes.get('broad-status').textContent,/日期范围无效/);
assert.equal(nodes.get('broad-chart').children.length,0);
assert.equal(nodes.get('broad-offshore-table').children.length,0);
assert.equal(openMembers.disabled,true);
editDates('2026-11-06','2026-11-06');nodes.get('refresh').click();await settle();
expectRange('000300','2026-11-06','2026-11-06');
const noEstimate=nodes.get('broad-summary').children[2];
assert.equal(noEstimate.children[0].textContent,'2026-11-06 · 应有 2 只 ETF');
assert.equal(noEstimate.children[1].textContent,'当日暂无估算');
assert.equal(nodes.get('broad-total').children[1].children[2].children[1].textContent,'当日暂无估算','missing final date is explicit before opening coverage');
assert.equal(nodes.get('broad-total').children[0].children[1].textContent,'—');

editDates('2026-11-07','2026-11-08');nodes.get('broad-apply').click();await settle();
expectRange('000300','2026-11-07','2026-11-08');
assert.equal(nodes.get('broad-summary').children[2].children[1].textContent,'无交易日','a weekend has no session for which to estimate member coverage');
hold=true;
editDates('2026-11-06','2026-11-09');nodes.get('broad-apply').click();
const submitted=pending.at(-1);
editDates('2026-11-09','2026-11-09');
submitted.resolve(response(indexResponse(submitted.u)));await settle();hold=false;
assert.match(nodes.get('broad-status').textContent,/日期待应用.*当前结果为 2026-11-06 至 2026-11-09/,'a late response does not erase a date edit');
fixtureFlow=-10000;
nodes.get('refresh').click();await settle();expectRange('000300','2026-11-09','2026-11-09');
assert.match(nodes.get('broad-total').children[0].children[0].textContent,/净流出/);
fixtureFlow=0;
nodes.get('broad-apply').click();await settle();
assert.notEqual(nodes.get('broad-total').children[0].children[1].textContent,'—','a real zero is shown as an amount');
assert.doesNotMatch(nodes.get('broad-total').children[0].children[0].textContent,/净流入|净流出/);
assert.match(nodes.get('broad-drivers-period').textContent,/1 只净额为零/);
assert.match(ranks.children[0].children[1].textContent,/无净流入/);
editDates('2026-11-08','2026-11-07');periods[1].click();await settle();
expectRange('000300','2026-08-09','2026-11-09');
assert.equal(periods[1].attributes['aria-pressed'],'true','a shortcut replaces pending dates and restores its selected state');
fixtureFlow=10000;
version='v2';nodes.get('broad-apply').click();await settle();assert.equal(nodes.get('broad-chart').children.length,0);assert.equal(nodes.get('broad-daily-body').children.length,0);assert.match(nodes.get('broad-chart-state').textContent,/版本已更新/);
assert.equal(nodes.get('broad-total').children[0].children[1].textContent,'—');assert.equal(nodes.get('broad-members-body').children.length,0);
assert.equal(nodes.get('broad-total').attributes['aria-busy'],'false','an error is not left announced as still loading');
assert.equal(ranks.hidden,true);
// Source-date latency or failure must not prevent the core chart from loading.
version='v1';sourceMode='deferred';nodes.get('refresh').click();await settle();
assert.equal(sourcePending.length,1);
assert.equal(nodes.get('broad-chart').children.length,1,'the chart loads while source-date metadata is pending');
const freshnessCalls=calls.filter(url=>url.pathname==='/api/v1/history/meta').length;
periods[0].click();await settle();nodes.get('broad-index').value='000001';nodes.get('broad-index').listeners.change();await settle();
assert.equal(calls.filter(url=>url.pathname==='/api/v1/history/meta').length,freshnessCalls,'period and index changes reuse the current source-date request');
assert.match(nodes.get('broad-asof').textContent,/指数行情截至 2026-11-05/);
linkedHistory='h2';version='v2';nodes.get('refresh').click();await settle();assert.equal(sourcePending.length,2);
sourcePending[1](response(historyMetadata('h2','2026-11-07')));await settle();
assert.match(nodes.get('broad-asof').textContent,/全目录ETF份额截至 2026-11-07/);
const currentSourceText=nodes.get('broad-asof').textContent;
sourcePending[0](response(historyMetadata('h1','2026-11-08')));await settle();
assert.equal(nodes.get('broad-asof').textContent,currentSourceText,'an older source metadata response cannot overwrite the current version');
sourceMode='mismatch';nodes.get('refresh').click();await settle();
assert.match(nodes.get('broad-asof').textContent,/全目录ETF份额截至 暂无 · 全目录净值截至 暂无/);
assert.equal(nodes.get('broad-chart').children.length,1,'mismatched source metadata does not fail the verified main graph');
sourceMode='failure';nodes.get('refresh').click();await settle();
assert.match(nodes.get('broad-asof').textContent,/全目录ETF份额截至 暂无/);
assert.equal(nodes.get('broad-chart').children.length,1,'unavailable source metadata leaves the main graph usable');
console.log('PASS: estimated flow labels and coverage, pending/applied dates, index and refresh consistency, section jumps, first-column drilldown, scoped history, pagination, stale-response protection');
