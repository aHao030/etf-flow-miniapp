import assert from 'node:assert/strict';
import {setMoneyUnit} from '../public/logic.mjs';
class Node {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.listeners={}; this.textContent=''; this.value=''; this.hidden=false; this.disabled=false; this.attributes={}; this.dataset={}; this.classList={toggle(){}}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=[...children]; this.textContent=''; }
  addEventListener(type,fn) { this.listeners[type]=fn; }
  setAttribute(key,value) { this.attributes[key]=value; }
  scrollIntoView() {}
  click() { if(this.listeners.click) this.listeners.click({}); }
}
const nodes=new Map(),events={};
globalThis.document={getElementById(id){if(id==='history-tab')return null;if(!nodes.has(id)) nodes.set(id,new Node());return nodes.get(id);},createElement:tag=>new Node(tag),querySelectorAll(){return [];},addEventListener:(type,fn)=>events[type]=fn};
const calls=[], pending=[], pendingMeta=[];
let deferMeta=false;
const fund=(code,name='示例 ETF')=>({fund_code:code,fund_name:name,market:'SH',listed_date:'2022-01-01',lifecycle_status:'current_seen',shares:{count:3,start:'2022-01-04',end:'2022-01-06'},nav:{count:2,start:'2022-01-04',end:'2022-01-05'},flow_status:'unavailable'});
const response=data=>({ok:true,json:async()=>({code:0,data})});
const metadata={target_start:'2021-01-01',target_end:'2026-10-30',generated_at:'2026-10-31T00:00:00Z',total_funds:1,shares_funds:1,nav_funds:1,shares_rows:3,nav_rows:2,complete:false,notes:['数据尚未齐全']};
globalThis.fetch=async url=>{
  calls.push(String(url)); const u=new URL(String(url),'http://localhost');
  if(u.pathname.endsWith('/history/meta')) return deferMeta ? new Promise(resolve=>pendingMeta.push(resolve)) : response(metadata);
  if(u.pathname.endsWith('/history/etfs')) return response({items:[fund('510300','<img src=x onerror=alert(1)>')],total:21,page:Number(u.searchParams.get('page')),page_size:20});
  if(u.pathname.endsWith('/chart')) return {ok:false,json:async()=>({code:50301,message:'图表测试另行覆盖'})};
  if(u.pathname.includes('/history/etf/')) return new Promise(resolve=>pending.push({url:String(url),resolve}));
  throw Error('Unexpected API '+url);
};
await import('../public/history.mjs');
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
await settle();
assert.equal(calls.length,0,'hidden history must not fetch metadata or directory on startup');
nodes.get('refresh').click();await settle();assert.equal(calls.length,0,'refresh skips unopened history');
const openBroad = (code,start,end)=>events['etf-open-history']({detail:{...fund(code),return_view:'broad',range_start:start,range_end:end}});
const requested = item=>{const url=new URL(item.url,'https://test.local');return [url.pathname.split('/').at(-1),url.searchParams.get('start'),url.searchParams.get('end')];};
openBroad('510500','2005-01-01','2022-05-30'); await settle();
assert.deepEqual(requested(pending[0]),['510500','2021-01-01','2022-05-30'],'first metadata response intersects the originating broad range');
assert.equal(nodes.get('history-end').value,'2022-05-30');
openBroad('159915','1990-01-01','2000-12-31');
assert.match(nodes.get('history-rows-state').children[0].textContent,/没有交集/);
assert.equal(nodes.get('history-start').value,'1990-01-01');
assert.equal(nodes.get('history-back').dataset.returnView,'broad');
assert.equal(pending.length,1,'fully outside range never falls back to an unbounded query');
pending[0].resolve(response({fund:fund('510500'),start:'2021-01-01',end:'2022-05-30'})); await settle();
assert.match(nodes.get('history-fund-heading').textContent,/159915/);
assert.match(nodes.get('history-rows-state').children[0].textContent,/没有交集/,'late reply cannot clear the newer empty state');
nodes.get('refresh').click(); await settle(); assert.equal(pending.length,1,'refresh retains no-intersection state');
deferMeta=true; nodes.get('refresh').click(); await settle();
openBroad('159915','2022-03-01','2023-03-01');
openBroad('510500','2025-01-07','2030-01-01');
assert.equal(pending.length,1,'selection waits for the pending metadata');
deferMeta=false; pendingMeta.shift()(response(metadata)); await settle();
assert.deepEqual(requested(pending[1]),['510500','2025-01-07','2026-10-30'],'last fund and range win when metadata arrives');
pending[1].resolve(response({fund:fund('510500'),start:'2025-01-07',end:'2026-10-30',items:[],total:0,page:1,page_size:100}));await settle();
assert.match(nodes.get('history-fund-subtitle').textContent,/已按可查询范围调整/);
nodes.get('history-start').value='2022-04-01'; nodes.get('history-end').value='2022-09-30'; nodes.get('history-apply').click();
assert.deepEqual(requested(pending[2]),['510500','2022-04-01','2022-09-30']);
const oldCustom=pending[2];pending.length=0;
events['etf-open-history']({detail:{...fund('510300','<img src=x onerror=alert(1)>'),return_view:'stock'}});
oldCustom.resolve(response({fund:fund('510500'),start:'2022-04-01',end:'2022-09-30',items:[],total:0,page:1,page_size:100}));
assert.equal(nodes.get('history-back').dataset.returnView,'stock');
assert.equal(nodes.get('history-back').textContent,'返回个股观察');
assert.equal(nodes.get('history-fund-heading').textContent,'<img src=x onerror=alert(1)>  510300','fund labels remain safe text');
await settle();
assert.equal(nodes.get('history-start').value,'2021-01-01');
assert.equal(nodes.get('history-end').value,'2026-10-30');
assert.match(nodes.get('history-asof').textContent,/尚未完成/);
assert(!calls.some(url=>url.includes('/history/etfs')),'drilldown does not load the removed directory');
assert.equal(pending.length,1);
assert.deepEqual(requested(pending[0]),['510300','2021-01-01','2026-10-30'],'stock full-history entry resets the previous ETF custom dates');
const data={fund:fund('510300'),start:'2021-01-01',end:'2026-10-30',total:101,page:1,page_size:100,items:[{trade_date:'2022-01-04',shares:null,unit_nav:1.2345,acc_nav:0,estimated_flow:null,quality:'raw_observation'}],coverage:{shares:{count:0,start:null,end:null},nav:{count:1,start:'2022-01-04',end:'2022-01-04'}},notes:['未完成']};
pending[0].resolve(response(data)); await settle();
assert.equal(nodes.get('history-rows').children[0].children[1].textContent,'—');
assert.equal(nodes.get('history-rows').children[0].children[2].textContent,'1.2345');
assert.equal(nodes.get('history-rows').children[0].children[3].textContent,'0');
assert.equal(nodes.get('history-rows').children[0].children[4].textContent,'—');
assert.equal(nodes.get('history-rows').children[0].children[5].textContent,'原始观测');
assert.equal(nodes.get('history-next').disabled,false);
nodes.get('history-next').click(); assert.match(pending[1].url,/page=2/);
nodes.get('history-start').value='2023-01-01'; nodes.get('history-end').value='2022-01-01'; nodes.get('history-apply').click();
assert.match(nodes.get('history-rows-state').children[0].textContent,/有效日期范围/);
pending[1].resolve(response({...data,page:2})); await settle(); assert.equal(nodes.get('history-rows').children.length,0);
nodes.get('history-start').value='2021-01-01'; nodes.get('history-end').value='2026-10-30'; nodes.get('history-apply').click();
const validRows=[{...data.items[0],estimated_flow:-12345.67,quality:'estimated'},{...data.items[0],estimated_flow:null,quality:'missing_current_nav'}];
pending[2].resolve(response({...data,items:validRows,coverage:{...data.coverage,flow:{count:1,start:'2022-01-04',end:'2022-01-04'}}})); await settle();
assert.equal(nodes.get('history-rows').children[0].children[4].textContent,'-1.23 万元');
assert.equal(nodes.get('history-rows').children[0].children[5].textContent,'估算');
assert.equal(nodes.get('history-rows').children[1].children[5].textContent,'当日缺净值');
assert.equal(nodes.get('history-coverage').children.length,3);
const unitCalls=calls.length;setMoneyUnit('yi');
assert.equal(calls.length,unitCalls,'unit changes only rerender cached rows');
assert.equal(nodes.get('history-rows').children[0].children[4].textContent,'-0.0001234567 亿元');
assert.equal(nodes.get('history-rows').children[0].children[2].textContent,'1.2345','NAV does not change unit');
setMoneyUnit('auto');
nodes.get('etf-market').value='SZ'; nodes.get('etf-market').listeners.change(); await settle(); assert(calls.some(x=>x.includes('market=SZ')));
nodes.get('catalog-next').click(); await settle(); assert(calls.some(x=>x.includes('page=2')&&x.includes('/history/etfs')));
nodes.get('stock-tab').click(); assert.equal(nodes.get('history-view').hidden,true); assert.equal(nodes.get('stock-view').hidden,false);
events['etf-open-history']({detail:{...fund('510300'),return_view:'stock',range_start:'2026-10-12',range_end:'2026-10-23'}});
assert.deepEqual(requested(pending.at(-1)),['510300','2026-10-12','2026-10-23']);
console.log('PASS: history metadata, catalog, dates, null values, pagination, stale response and safe text');
