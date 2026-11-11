import assert from 'node:assert/strict';
import { money, signClass, latestOnly, formatWeight, weightLabel } from '../public/logic.mjs';
assert.equal(money(null), '—'); assert.equal(money(undefined), '—'); assert.equal(money(0), '0.00 万元');
assert.equal(money(-12000), '-1.20 万元'); assert.equal(signClass(-1), 'negative'); assert.equal(formatWeight(null), '—');
assert.equal(weightLabel('index_csindex(000300)'), '指数权重'); assert.equal(weightLabel('report(2026Q2)'), '定期披露持仓');
const guard = latestOnly(), old = guard.next(), fresh = guard.next(); assert(!guard.current(old)); assert(guard.current(fresh));
class Node {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.hidden = false; this.disabled = false; this.value = ''; this.textContent = ''; this.attributes = {}; this.style = {}; this.clientWidth = 800; this.className = ''; this.classList = { toggle: () => {} }; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; this.textContent = ''; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  removeEventListener(type) { delete this.listeners[type]; }
  getBoundingClientRect() { return {left:0,width:800}; }
  setAttribute(key, value) { this.attributes[key] = value; }
  querySelector(selector) { return selector === '.search-more' ? this.children.find(x => x.className === 'result search-more') : null; }
  remove() {} focus() {this.focusCount=(this.focusCount||0)+1;} scrollIntoView() {this.scrollCount=(this.scrollCount||0)+1;} insertAdjacentText(position, text) { this.append(text); }
  click() { if (this.listeners.click) this.listeners.click({}); }
}
const nodes = new Map();
const controls = {
  '[data-stock-period]': ['1d','5d','20d','3m','6m','1y','2y','3y','5y'].map(x => Object.assign(new Node('button'), {dataset:{stockPeriod:x}})),
  '[data-rank-period]': ['1d','5d','20d'].map(x => Object.assign(new Node('button'), {dataset:{rankPeriod:x}})),
  '[data-direction]': ['in','out'].map(x => Object.assign(new Node('button'), {dataset:{direction:x}}))
};
globalThis.document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id,new Node()); return nodes.get(id); }, createElement: tag => new Node(tag), createElementNS: (_,tag) => new Node(tag), querySelectorAll: selector => controls[selector] || [] };
const saved = new Map(); globalThis.localStorage = { getItem: key => saved.get(key) || null, setItem: (key,value) => saved.set(key,value) };
const calls = [];
const stock = period => ({stock_code:'300750',stock_name:'<img src=x onerror=alert(1)>',industry_name:'电子',trade_date:'2026-10-29',data_status:'ok',movement:{period,net_flow:123,inflow:123,outflow:0,valid_days:1,window_days:1,complete:true},coverage:{tracked_etfs:1,pending_etfs:0},quality:{},series:[{trade_date:'2026-10-29',flow:123}],etf_contributions:[{etf_code:'510300',etf_name:'ETF',flow:123,weight:0.1,weight_source:'index',status:'complete',valid_days:1}]});
globalThis.fetch = async url => { calls.push(String(url)); const u = new URL(String(url), 'http://localhost'); let data;
  if (u.pathname.endsWith('/broad/calendar')) data={start:'2021-01-01',end:'2026-12-31',dates:Array.from({length:2191},(_,i)=>new Date(Date.UTC(2021,0,1)+i*86400000)).filter(d=>![0,6].includes(d.getUTCDay())).map(d=>d.toISOString().slice(0,10))};
  else if (u.pathname.endsWith('/meta')) data={latest_date:'2026-10-29',coverage:{tracked_etfs:18,active_etfs:13,pending_etfs:5},disclosure:{short:'估算'}};
  else if (u.pathname.endsWith('/industries')) data={items:[{industry_code:'1',industry_name:'电子'}]};
  else if (u.pathname.endsWith('/rank')) data={total:1,has_more:false,items:[{rank:1,stock_code:'300750',stock_name:'宁德时代',flow:123}]};
  else if (u.pathname.endsWith('/stocks')) data={items:[{stock_code:'300750',stock_name:'宁德时代',flow_1d:123}],has_more:false};
  else if (u.pathname.endsWith('/stock/300750')) data=stock(u.searchParams.get('period'));
  else if (u.pathname.endsWith('/etf/510300')) data={etf_name:'ETF',trade_date:'2026-10-29',est_flow:10};
  else throw Error('Unexpected URL ' + u.pathname);
  return { ok:true, json:async()=>({code:0,data}) };
};
await import('../public/app.mjs');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
await settle(); assert.equal(nodes.get('trade-date').textContent, '2026-10-29');
assert.match(nodes.get('coverage').textContent, /已有 A 股披露持仓 13 只/);
assert.equal(nodes.get('rank-list').children.length,1);
nodes.get('rank-list').children[0].click(); await settle();
assert(calls.some(url=>url.includes('/stock/300750?period=20d')),'new visits open a twenty-day trend');
assert.equal(nodes.get('stock-heading').focusCount,1);
assert.equal(nodes.get('stock-heading').textContent, '<img src=x onerror=alert(1)>  300750');
assert.equal(nodes.get('stock-content').hidden,false); assert.equal(nodes.get('etf-body').children.length,1);
controls['[data-stock-period]'][1].click(); await settle(); assert(calls.some(x => x.includes('/stock/300750?period=5d')));
nodes.get('watch-toggle').click(); assert.match(saved.get('etf-flow-watch-v1'), /300750/);
nodes.get('watch-list').children[0].click();await settle();assert.equal(nodes.get('stock-heading').focusCount,2);assert.equal(nodes.get('stock-heading').scrollCount,2);assert.match(calls.filter(url=>url.includes('/stock/300750?')).at(-1),/period=5d/,'watch selection preserves chosen period');
controls['[data-direction]'][1].click(); await settle(); assert(calls.some(x => x.includes('direction=out')));
nodes.get('search').value = '宁德'; nodes.get('search').listeners.keydown({key:'Enter',preventDefault(){}}); await settle(); assert.equal(nodes.get('search-results').children.length,1);
nodes.get('search-results').children[0].click();await settle();assert.equal(nodes.get('stock-heading').focusCount,3);assert.equal(nodes.get('stock-heading').scrollCount,3);
const normalFetch = globalThis.fetch, pending = [];
globalThis.fetch = url => String(url).includes('/stock/300750?period=') ? new Promise(resolve => pending.push({url:String(url),resolve})) : normalFetch(url);
controls['[data-stock-period]'][0].click(); controls['[data-stock-period]'][2].click();
assert.equal(pending.length, 2);
const response = data => ({ok:true,json:async()=>({code:0,data})});
const recent = stock('20d'); recent.stock_name = '最新结果'; pending[1].resolve(response(recent)); await settle();
const stale = stock('1d'); stale.stock_name = '过期结果'; pending[0].resolve(response(stale)); await settle();
assert.equal(nodes.get('stock-heading').textContent, '最新结果  300750');
globalThis.fetch = normalFetch;
const longRows = Array.from({length:1200},(_,i)=>({trade_date:new Date(Date.UTC(2021,0,1)+i*86400000).toISOString().slice(0,10),flow:1}));
globalThis.fetch = async url => {
  if (!String(url).includes('/stock/300750?period=5y')) return normalFetch(url);
  const long = stock('5y'); long.series = longRows;
  long.etf_contributions=Array.from({length:42},(_,i)=>({etf_code:String(510300+i),etf_name:'ETF '+i,flow:i===41?null:i===40?-100000:123,weight:.1,weight_source:'index',weight_date:'2026-06-30',status:'partial',valid_days:1}));
  long.movement = {net_flow:null,inflow:null,outflow:null,observed_net_flow:1200,window_start:'2021-01-01',window_end:'2026-10-30',start_exclusive:true,available_start:'2021-01-02',available_end:'2024-04-14',valid_days:1199,window_days:null,missing_days:null,complete:false};
  return response(long);
};
controls['[data-stock-period]'][8].click(); await settle();
assert.equal(nodes.get('stock-net').textContent,'+0.12 万元');
assert.match(nodes.get('observed-total').textContent,/已有记录累计/);
assert.match(nodes.get('window-coverage').textContent,/缺口：未知/);
assert.match(nodes.get('chart-start').textContent,/2021-01-01（不含）/);
assert.equal(nodes.get('chart-end').textContent,'2026-10-30');
assert.match(nodes.get('chart-description').textContent,/日期与金额可逐日查看/);
assert.equal(nodes.get('series-body').children.length,50);
assert.equal(nodes.get('series-body').children[0].children[0].textContent,'2026-10-30','latest day opens the financial table');
assert.match(nodes.get('stock-latest-observation').textContent,/2026-10-30 暂无可估算记录，本期最近有效日 2024-04-12/);assert.equal(nodes.get('stock-latest-observation').hidden,false);
const bars=nodes.get('contribution-chart');assert.equal(bars.children.filter(n=>n.className==='contribution-row').length,10);
assert.match(bars.children[0].children[0].textContent,/10 \/ 41/);assert.match(bars.children[1].children[0].textContent,/ETF 40/,'largest absolute contribution includes outflow');
assert.match(bars.children[0].children[1].textContent,/全部 42/);bars.children[0].children[1].click();assert.equal(nodes.get('stock-etf-columns').focusCount,1);
const chartFrame=nodes.get('chart').children[0], chartSvg=chartFrame.children.find(n=>n.tag==='svg'), slider=chartFrame.children.find(n=>n.tag==='input');
assert.equal(chartSvg.children.filter(n=>n.tag==='rect').length,longRows.filter(r=>r.trade_date>'2021-01-01'&&![0,6].includes(new Date(r.trade_date+'T00:00:00Z').getUTCDay())).length);
assert(chartSvg.children.filter(n=>n.tag==='text').length>=10);
slider.value='0';slider.listeners.input();
assert.match(chartFrame.children[0].children[0].textContent,/2021-01-04/);
assert.equal(chartFrame.children[0].children[1].textContent,'+0.0001 万元');
nodes.get('series-next').click(); assert.equal(nodes.get('series-body').children.length,50);
assert(nodes.get('series-body').children[0].children[0].textContent<'2026-10-30');
assert.equal(nodes.get('series-heading').focusCount,1);assert.equal(nodes.get('series-heading').scrollCount,1);
assert.match(nodes.get('series-page').textContent,/第 2 页/);
const callsBeforePaging=calls.length;
assert.equal(nodes.get('etf-body').children.length,20);
nodes.get('stock-etf-next').click();assert.equal(nodes.get('etf-body').children.length,20);
nodes.get('stock-etf-next').click();assert.equal(nodes.get('etf-body').children.length,2);assert.equal(nodes.get('stock-etf-next').disabled,true);
assert.equal(calls.length,callsBeforePaging,'contribution paging uses verified loaded data');
nodes.get('etf-body').children[1].children[0].children[0].click();
assert.equal(calls.length,callsBeforePaging,'ETF contribution details never request the nonexistent market/etf route');
assert.match(nodes.get('etf-detail').children[1].textContent,/期间估算贡献：—/,'missing contribution is not zero');
assert.match(nodes.get('etf-detail').children[2].textContent,/2021-01-01（不含）/,'ETF contribution keeps the fund period exclusion boundary');
assert.match(nodes.get('etf-detail').children[3].textContent,/2026-06-30/);
let historyEvent;
document.dispatchEvent=event=>{historyEvent=event;};
nodes.get('etf-detail').children.find(child=>child.id==='stock-fund-history').click();
assert.equal(historyEvent.detail.return_view,'stock');
assert.equal(historyEvent.detail.range_start,'2021-01-02','exclusive stock boundary maps to the next inclusive fund date');
assert.equal(historyEvent.detail.range_end,'2026-10-30');
nodes.get('stock-etf-prev').click();assert.equal(nodes.get('etf-body').children.length,20);
console.log('PASS: formatting, stale responses, search, exact chart axes/date/amount, keyboard date inspection and paginated table');
