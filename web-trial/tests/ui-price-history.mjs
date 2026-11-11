import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setMoneyUnit} from '../public/logic.mjs';
class Node{
 constructor(tag='div'){Object.assign(this,{tag,children:[],listeners:{},value:'',hidden:false,disabled:false,textContent:'',attributes:{}});}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;this.textContent='';}addEventListener(k,fn){this.listeners[k]=fn;}setAttribute(k,v){this.attributes[k]=v;}click(){if(!this.disabled)this.listeners.click?.({});}
 focus(options){this.focusOptions=options;this.focusCount=(this.focusCount||0)+1;}scrollIntoView(options){this.scrollOptions=options;this.scrollCount=(this.scrollCount||0)+1;}
}
const nodes=new Map(),calls=[],pending=[],renderings=[];
globalThis.document={getElementById(id){if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},createElement:tag=>new Node(tag)};
globalThis.__priceHistoryChart=(container,rows,options)=>{renderings.push({rows,options});container.append(new Node('svg'));return ()=>container.replaceChildren();};
let coverageAvailable=true;
globalThis.fetch=async url=>{
 const u=new URL(url,'http://local.test');calls.push(u);
 if(u.pathname==='/local/price-coverage')return {ok:coverageAvailable,json:async()=>({schema:1,kind:'listed-stock-price-coverage',target_end:'2026-11-10',universe_complete:false,summary:{stocks:5225,stocks_with_data:400,stocks_from_listing:350,rows:1000000}})};
 if(u.pathname.startsWith('/api/v1/prices/stock/'))return new Promise(resolve=>pending.push({u,resolve}));
 throw Error('Unexpected request: '+u.pathname);
};
let source=await readFile(new URL('../public/price-history.mjs',import.meta.url),'utf8');
source=source.replace("import {renderTimeChart, exactAmount} from './charts.mjs';",'import {exactAmount} from '+JSON.stringify(new URL('../public/charts.mjs',import.meta.url).href)+'; const renderTimeChart=globalThis.__priceHistoryChart;').replace("from './logic.mjs'",'from '+JSON.stringify(new URL('../public/logic.mjs',import.meta.url).href));
const ui=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64')),settle=()=>new Promise(r=>setTimeout(r,0));
const response=data=>({ok:true,json:async()=>({code:0,data})});
const stock={stock_code:'600519',stock_name:'贵州茅台',stock_listed_date:'2001-08-27',trade_date:'2026-11-10'};
function payload(request,dates){return {stock_code:request.u.pathname.split('/').at(-1),stock_listed_date:stock.stock_listed_date,start:request.u.searchParams.get('start'),end:request.u.searchParams.get('end'),price_unit:'元/股',adjustment:'none',status:'partial',trading_dates:dates,series:[{trade_date:dates[0],close:31.39},{trade_date:dates.at(-1),close:1600.12}]};}
await settle();assert.equal(nodes.get('stock-price-coverage').hidden,false);assert.match(nodes.get('stock-price-coverage').textContent,/400 \/ 5225/);assert.match(nodes.get('stock-price-coverage').textContent,/北交所目录尚未核验/);
assert.equal(calls.length,1,'price history waits for explicit stock action');
ui.setPriceHistoryStock(stock);nodes.get('price-history-open').click();
assert.equal(pending[0].u.searchParams.get('start'),'2001-08-27','lifetime price query starts at IPO rather than ETF history start');
const dates=[];for(let date=new Date('2001-08-27T00:00:00Z');dates.length<60;date.setUTCDate(date.getUTCDate()+1)){if(![0,6].includes(date.getUTCDay()))dates.push(date.toISOString().slice(0,10));}dates.push('2026-11-10');
pending[0].resolve(response({...payload(pending[0],dates),source_calendar_excluded_count:40}));await settle();
assert.equal(renderings.at(-1).options.start,'2001-08-27');assert.equal(renderings.at(-1).options.unit,'元/股');assert.equal(renderings.at(-1).options.signed,false);assert.equal(renderings.at(-1).options.preserveHistoricalSessions,true);
assert.equal(nodes.get('price-history-body').children.length,50);assert.equal(nodes.get('price-history-body').children[0].children[0].textContent,'2026-11-10');assert.equal(nodes.get('price-history-body').children[1].children[1].textContent,'—');
assert.match(nodes.get('price-history-status').children[0].textContent,/已核验上市日 2001-08-27/);assert.match(nodes.get('price-history-status').children[0].textContent,/59 个开市日没有成交收盘记录/);
const before=calls.length;setMoneyUnit('yi');nodes.get('price-history-next').click();assert.equal(calls.length,before);assert.equal(nodes.get('price-history-body').children.length,11);assert.match(nodes.get('price-history-body').children.at(-1).children[1].textContent,/31.39 元\/股/);setMoneyUnit('auto');
assert.equal(nodes.get('price-history-rows-heading').focusCount,1);assert.equal(nodes.get('price-history-rows-heading').scrollCount,1);assert.deepEqual(nodes.get('price-history-rows-heading').focusOptions,{preventScroll:true});
assert.match(nodes.get('price-history-status').children[0].textContent,/40 条源日期与当前交易日历不一致/);
ui.setPriceHistoryStock({...stock,movement:{period:'1d'}});assert.equal(nodes.get('price-history-content').hidden,false,'fund period changes do not collapse independent price history');
nodes.get('price-history-start').value='2000-01-01';nodes.get('price-history-apply').click();assert.equal(calls.length,before);assert.equal(nodes.get('price-history-chart').children.length,0);assert.match(nodes.get('price-history-status').children[0].textContent,/有效日期范围/);
nodes.get('price-history-start').value='2001-09-01';nodes.get('price-history-end').value='2001-10-01';nodes.get('price-history-apply').click();
ui.beginPriceHistoryStock('000001');ui.setPriceHistoryStock({stock_code:'000001',stock_name:'平安银行',stock_listed_date:'1991-04-03',trade_date:'2026-11-10'});nodes.get('price-history-open').click();
assert.equal(pending[2].u.searchParams.get('start'),'1991-04-03');
const oldRenderCount=renderings.length;pending[1].resolve(response(payload(pending[1],['2001-09-03','2001-10-01'])));await settle();assert.equal(renderings.length,oldRenderCount,'old stock response cannot replace current stock');
pending[2].resolve(response({...payload(pending[2],['1991-04-03','2026-11-10']),stock_listed_date:'1991-04-03',trading_dates:undefined}));await settle();assert.match(nodes.get('price-history-status').children[0].textContent,/交易日历未通过核验/);assert.equal(nodes.get('price-history-chart').children.length,0,'missing full calendar cannot silently fall back to clipped fund calendar');
ui.beginPriceHistoryStock('600000');ui.setPriceHistoryStock({stock_code:'600000',trade_date:'2026-11-10'});nodes.get('price-history-open').click();assert.equal(pending[3].u.searchParams.get('start'),'1990-01-01');
pending[3].resolve(response({...payload(pending[3],['1990-12-19','2026-11-10']),stock_listed_date:null}));await settle();assert.match(nodes.get('price-history-status').children[0].textContent,/上市日期尚未核验/);
coverageAvailable=false;ui.beginPriceHistoryStock('000002');ui.refreshPriceHistory();await settle();assert.equal(nodes.get('stock-price-coverage').hidden,true,'cloud or unavailable local coverage fails quietly');
assert(calls.every(u=>u.pathname==='/local/price-coverage'||u.pathname.startsWith('/api/v1/prices/')),'price-only mode never fetches or changes ETF fund data');
console.log('PASS: IPO range, early history, full-calendar requirement, real gaps, cache-only pagination, units, request races and optional coverage');
