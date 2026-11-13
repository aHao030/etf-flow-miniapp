import assert from 'node:assert/strict';
class Node {
  constructor(tag='div') {
    this.tag=tag; this.children=[]; this.listeners={}; this.dataset={}; this.hidden=false;
    this.disabled=false; this.value=''; this.textContent=''; this.className=''; this.attributes={};
    this.classList={toggle(){}};
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=[...items]; this.textContent=''; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  setAttribute(key, value) { this.attributes[key]=value; }
  querySelector() { return null; }
  scrollIntoView() {} focus() {} insertAdjacentText() {} remove() {}
  click() { for (const fn of this.listeners.click || []) fn({}); }
}
const nodes = new Map(), events = {};
const controls = {
  '[data-stock-period]':['1d','5d','20d'].map(x => Object.assign(new Node('button'),{dataset:{stockPeriod:x}})),
  '[data-rank-period]':['1d','5d','20d'].map(x => Object.assign(new Node('button'),{dataset:{rankPeriod:x}})),
  '[data-direction]':['in','out'].map(x => Object.assign(new Node('button'),{dataset:{direction:x}}))
};
globalThis.document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id,new Node()); return nodes.get(id); },
  createElement: tag => new Node(tag), createElementNS: (_,tag) => new Node(tag),
  querySelectorAll: selector => controls[selector] || [],
  addEventListener: (type,fn) => events[type] = fn
};
globalThis.localStorage = {getItem(){return null;},setItem(){}};
const response = data => ({ok:true,json:async()=>({code:0,data})});
const calls = [];
let version = {end:'2026-10-30', total:1813, stock:'2026-10-30'}, metaMode='normal';
const pendingMeta = [];
function metadata(v) { return {target_start:'2021-01-01',target_end:v.end,generated_at:v.end+'T12:00:00Z',total_funds:v.total,shares_funds:2,nav_funds:2,shares_rows:3,nav_rows:3,complete:false,notes:[]}; }
const fund = {fund_code:'510300',fund_name:'沪深300ETF',market:'SH',lifecycle_status:'current_seen',shares:{count:1},nav:{count:1}};
globalThis.fetch = async url => {
  const u = new URL(String(url),'http://localhost'); calls.push(u.pathname+u.search);
  if (u.pathname==='/api/v1/history/meta') {
    if (metaMode==='deferred') return new Promise(resolve => pendingMeta.push(resolve));
    if (metaMode==='failure') return {ok:false,json:async()=>({code:50301,message:'历史暂不可用'})};
    return response(metadata(version));
  }
  if (u.pathname==='/api/v1/history/etfs') return response({items:[fund],total:version.total,page:Number(u.searchParams.get('page')),page_size:20});
  if (u.pathname==='/api/v1/history/etf/510300') return response({fund,start:u.searchParams.get('start'),end:u.searchParams.get('end'),total:1,page:1,page_size:100,items:[{trade_date:u.searchParams.get('end'),shares:123,unit_nav:1,acc_nav:1,estimated_flow:null,quality:'raw_observation'}],coverage:{shares:{count:1},nav:{count:1}},notes:[]});
  if (u.pathname==='/api/v1/market/meta') return response({latest_date:version.stock,generated_at:version.stock+'T07:45:00Z',coverage:{},disclosure:{}});
  if (u.pathname==='/api/v1/market/industries') return response({items:[]});
  if (u.pathname==='/api/v1/market/rank') return response({items:[],total:0,has_more:false});
  throw Error('Unexpected URL: '+u.pathname);
};
await import('../public/app.mjs'); await import('../public/history.mjs');
const settle = () => new Promise(resolve => setTimeout(resolve,0));
await settle();
assert.equal(nodes.get('refresh').listeners.click.length,2,'both modules retain their listener');
assert(!calls.some(x=>x.startsWith('/api/v1/history/')),'history stays dormant until a fund drilldown');
events['etf-open-history']({detail:fund}); await settle();
assert.equal(nodes.get('history-end').value,'2026-10-30');
version = {end:'2026-11-02',total:1815,stock:'2026-11-02'};
const before = calls.length; nodes.get('refresh').click(); await settle();
const refreshed = calls.slice(before);
assert(refreshed.some(x => x==='/api/v1/market/meta'));
assert(refreshed.some(x => x==='/api/v1/history/meta'));
assert(!refreshed.some(x => x.startsWith('/api/v1/history/etfs?')),'refresh does not fetch removed directory');
assert(refreshed.some(x => x.includes('/api/v1/history/etf/510300?') && x.includes('end=2026-11-02')));
assert.equal(nodes.get('trade-date').textContent,'暂无','older market metadata without a linked history never supplies an actual source date');
assert.equal(nodes.get('refresh-time').dateTime,'');
assert.equal(nodes.get('refresh-time').textContent,'暂无','batch generation time is never labelled as source update time');
assert.match(nodes.get('history-asof').textContent,/2026-11-02/);
assert.match(nodes.get('history-summary').children[0].children[1].textContent,/1,815/);
assert.equal(nodes.get('history-end').value,'2026-11-02');
// Editing an unapplied start date also preserves the default end date and the edit.
nodes.get('history-start').value='2022-05-01';
version={end:'2026-11-03',total:1816,stock:'2026-11-03'};
const unsavedBefore=calls.length; nodes.get('refresh').click(); await settle();
assert.equal(nodes.get('history-start').value,'2022-05-01');
assert.equal(nodes.get('history-end').value,'2026-11-02');
assert(calls.slice(unsavedBefore).some(x=>x.includes('/history/etf/510300?') && x.includes('start=2021-01-01') && x.includes('end=2026-11-02')));
// An explicitly applied end date stays fixed even if it matched the latest date.
nodes.get('history-start').value='2021-01-01'; nodes.get('history-end').value='2026-11-03'; nodes.get('history-apply').click(); await settle();
version={end:'2026-11-04',total:1817,stock:'2026-11-04'};
const explicitBefore=calls.length; nodes.get('refresh').click(); await settle();
assert.equal(nodes.get('history-end').value,'2026-11-03');
assert(calls.slice(explicitBefore).some(x=>x.includes('/history/etf/510300?') && x.includes('end=2026-11-03')));
// An explicitly applied range remains fixed after the source advances.
nodes.get('history-start').value='2022-01-01'; nodes.get('history-end').value='2025-12-31'; nodes.get('history-apply').click(); await settle();
version={end:'2026-11-03',total:1816,stock:'2026-11-03'};
const customBefore=calls.length; nodes.get('refresh').click(); await settle();
assert.equal(nodes.get('history-end').value,'2025-12-31');
assert(calls.slice(customBefore).some(x=>x.includes('/history/etf/510300?') && x.includes('start=2022-01-01') && x.includes('end=2025-12-31')));
// An unsaved edit is not overwritten, and the currently applied range is refreshed.
nodes.get('history-end').value='2024-03-01';
version={end:'2026-11-04',total:1817,stock:'2026-11-04'};
const editBefore=calls.length; nodes.get('refresh').click(); await settle();
assert.equal(nodes.get('history-end').value,'2024-03-01');
assert(calls.slice(editBefore).some(x=>x.includes('/history/etf/510300?') && x.includes('end=2025-12-31')));
// Two overlapping metadata requests must leave the newest response in control.
metaMode='deferred'; nodes.get('refresh').click(); nodes.get('refresh').click(); await settle();
assert.equal(pendingMeta.length,2);
pendingMeta[1](response(metadata({end:'2026-11-06',total:1819}))); await settle();
pendingMeta[0](response(metadata({end:'2026-11-05',total:1818}))); await settle();
assert.match(nodes.get('history-asof').textContent,/2026-11-06/);
// Failure is visible and its retry loads the historical metadata again.
metaMode='failure'; nodes.get('refresh').click(); await settle();
assert.match(nodes.get('history-asof').children[0].textContent,/历史暂不可用/);
metaMode='normal'; version={end:'2026-11-07',total:1820,stock:'2026-11-07'};
nodes.get('history-asof').children[2].click(); await settle();
assert.match(nodes.get('history-asof').textContent,/2026-11-07/);
console.log('PASS: shared refresh, history range preservation, races and error recovery');
