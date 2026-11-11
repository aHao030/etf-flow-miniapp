import assert from 'node:assert/strict';
class Node {
  constructor(tag='div'){Object.assign(this,{tag,children:[],listeners:{},dataset:{},hidden:false,disabled:false,value:'',textContent:'',attributes:{},style:{},clientWidth:800,className:'',classList:{toggle(){}}});}
  append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=[...nodes];this.textContent='';}
  addEventListener(type,fn){this.listeners[type]=fn;} removeEventListener(type){delete this.listeners[type];}
  getBoundingClientRect(){return {left:0,width:800};} setAttribute(k,v){this.attributes[k]=v;}
  querySelector(){return null;} remove(){} focus(){} scrollIntoView(){} insertAdjacentText(){}
  click(){this.listeners.click?.({});}
}
const nodes=new Map(),controls={'[data-stock-period]':['1d','5d'].map(p=>Object.assign(new Node('button'),{dataset:{stockPeriod:p}}))};
globalThis.document={getElementById(id){if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},createElement:tag=>new Node(tag),createElementNS:(_,tag)=>new Node(tag),querySelectorAll:s=>controls[s]||[]};
globalThis.localStorage={getItem(){return null;},setItem(){}};
const calls=[],pending=[];const respond=data=>({ok:true,json:async()=>({code:0,data})});
const dates=['2026-11-06','2026-11-09'];
function stock(code,period){return {stock_code:code,stock_name:code,trade_date:'2026-11-09',movement:{window_start:dates[0],window_end:dates[1],trading_dates:dates,observed_net_flow:2e8,valid_days:1,window_days:2},series:[{trade_date:dates[1],flow:2e8}],etf_contributions:[],coverage:{}};}
function price(code,close){return {stock_code:code,start:dates[0],end:dates[1],price_unit:'元/股',adjustment:'none',status:'ready',series:[{trade_date:dates[1],close}],notes:[]};}
globalThis.fetch=async url=>{
  const u=new URL(url,'http://localhost');calls.push(u.pathname+u.search);
  if(u.pathname.startsWith('/api/v1/prices/'))return new Promise(resolve=>pending.push({code:u.pathname.split('/').at(-1),resolve}));
  if(u.pathname.endsWith('/meta'))return respond({latest_date:dates[1],coverage:{},disclosure:{}});
  if(u.pathname.endsWith('/industries'))return respond({items:[]});
  if(u.pathname.endsWith('/rank'))return respond({items:['300750','600519'].map((stock_code,i)=>({rank:i+1,stock_code,stock_name:stock_code,flow:2e8})),total:2});
  if(u.pathname.includes('/market/stock/'))return respond(stock(u.pathname.split('/').at(-1),u.searchParams.get('period')));
  throw Error('Unexpected '+u.pathname);
};
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
await import('../public/app.mjs');await settle();
nodes.get('rank-list').children[0].click();await settle();
assert.equal(nodes.get('stock-content').hidden,false,'fund data displays before price arrives');
nodes.get('rank-list').children[1].click();await settle();
assert.equal(pending.length,2);
pending[1].resolve(respond(price('600519',1600.12)));await settle();
assert.match(nodes.get('price-status').children[0].textContent,/1 个交易日/);
const text=n=>[n.textContent,...n.children.map(x=>typeof x==='string'?x:text(x))].join(' ');
assert.match(text(nodes.get('chart')),/1,600.12 元\/股/);
assert.match(text(nodes.get('chart')),/2.00 亿元/);
assert.equal(nodes.get('series-body').children[0].children[0].textContent,'2026-11-09');
assert.equal(nodes.get('series-body').children[0].children[2].textContent,'1,600.12');
assert.equal(nodes.get('series-body').children[1].children[2].textContent,'—','earlier missing price remains missing');
assert.equal(nodes.get('stock-latest-observation').hidden,true,'target day with an observed amount has no missing-date notice');
pending[0].resolve(respond(price('300750',999)));await settle();
assert.match(text(nodes.get('chart')),/1,600.12 元\/股/,'late response cannot replace selected stock');
assert.doesNotMatch(text(nodes.get('chart')),/999.00 元\/股/);
const before=calls.length;nodes.get('money-unit').value='wan';nodes.get('money-unit').listeners.change();await settle();
assert.equal(calls.length,before,'money-unit changes do not fetch funds or quotes');
assert.match(text(nodes.get('chart')),/20,000.00 万元/);assert.match(text(nodes.get('chart')),/1,600.12 元\/股/);
controls['[data-stock-period]'][1].click();await settle();
assert.equal(pending.length,2,'same range uses already validated price cache');
nodes.get('refresh').click();await settle();assert.equal(pending.length,3,'explicit refresh can retry published prices');
pending[2].resolve(respond(price('300750',100)));await settle();
assert.match(text(nodes.get('price-status')),/身份或日期范围不匹配/);
assert.equal(nodes.get('stock-content').hidden,false);assert.doesNotMatch(text(nodes.get('chart')),/1,600.12 元\/股/);
console.log('PASS stock overlay: independent loading, aligned true gaps, identity checks, stale request suppression, cache and unit-only redraw');
