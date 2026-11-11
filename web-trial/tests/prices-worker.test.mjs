import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
const base=(await Promise.all(['history','broad','market','prices','index'].map(n=>readFile(new URL('../worker/'+n+'.js',import.meta.url),'utf8')))).join('\n');
let serial=0;
async function setup(fetcher,extra={}){
 const mod=await import('data:text/javascript;base64,'+Buffer.from(base+'\nexport {priceParse,priceWindows,priceEastmoneyValid};\n// '+(++serial)).toString('base64'));
 const values=new Map(),stock={stock_code:'600519',stock_name:'测试股票',stock_listed_date:'2001-08-27',file:null,...extra};
 values.set('market-latest.json',JSON.stringify({version:'mv1',target_start:'2004-12-30',target_end:'2026-11-09'}));
 values.set('market/mv1/meta.json',JSON.stringify({version:'mv1',target_start:'2004-12-30',target_end:'2026-11-09',history_version:'hv1'}));
 values.set('market/mv1/catalog.json',JSON.stringify({items:[stock]}));
 values.set('market/mv1/calendar.json',JSON.stringify({dates:['2021-11-09','2024-12-31','2025-10-24','2025-10-28','2026-11-06','2026-11-09']}));
 values.set('broad-latest.json',JSON.stringify({version:'bv1',target_start:'1990-12-19',target_end:'2026-11-09'}));
 values.set('broad/bv1/meta.json',JSON.stringify({version:'bv1',target_start:'1990-12-19',target_end:'2026-11-09',history_version:'hv1'}));
 values.set('broad/bv1/calendar.json',JSON.stringify({dates:['1990-12-19','2001-08-27','2021-11-09','2024-12-31','2025-10-24','2025-10-28','2026-11-06','2026-11-09']}));
 const env={PRICE_FETCH:fetcher,BUCKET:{get:async k=>values.has(k)?{text:async()=>values.get(k)}:null,put:async(k,v)=>values.set(k,String(v))}};
 const call=async(path='/api/v1/prices/stock/600519?start=2021-11-09&end=2026-11-09',method='GET')=>{const r=await mod.default.fetch(new Request('https://private.test'+path,{method}),env);return {status:r.status,body:await r.json()};};
 return {mod,env,values,call,stock};
}
const candle=(d,p=100,v=10)=>[d,String(p),String(p),String(p+1),String(p-1),String(v)];
const body=(symbol,rows)=>({code:0,data:{[symbol]:{day:rows,qt:{[symbol]:['','测试',symbol.slice(2),'999999']}}}});
const source=(url,rows)=>{const [symbol,,start,end]=new URL(url).searchParams.get('param').split(',');return new Response(JSON.stringify(body(symbol,rows.filter(r=>r[0]>=start&&r[0]<=end))),{headers:{'content-type':'application/json'}});};
test('five-year source windows avoid provider truncation; exact date/unit contract and cache reuse',async()=>{
 const calls=[],s=await setup(async(url,opts)=>{calls.push({url,opts});return source(url,[candle('2024-12-31',80),candle('2026-11-06',100),candle('2026-11-09',101)]);});
 const result=await s.call(),d=result.body.data;assert.equal(result.status,200);assert.equal(calls.length,2);assert.deepEqual(d.series.map(r=>r.close),[80,100,101]);assert.equal(d.price_unit,'元/股');assert.equal(d.adjustment,'none');assert.equal(d.status,'partial');assert.equal(d.start,'2021-11-09');assert.equal(d.end,'2026-11-09');assert.equal(d.target_end,'2026-11-09');assert.equal(d.market_version,'mv1');
 assert(calls.every(c=>c.url.startsWith('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?')&&c.opts.redirect==='error'));
 assert(calls.every(c=>new URL(c.url).searchParams.get('param').endsWith(',2000,')));
 await s.call();await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09');assert.equal(calls.length,2,'range and amount unit changes must reuse source windows');
});
test('unknown stocks, future dates, invalid ranges and arbitrary source parameters never reach the network',async()=>{
 let n=0;const s=await setup(async()=>{n++;throw Error('must not fetch');});
 for(const path of ['/api/v1/prices/stock/000001','/api/v1/prices/stock/../../x','/api/v1/prices/stock/600519?end=2026-11-10','/api/v1/prices/stock/600519?start=2026-02-30','/api/v1/prices/stock/600519?start=2026-11-09&end=2026-11-06','/api/v1/prices/stock/600519?url=https://evil.test','/api/v1/prices/stock/600519?start=2026-01-01&start=2026-02-01'])assert.notEqual((await s.call(path)).status,200,path);
 assert.equal((await s.call('/api/v1/prices/stock/600519','POST')).status,405);assert.equal(n,0);
});
test('identity or adjusted data is rejected, and a failed window gets bounded retry backoff',async()=>{
 let n=0;const s=await setup(async()=>{n++;return new Response(JSON.stringify(body('sh600000',[candle('2026-11-09')])));});
 const d=(await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09')).body.data;assert.equal(d.status,'unavailable');assert.deepEqual(d.series,[]);assert.equal(d.failed_windows,1);
 await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09');assert.equal(n,1);
 const good=body('sh600519',[candle('2026-11-09')]);
 for(const mutate of [x=>x.data.sh600519.qfqday=[],x=>x.data.sh600519.qt.sh600519[2]='600000',x=>x.data.sh600519.day[0][2]=true,x=>x.data.sh600519.day[0][3]='1',x=>x.data.sh600519.day.push(candle('2026-11-09')),x=>x.data.sh600519.day=Array.from({length:1501},()=>candle('2026-11-09'))]){const x=structuredClone(good);mutate(x);assert.throws(()=>s.mod.priceParse(x,'600519',{start:'2025-01-01',end:'2029-12-31'},'2026-11-09'));}
});
test('failed refresh retains verified old prices at their real dates, never today or zero',async()=>{
 const s=await setup(async()=>{throw Error('offline');});const key='prices/v1/600519/2025.json';
 const old={schema:1,stock_code:'600519',window_start:'2025-01-01',window_end:'2029-12-31',checked_through:'2026-11-06',fetched_at:'2026-11-06T10:00:00Z',source:'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',adjustment:'none',price_unit:'元/股',series:[['2026-11-06',123.45]]};
 s.values.set(key,JSON.stringify(old));const d=(await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09')).body.data;
 assert.equal(d.status,'partial');assert.equal(d.available_end,'2026-11-06');assert.deepEqual(d.series,[{trade_date:'2026-11-06',close:123.45}]);assert.deepEqual(JSON.parse(s.values.get(key)),old);
});
test('verified IPO, dates absent from the published calendar and zero-volume rows are filtered without interpolation',async()=>{
 const s=await setup(async url=>source(url,[candle('2025-10-24'),candle('2025-10-28'),candle('2026-11-06',100,0),candle('2026-11-07'),candle('2026-11-09',102)]),{stock_listed_date:'2025-10-28'});
 const d=(await s.call()).body.data;assert.equal(d.start,'2021-11-09');assert.deepEqual(d.series.map(r=>r.trade_date),['2025-10-28','2026-11-09']);assert.equal(d.available_start,'2025-10-28');assert.equal(d.source_calendar_excluded_count,1);assert.equal(d.coverage.source_calendar_excluded_count,1);assert(d.notes.some(note=>note.includes('待核验未绘图')));
});
test('concurrent same-window requests share one source request; empty real windows stay unavailable',async()=>{
 let count=0;const s=await setup(async url=>{count++;return source(url,[]);});const path='/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09';
 const results=await Promise.all([s.call(path),s.call(path)]);assert.equal(count,1);assert(results.every(x=>x.body.data.status==='unavailable'&&x.body.data.series.length===0));
});
test('access and rate-limit responses are not bypassed or repeatedly retried',async()=>{
 let count=0;const s=await setup(async()=>{count++;return new Response('blocked',{status:403});});await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09');
 await s.call('/api/v1/prices/stock/600519?start=2021-11-09&end=2024-12-31');assert.equal(count,1);
});
test('source history regressions retain the successful cache and back off',async()=>{
 let n=0;const s=await setup(async url=>{n++;return source(url,[]);});
 const key='prices/v1/600519/2025.json',old={schema:1,stock_code:'600519',window_start:'2025-01-01',window_end:'2029-12-31',checked_through:'2026-11-06',fetched_at:new Date(Date.now()-3*3600000).toISOString(),source:'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',adjustment:'none',price_unit:'元/股',series:[['2026-11-06',123.45]]};
 s.values.set(key,JSON.stringify(old));const path='/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09';
 const d=(await s.call(path)).body.data;assert.equal(d.status,'partial');assert.deepEqual(d.series,[{trade_date:'2026-11-06',close:123.45}]);assert.deepEqual(JSON.parse(s.values.get(key)),old);
 assert.equal(JSON.parse(s.values.get('prices/attempts/v1/600519/2025.json')).reason,'source_history_regression');await s.call(path);assert.equal(n,1);
});
test('future cache timestamps and malformed cache JSON cannot suppress fresh observations',async()=>{
 let n=0;const s=await setup(async url=>{n++;return source(url,[candle('2026-11-06',100),candle('2026-11-09',101)]);});
 const key='prices/v1/600519/2025.json';s.values.set(key,'{broken');s.values.set('prices/attempts/v1/600519/2025.json','{broken');
 const path='/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09';assert.equal((await s.call(path)).body.data.status,'ready');assert.equal(n,1);
 const record=JSON.parse(s.values.get(key));record.fetched_at=new Date(Date.now()+3600000).toISOString();s.values.set(key,JSON.stringify(record));
 s.values.set('prices/attempts/v1/600519/2025.json',JSON.stringify({attempted_at:record.fetched_at,target_end:'2026-11-09',reason:'source_unavailable'}));
 assert.equal((await s.call(path)).body.data.status,'ready');assert.equal(n,2);
});
test('pending current windows retry after two hours but retain fresh historical windows',async()=>{
 let n=0;const s=await setup(async url=>{n++;return source(url,[candle('2026-11-06',100),candle('2026-11-09',101)]);});
 const key='prices/v1/600519/2025.json',record={schema:1,stock_code:'600519',window_start:'2025-01-01',window_end:'2029-12-31',checked_through:'2026-11-09',fetched_at:new Date().toISOString(),source:'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',adjustment:'none',price_unit:'元/股',series:[['2026-11-06',100]]};
 s.values.set(key,JSON.stringify(record));const path='/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09';assert.equal((await s.call(path)).body.data.status,'partial');assert.equal(n,0);
 record.fetched_at=new Date(Date.now()-3*3600000).toISOString();s.values.set(key,JSON.stringify(record));assert.equal((await s.call(path)).body.data.status,'ready');assert.equal(n,1);
});
test('prices before ETF inception use the linked full calendar and explicit listing boundary',async()=>{
 const s=await setup(async url=>source(url,[candle('2001-08-27',35.55),candle('2003-12-31',28.9)]));
 const cal=JSON.parse(s.values.get('broad/bv1/calendar.json'));cal.dates.splice(2,0,'2003-12-31');s.values.set('broad/bv1/calendar.json',JSON.stringify(cal));
 const d=(await s.call('/api/v1/prices/stock/600519?start=1990-01-01&end=2004-12-29')).body.data;
 assert.equal(d.start,'1990-01-01');assert.equal(d.stock_listed_date,'2001-08-27');assert.equal(d.listing_status,'verified');assert.equal(d.calendar_start,'1990-12-19');assert.equal(d.calendar_end,'2026-11-09');assert.equal(d.calendar_version,'bv1');assert.equal(d.history_version,'hv1');
 assert.deepEqual(d.trading_dates,['2001-08-27','2003-12-31']);assert.deepEqual(d.series,[{trade_date:'2001-08-27',close:35.55},{trade_date:'2003-12-31',close:28.9}]);assert.equal(d.coverage.calendar_days,2);assert.equal(d.coverage.all_calendar_dates_priced,true);assert.equal(d.available_start,'2001-08-27');assert.equal(d.status,'ready');
});
test('stale or mismatched full calendars fail clearly before any external price request',async()=>{
 for(const mutate of [s=>s.values.delete('broad-latest.json'),s=>s.values.set('broad/bv1/meta.json',JSON.stringify({version:'bv1',target_start:'1990-12-19',target_end:'2026-11-09',history_version:'different'})),s=>s.values.set('broad/bv1/calendar.json',JSON.stringify({dates:['1990-12-19','2026-11-06']}))]){
  let n=0;const s=await setup(async()=>{n++;throw Error('must not fetch');});mutate(s);assert.equal((await s.call()).status,503);assert.equal(n,0);
 }
});
test('unknown IPO is not inferred and absent dates remain explicit gaps',async()=>{
 const s=await setup(async url=>source(url,[candle('2026-11-09',101)]),{stock_listed_date:null});
 const d=(await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09')).body.data;
 assert.equal(d.stock_listed_date,null);assert.equal(d.listing_status,'unknown');assert.equal(d.coverage.listing_boundary_verified,false);assert.equal(d.coverage.unpriced_calendar_days,1);assert.equal(d.status,'partial');assert.deepEqual(d.series,[{trade_date:'2026-11-09',close:101}]);
});
function eastmoneyRecord(rows=[['2026-11-06',100],['2026-11-09',101]]){
 return {schema:1,provider:'eastmoney',stock_code:'600519',stock_name:'测试股票',market:'SH',source:'https://push2his.eastmoney.com/api/qt/stock/kline/get',adjustment:'none',price_unit:'元/股',listed_date:'2001-08-27',checked_through:'2026-11-09',fetched_at:new Date().toISOString(),fqt:0,klt:101,provider_total:rows.length,series:rows,columns:['date','open','close','high','low','provider_volume','provider_amount'],volume_unit:'provider_raw_unverified',amount_unit:'provider_raw_unverified',candles:rows.map(([day,close])=>[day,close,close,close+1,close-1,100,1000])};
}
function tencentRecord(){return {schema:1,stock_code:'600519',window_start:'2025-01-01',window_end:'2029-12-31',checked_through:'2026-11-09',fetched_at:new Date().toISOString(),source:'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',adjustment:'none',price_unit:'元/股',series:[['2026-11-06',800],['2026-11-09',900]]};}
test('a complete independent Eastmoney cache is read without network or mixing Tencent prices',async()=>{
 let calls=0;const s=await setup(async()=>{calls++;throw Error('must never fetch for an existing independent provider');});
 s.values.set('prices/eastmoney/v1/600519.json',JSON.stringify(eastmoneyRecord()));s.values.set('prices/v1/600519/2025.json',JSON.stringify(tencentRecord()));const before=new Map(s.values);
 const d=(await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09')).body.data;
 assert.equal(d.provider,'eastmoney');assert.equal(d.source_label,'东方财富不复权日K');assert.equal(d.source,'https://push2his.eastmoney.com/api/qt/stock/kline/get');assert.deepEqual(d.series,[{trade_date:'2026-11-06',close:100},{trade_date:'2026-11-09',close:101}]);assert.equal(d.source_record_count,2);assert.equal(d.source_checked_through,'2026-11-09');assert.equal(d.status,'ready');assert.equal(calls,0);assert.deepEqual(s.values,before,'reading a complete provider never mutates either cache');
});
test('an older valid Eastmoney cache remains one partial curve instead of receiving a Tencent tail',async()=>{
 let calls=0;const s=await setup(async()=>{calls++;throw Error('must not fetch');}),record=eastmoneyRecord([['2026-11-06',100]]);record.checked_through='2026-11-06';record.fetched_at='2026-11-06T10:00:00Z';
 s.values.set('prices/eastmoney/v1/600519.json',JSON.stringify(record));s.values.set('prices/v1/600519/2025.json',JSON.stringify(tencentRecord()));
 const d=(await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09')).body.data;
 assert.equal(d.provider,'eastmoney');assert.equal(d.status,'partial');assert.equal(d.available_end,'2026-11-06');assert.equal(d.source_checked_through,'2026-11-06');assert.deepEqual(d.series,[{trade_date:'2026-11-06',close:100}]);assert.equal(calls,0);assert(d.notes.some(n=>n.includes('不拼接其他来源补尾')));
});
test('Eastmoney cache identity, listing, full counts, raw candles and exact close derivation are mandatory',async()=>{
 const mutations=[d=>d.schema=2,d=>d.provider='tencent',d=>d.stock_code='600000',d=>d.market='SZ',d=>d.listed_date='2000-01-01',d=>d.adjustment='qfq',d=>d.fqt=1,d=>d.klt=102,d=>d.source='https://untrusted.test/quote',d=>d.provider_total++,d=>d.series[0][1]=999,d=>d.candles[0][2]=true,d=>d.candles[0][3]=1,d=>d.candles[0][5]=-1,d=>d.candles[0][6]=-1,d=>d.candles[1][0]='2026-11-06',d=>d.checked_through='2026-11-10',d=>d.fetched_at='2099-01-01T00:00:00Z'];
 let calls=0;const s=await setup(async()=>{calls++;throw Error('fallback should reuse its own cache');});s.values.set('prices/v1/600519/2025.json',JSON.stringify(tencentRecord()));
 for(const change of mutations){const d=eastmoneyRecord();change(d);assert.equal(s.mod.priceEastmoneyValid(d,'600519','2001-08-27','2026-11-09'),false);s.values.set('prices/eastmoney/v1/600519.json',JSON.stringify(d));const result=(await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09')).body.data;assert.equal(result.provider,'tencent');assert.deepEqual(result.series.map(x=>x.close),[800,900]);}
 assert.equal(calls,0);
});
test('oversized Eastmoney files are ignored and a missing calendar never triggers external price work',async()=>{
 let calls=0;const s=await setup(async()=>{calls++;throw Error('must not fetch');});s.values.set('prices/v1/600519/2025.json',JSON.stringify(tencentRecord()));s.values.set('prices/eastmoney/v1/600519.json',JSON.stringify({...eastmoneyRecord(),pad:'x'.repeat(2*1024*1024)}));
 assert.equal((await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09')).body.data.provider,'tencent');
 s.values.set('prices/eastmoney/v1/600519.json',JSON.stringify(eastmoneyRecord()));s.values.delete('broad/bv1/calendar.json');assert.equal((await s.call()).status,503);assert.equal(calls,0);
});
test('Eastmoney zero-volume raw candles are preserved only as raw observations, never filled prices',async()=>{
 let calls=0;const s=await setup(async()=>{calls++;throw Error('must not fetch');}),d=eastmoneyRecord();d.candles[0][5]=0;d.series.shift();s.values.set('prices/eastmoney/v1/600519.json',JSON.stringify(d));
 const result=(await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-09')).body.data;assert.equal(result.provider,'eastmoney');assert.equal(result.status,'partial');assert.deepEqual(result.series,[{trade_date:'2026-11-09',close:101}]);assert.equal(result.coverage.unpriced_calendar_days,1);assert.equal(calls,0);
});
test('calendar disagreements remain explicit partial coverage even when every calendar day is priced; only selected effective dates count',async()=>{
 let calls=0;const s=await setup(async()=>{calls++;throw Error('must not fetch');});
 const record=eastmoneyRecord([['2026-11-05',99],['2026-11-06',100],['2026-11-07',101],['2026-11-08',102],['2026-11-09',103]]),key='prices/eastmoney/v1/600519.json';s.values.set(key,JSON.stringify(record));const before=new Map(s.values);
 const d=(await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-07')).body.data;
 assert.equal(d.status,'partial','covered formal-calendar dates do not imply the provider and calendar agree');assert.equal(d.coverage.all_calendar_dates_priced,true);assert.equal(d.coverage.unpriced_calendar_days,0);assert.equal(d.source_calendar_excluded_count,1);assert.equal(d.coverage.source_calendar_excluded_count,1);assert.deepEqual(d.series,[{trade_date:'2026-11-06',close:100}]);assert(d.notes.some(note=>note.includes('1 条源行情日期')&&note.includes('待核验未绘图')&&note.includes('不据此推断休市')));
 const narrow=(await s.call('/api/v1/prices/stock/600519?start=2026-11-06&end=2026-11-06')).body.data;
 assert.equal(narrow.source_calendar_excluded_count,0,'source dates before or after the selected effective range never count');assert.equal(narrow.coverage.source_calendar_excluded_count,0);assert.equal(narrow.status,'ready');assert(!narrow.notes.some(note=>note.includes('待核验未绘图')));
 const unresolved=(await s.call('/api/v1/prices/stock/600519?start=2026-11-07&end=2026-11-08')).body.data;
 assert.equal(unresolved.status,'partial','only unresolved source dates remain partial, not silently ready or absent');assert.equal(unresolved.source_calendar_excluded_count,2);assert.deepEqual(unresolved.series,[]);assert.deepEqual(unresolved.trading_dates,[]);assert.deepEqual(s.values,before,'calendar filtering never rewrites original source records');assert.equal(calls,0);
});
