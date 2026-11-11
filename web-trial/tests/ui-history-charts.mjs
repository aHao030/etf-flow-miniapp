import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setMoneyUnit} from '../public/logic.mjs';
class Node {
  constructor(tag='div') { this.tag=tag;this.children=[];this.listeners={};this.dataset={};this.textContent='';this.value='';this.attributes={};this.classList={toggle(){}}; }
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;this.textContent='';}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(name,value){this.attributes[name]=value;}
  scrollIntoView(){}
  click(){this.listeners.click?.({});}
}
const nodes=new Map(), events={}, metrics=['flow','nav','shares'].map(historyMetric=>Object.assign(new Node('button'),{dataset:{historyMetric}}));
globalThis.document={
  getElementById(id){if(id==='history-tab')return null;if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},
  createElement:tag=>new Node(tag),querySelectorAll:selector=>selector==='[data-history-metric]'?metrics:[],
  addEventListener:(type,fn)=>events[type]=fn
};
const renderings=[];
globalThis.__historyTestRender=(container,rows,options)=>{renderings.push({rows,options});container.append(new Node('svg'));return ()=>container.replaceChildren();};
const response=data=>({ok:true,json:async()=>({code:0,data})});
const tables=[], charts=[], calls=[];
const fund=code=>({fund_code:code,fund_name:'ETF '+code,market:'SH',shares:{count:120},nav:{count:120}});
globalThis.fetch=async url=>{
  const u=new URL(url,'https://local.test');calls.push(u.pathname+u.search);
  if(u.pathname.endsWith('/broad/calendar'))return response({start:'2021-01-01',end:'2026-12-31',dates:Array.from({length:2191},(_,i)=>new Date(Date.UTC(2021,0,1)+i*86400000)).filter(d=>![0,6].includes(d.getUTCDay())).map(d=>d.toISOString().slice(0,10))});
  if(u.pathname.endsWith('/meta'))return response({target_start:'2021-01-01',target_end:'2026-11-09',total_funds:2,shares_funds:2,nav_funds:2,notes:[]});
  if(u.pathname.endsWith('/etfs'))return response({items:[fund('510300')],total:1,page:1,page_size:20});
  if(u.pathname.endsWith('/chart'))return new Promise(resolve=>charts.push({url:u,resolve}));
  if(u.pathname.includes('/etf/'))return new Promise(resolve=>tables.push({url:u,resolve}));
  throw Error('Unexpected request');
};
// The chart renderer is tested separately; keep this test focused on API/version/race orchestration.
let source=await readFile(new URL('../public/history.mjs',import.meta.url),'utf8');
source=source.replace("from './logic.mjs'",'from '+JSON.stringify(new URL('../public/logic.mjs',import.meta.url).href))
  .replace("from './calendar.mjs'",'from '+JSON.stringify(new URL('../public/calendar.mjs',import.meta.url).href))
  .replace("import { renderTimeChart } from './charts.mjs';",'const renderTimeChart=globalThis.__historyTestRender;');
await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
function detail(request,version='v1'){
  return {version,fund:fund(request.url.pathname.split('/').at(-1)),start:request.url.searchParams.get('start'),end:request.url.searchParams.get('end'),
    total:120,page:Number(request.url.searchParams.get('page')),page_size:100,items:[],coverage:{shares:{count:120},nav:{count:119}},notes:[]};
}
function chartData(d){
  return {...d,series:Array.from({length:120},(_,i)=>({trade_date:new Date(Date.UTC(2022,0,1+i)).toISOString().slice(0,10),shares:100+i,unit_nav:i===7?null:1.12345678,estimated_flow:i===7?null:0,quality:i===7?'missing_current_nav':'estimated'}))};
}
await settle();assert.equal(calls.length,0);events['etf-open-history']({detail:fund('510300')});await settle();
const first=detail(tables[0]);tables[0].resolve(response(first));await settle();
assert.equal(charts.length,1);assert.match(nodes.get('history-chart-state').textContent,/完整范围/);
nodes.get('history-next').click();tables[1].resolve(response({...first,page:2}));await settle();
assert.equal(charts.length,1,'table pagination must not restart a pending full-range chart');
charts[0].resolve(response(chartData(first)));await settle();
assert.equal(renderings.at(-1).rows.length,120,'chart receives full range, not the table page');
assert.equal(renderings.at(-1).rows[7].value,null);assert.equal(renderings.at(-1).rows[8].value,0);
metrics[1].click();assert.equal(renderings.at(-1).options.label,'单位净值');assert.equal(renderings.at(-1).rows[0].value,1.12345678);
metrics[2].click();assert.equal(renderings.at(-1).options.unit,'份');assert.equal(charts.length,1,'metric switch uses the same verified range');
const beforeUnitCalls=calls.length,beforeUnitRenders=renderings.length;setMoneyUnit('yi');
assert.equal(calls.length,beforeUnitCalls);assert.equal(renderings.length,beforeUnitRenders+1,'unit changes redraw cached chart without fetching');
assert.equal(renderings.at(-1).options.unit,'份');setMoneyUnit('auto');
nodes.get('history-start').value='2022-01-01';nodes.get('history-end').value='2022-05-01';nodes.get('history-apply').click();
assert.equal(nodes.get('history-chart').children.length,0,'date change clears obsolete chart immediately');
const second=detail(tables[2]);tables[2].resolve(response(second));await settle();
events['etf-open-history']({detail:fund('159915')});
assert.equal(nodes.get('broad-view').hidden,true);assert.equal(nodes.get('history-view').hidden,false);
const third=detail(tables[3]);tables[3].resolve(response(third));await settle();
const renderCount=renderings.length;charts[1].resolve(response(chartData(second)));await settle();
assert.equal(renderings.length,renderCount,'prior ETF chart response cannot overwrite current ETF');
charts[2].resolve(response(chartData(third)));await settle();assert.equal(renderings.length,renderCount+1);
nodes.get('history-apply').click();const fourth=detail(tables[4],'v3');tables[4].resolve(response(fourth));await settle();
charts[3].resolve(response({...chartData(fourth),version:'v4'}));await settle();
assert.equal(nodes.get('history-chart').children.length,0);assert.match(nodes.get('history-chart-state').children[0].textContent,/版本不一致/);
nodes.get('history-start').value='2025-01-01';nodes.get('history-end').value='2024-01-01';nodes.get('history-apply').click();
assert.equal(nodes.get('history-chart').children.length,0);assert.match(nodes.get('history-chart-state').textContent,/日期范围无效/);
const invalidCount=renderings.length;setMoneyUnit('yi');assert.equal(renderings.length,invalidCount,'invalid range cannot resurrect old cached chart');setMoneyUnit('auto');
console.log('PASS: full-range chart, null/zero, metric switch, pagination, stale fund response, version mismatch, date validation');
