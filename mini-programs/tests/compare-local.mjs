// Overlap check against the pre-existing, read-only local market/ETF service.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const offline=process.env.ETF_MINI_DIST?require(path.resolve(process.env.ETF_MINI_DIST,'wechat/lib/offline.js')):require('../dist/wechat/lib/offline.js');
const results={target:offline.meta.end,broad_groups:0,broad_date_windows:0,fund_charts:0,stock_periods:0,report_queries:0,quote_source_requests:0};
const read=process.argv.includes('--pinned')?await (await import('./reference-api.mjs')).referenceApi(offline.meta):url=>fetch(url,{redirect:'error'});
async function actual(route,params){
 const u=new URL('http://127.0.0.1:4173/api/v1/'+route);
 for(const [k,v] of Object.entries(params))u.searchParams.set(k,v);
 const r=await read(u),body=await r.json();assert.equal(body.code,0);return body.data;
}
for(const code of ['all',...offline.meta.broad.indexes.map(i=>i.code)]){
 const p={start:offline.meta.start,end:offline.meta.end,off_page_size:10},route='broad/index/'+code;
 const a=offline.query(route,p),b=await actual(route,p);
 assert.deepEqual(a.series,b.series);assert.deepEqual(a.trading_dates,b.trading_dates);
 for(const k of ['fund_code','period_observed_flow','period_complete_flow','period_valid_days','period_expected_days','end_date_flow']){
   assert.deepEqual(a.members.map(f=>f[k]),b.members.map(f=>f[k]),code+' '+k);
 }
 assert.equal(a.off_exchange.total,b.off_exchange.total);assert.deepEqual(a.off_exchange.reports,b.off_exchange.reports);
 results.broad_groups++;
}
// Check interval totals on shorter, nested windows as well as the full archive.
// Dates are selected from the archive calendar, so this works across holidays.
const route='broad/index/000300';
const whole=offline.query(route,{start:offline.meta.start,end:offline.meta.end});
const dates=whole.trading_dates;
assert.ok(dates.length,'snapshot calendar has no observed trading days');
for(const width of new Set([1,Math.min(20,dates.length)])){
 const range=dates.slice(-width),p={start:range[0],end:range.at(-1)};
 const selected=offline.query(route,p),reference=await actual(route,p);
 assert.deepEqual(selected.trading_dates,range);
 assert.deepEqual(selected.trading_dates,reference.trading_dates);
 assert.deepEqual(selected.series,reference.series);
 assert.deepEqual(selected.members.map(row=>row.fund_code),reference.members.map(row=>row.fund_code));
 for(const [i,row] of selected.members.entries()){
   for(const [field,value] of Object.entries(row))assert.deepEqual(value,reference.members[i][field],row.fund_code+' '+field);
 }
 results.broad_date_windows++;
}
for(const code of ['000300','all']){
 const p={start:offline.meta.start,end:offline.meta.end,page:2,page_size:10},route='broad/off/'+code;
 const a=offline.query(route,p),b=await actual(route,p);
 assert.equal(a.total,b.total);assert.deepEqual(a.reports,b.reports);results.report_queries++;
}
for(const code of ['510300','159919','510500']){
 const p={start:offline.meta.start,end:offline.meta.end},route='history/etf/'+code+'/chart';
 const a=offline.query(route,p),b=await actual(route,p);
 assert.equal(a.total,b.total);
 for(const key of ['trade_date','shares','unit_nav','estimated_flow','quality'])assert.deepEqual(a.series.map(r=>r[key]),b.series.map(r=>r[key]));
 results.fund_charts++;
}
for(const code of offline.meta.stocks.map(s=>s.stock_code))for(const period of ['1d','20d','5y','all']){
 const p={period},route='market/stock/'+code,a=offline.query(route,p),b=await actual(route,p);
 assert.deepEqual(a.movement,b.movement);assert.deepEqual(a.etf_contributions,b.etf_contributions);
 for(const k of ['trade_date','flow','valid_etfs','expected_etfs'])assert.deepEqual(a.series.map(r=>r[k]),b.series.map(r=>r[k]));
 results.stock_periods++;
}
console.log(JSON.stringify(results));
const report=process.argv.slice(2).find(arg=>arg!=='--pinned');
if(report)await fs.writeFile(report,JSON.stringify(results,null,2));
