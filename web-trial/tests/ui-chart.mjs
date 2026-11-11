import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chartSeries, selectSeries } from '../public/logic.mjs';
const daily = [
  {trade_date:'2021-01-01',flow:10},{trade_date:'2026-10-01',flow:null},
  {trade_date:'2026-10-02',flow:0},{trade_date:'2026-10-03',flow:25}
];
const filtered = selectSeries(daily,'2021-01-01','2026-10-03',true);
assert.equal(filtered.length,3); assert.equal(filtered[0].trade_date,'2026-10-01');
const sparse = chartSeries(filtered,'2021-01-01','2026-10-03');
assert.equal(sparse.aggregated,true);
const sparseValues = sparse.points.filter(p => p.value !== null);
assert.equal(sparseValues.length,1); assert(sparseValues[0].x > .9);
assert.equal(sparseValues[0].value,25); assert.equal(sparseValues[0].count,2);
assert(sparse.points.some(p => p.value === null));
const close = Array.from({length:21},(_,i)=>({trade_date:new Date(Date.UTC(2026,8,28)+i*86400000).toISOString().slice(0,10),flow:i+1}));
const longWindow = chartSeries(close,'2021-11-02','2026-11-02');
assert(longWindow.aggregated); assert(longWindow.points.filter(p=>p.value!==null).length < close.length);
assert.equal(longWindow.points.reduce((sum,p)=>sum+(p.value??0),0),231);
assert.equal(longWindow.points.reduce((sum,p)=>sum+p.count,0),21);
assert(longWindow.points.slice(0,80).every(p=>p.value===null));
const zero = chartSeries([{trade_date:'2026-10-02',flow:0}], '2026-10-01', '2026-10-03');
assert.equal(zero.points[0].value,0);
const start=Date.UTC(2021,0,1), many=Array.from({length:1200},(_,i)=>({trade_date:new Date(start+i*86400000).toISOString().slice(0,10),flow:i%5===0?null:1}));
const full = chartSeries(many,'2021-01-01','2026-10-30');
assert(full.aggregated); assert.equal(full.points.length,90);
assert.equal(full.points.reduce((n,p)=>n+(p.value??0),0),960);
assert(full.points.some(p=>p.value===null));
assert(full.points.filter(p=>p.value===null).every(p=>p.count===0));
const html = await readFile(new URL('../public/index.html', import.meta.url), 'utf8');
assert.deepEqual([...html.matchAll(/data-stock-period="([^"]+)"/g)].map(m => m[1]), ['1d','5d','20d','3m','6m','1y','2y','3y','5y','all']);
assert.deepEqual([...html.matchAll(/data-rank-period="([^"]+)"/g)].map(m => m[1]), ['1d','5d','20d']);
console.log('PASS: selected window, sparse gaps, observed zero, bounded aggregation and period controls');
