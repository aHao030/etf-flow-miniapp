// Independent unadjusted closing-price cache. No price is inferred from fund flows.
const PRICE_SOURCE='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get';
const PRICE_EASTMONEY_SOURCE='https://push2his.eastmoney.com/api/qt/stock/kline/get';
const priceFlights=new Map();
let priceRunning=0,priceLastStart=0,priceBlockedUntil=0;
const priceWaiters=[];
const priceSleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const priceInstantOK=x=>typeof x==='string'&&instantOK(x.replace(/(\.\d{3})\d{1,3}(?=Z$|[+-]\d{2}:\d{2}$)/,'$1'));
function priceSymbol(code){return /^6/.test(code)?'sh'+code:/^[03]/.test(code)?'sz'+code:/^(?:4|8|92)/.test(code)?'bj'+code:null;}
function priceWindows(start,end){
  const out=[];for(let y=1990+5*Math.floor((Number(start.slice(0,4))-1990)/5);y<=Number(end.slice(0,4));y+=5)out.push({year:y,start:y+'-01-01',end:(y+4)+'-12-31'});
  return out;
}
function priceRecordValid(d,code,window){
  return object(d)&&d.schema===1&&d.stock_code===code&&d.window_start===window.start&&d.window_end===window.end&&d.adjustment==='none'&&d.price_unit==='元/股'&&d.source===PRICE_SOURCE&&priceInstantOK(d.fetched_at)&&dateOK(d.checked_through)&&d.checked_through>=window.start&&d.checked_through<=window.end&&Array.isArray(d.series)&&d.series.length<=1500&&d.series.every((r,i)=>Array.isArray(r)&&r.length===2&&dateOK(r[0])&&r[0]>=window.start&&r[0]<=d.checked_through&&Number.isFinite(r[1])&&r[1]>0&&r[1]<1e8&&(!i||d.series[i-1][0]<r[0]));
}
function priceEastmoneyValid(d,code,listedDate,target){
  const symbol=priceSymbol(code),columns=['date','open','close','high','low','provider_volume','provider_amount'];
  if(!object(d)||d.schema!==1||d.provider!=='eastmoney'||d.stock_code!==code||typeof d.stock_name!=='string'||!d.stock_name.trim()||d.stock_name.length>500||d.market!==symbol?.slice(0,2).toUpperCase()||d.source!==PRICE_EASTMONEY_SOURCE||d.adjustment!=='none'||d.price_unit!=='元/股'||d.fqt!==0||d.klt!==101||!listedDate||d.listed_date!==listedDate||!dateOK(d.checked_through)||d.checked_through<listedDate||d.checked_through>target||!priceInstantOK(d.fetched_at)||Date.parse(d.fetched_at)>Date.now()+60000||!Number.isSafeInteger(d.provider_total)||d.provider_total<0||d.provider_total>12000||!Array.isArray(d.candles)||d.candles.length!==d.provider_total||!Array.isArray(d.series)||d.series.length>d.candles.length||JSON.stringify(d.columns)!==JSON.stringify(columns)||d.volume_unit!=='provider_raw_unverified'||d.amount_unit!=='provider_raw_unverified')return false;
  let previous='',priced=0;
  for(const row of d.candles){
    if(!Array.isArray(row)||row.length!==7||!dateOK(row[0])||row[0]<listedDate||row[0]>d.checked_through||row[0]<=previous||!row.slice(1).every(v=>typeof v==='number'&&Number.isFinite(v)))return false;
    previous=row[0];const [open,close,high,low,volume,amount]=row.slice(1);
    if(Math.min(open,close,high,low)<=0||Math.max(open,close,high,low)>=1e8||low>Math.min(open,close)||high<Math.max(open,close)||low>high||volume<0||amount<0)return false;
    if(volume>0){const price=d.series[priced++];if(!Array.isArray(price)||price.length!==2||price[0]!==row[0]||price[1]!==close)return false;}
  }
  return priced===d.series.length;
}
async function priceReadEastmoney(env,code,listedDate,target){
  // Read this independent provider only from its prevalidated cache; never fetch a second source in a request.
  try{
    const object=await env.BUCKET.get('prices/eastmoney/v1/'+code+'.json');if(!object||object.size>2*1024*1024)return null;
    const body=await object.text();if(body.length>2*1024*1024||new TextEncoder().encode(body).byteLength>2*1024*1024)return null;
    const data=JSON.parse(body);return priceEastmoneyValid(data,code,listedDate,target)?data:null;
  }catch{return null;}
}
async function priceJSON(response){
  const length=Number(response.headers.get('content-length'));if(length>2*1024*1024)throw Error('response_too_large');
  const reader=response.body?.getReader();if(!reader)throw Error('invalid_response');let size=0;const chunks=[];
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>2*1024*1024){await reader.cancel();throw Error('response_too_large');}chunks.push(value);}
  const bytes=new Uint8Array(size);let at=0;for(const c of chunks){bytes.set(c,at);at+=c.byteLength;}
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
}
function priceParse(body,code,window,through){
  const symbol=priceSymbol(code),d=body?.data?.[symbol];
  if(body?.code!==0||!object(body.data)||Object.keys(body.data).length!==1||!object(d)||'qfqday' in d||'hfqday' in d||!Array.isArray(d.qt?.[symbol])||String(d.qt[symbol][2])!==code||!Array.isArray(d.day)||d.day.length>1500)throw Error('identity_or_shape');
  const rows=[];let previous='';
  for(const row of d.day){
    if(!Array.isArray(row)||row.length<6||!dateOK(row[0])||row[0]<window.start||row[0]>through||row[0]<=previous)throw Error('date_or_order');
    previous=row[0];
    if(!row.slice(1,6).every(v=>(typeof v==='number'||typeof v==='string'&&v.trim()!=='')&&Number.isFinite(Number(v))))throw Error('invalid_price');
    const [open,close,high,low,volume]=row.slice(1,6).map(Number);
    if(Math.min(open,close,high,low)<=0||Math.max(open,close,high,low)>=1e8||volume<0||low>Math.min(open,close)||high<Math.max(open,close)||low>high)throw Error('invalid_price');
    if(volume>0)rows.push([row[0],close]); // Zero-volume/suspension rows never create synthetic observations.
  }
  return rows;
}
async function priceThrottled(task){
  if(priceRunning>=2)await new Promise(resolve=>priceWaiters.push(resolve));else priceRunning++;
  try{const now=Date.now(),delay=Math.max(0,priceLastStart+400-now);priceLastStart=now+delay;if(delay)await priceSleep(delay);return await task();}
  finally{if(priceWaiters.length)priceWaiters.shift()();else priceRunning--;}
}
async function priceCachedWindow(env,code,window,target,listedDate){
  const key='prices/v1/'+code+'/'+window.year+'.json',attemptKey='prices/attempts/v1/'+code+'/'+window.year+'.json';
  const through=target<window.end?target:window.end,now=Date.now();
  let raw=null;try{raw=await getJSON(env.BUCKET,key);}catch{}
  const cached=priceRecordValid(raw,code,window)?raw:null;
  const at=cached?Date.parse(cached.fetched_at):0;
  // Finished historical windows are stable; live windows refresh once per target day.
  const pendingCurrent=through===target&&cached?.series.at(-1)?.[0]!==through;
  if(cached&&cached.checked_through>=through&&now-at>=0&&now-at<(pendingCurrent?2*3600000:30*86400000))return {record:cached,error:null,cached:true};
  let attempt=null;try{attempt=await getJSON(env.BUCKET,attemptKey);}catch{}
  const attemptAge=attempt?now-Date.parse(attempt.attempted_at):Infinity;
  if(attempt&&priceInstantOK(attempt.attempted_at)&&attemptAge>=0&&attemptAge<2*3600000&&attempt.target_end===through)return {record:cached,error:'source_retry_later',cached:true};
  const flightKey=key+':'+through;if(priceFlights.has(flightKey))return priceFlights.get(flightKey);
  const pending=(async()=>{
    try{
      if(Date.now()<priceBlockedUntil)throw Error('source_retry_later');
      if(typeof env.BUCKET.put!=='function')throw Error('cache_unavailable');
      const series=await priceThrottled(async()=>{
        if(Date.now()<priceBlockedUntil)throw Error('source_retry_later');
        const symbol=priceSymbol(code),from=listedDate&&listedDate>window.start?listedDate:window.start;
        if(from>through)return [];
        const url=new URL(PRICE_SOURCE);url.searchParams.set('param',[symbol,'day',from,through,'2000',''].join(','));
        const response=await (env.PRICE_FETCH||fetch)(url.href,{headers:{Accept:'application/json'},redirect:'error',signal:AbortSignal.timeout(12000)});
        if([401,403,429].includes(response.status)){priceBlockedUntil=Date.now()+2*3600000;throw Error('source_access_limited');}
        if(!response.ok)throw Error('source_unavailable');
        return priceParse(await priceJSON(response),code,window,through);
      });
      const receivedDates=new Set(series.map(r=>r[0]));
      if(cached?.series.some(r=>r[0]<=through&&!receivedDates.has(r[0])))throw Error('source_history_regression');
      const record={schema:1,stock_code:code,window_start:window.start,window_end:window.end,checked_through:through,fetched_at:new Date().toISOString(),source:PRICE_SOURCE,adjustment:'none',price_unit:'元/股',series};
      if(!priceRecordValid(record,code,window))throw Error('invalid_cache');
      await env.BUCKET.put(key,JSON.stringify(record));
      return {record,error:null,cached:false};
    }catch(e){
      const reason=['source_access_limited','source_retry_later','identity_or_shape','date_or_order','invalid_price','source_history_regression'].includes(e?.message)?e.message:'source_unavailable';
      if(typeof env.BUCKET.put==='function')try{await env.BUCKET.put(attemptKey,JSON.stringify({attempted_at:new Date().toISOString(),target_end:through,reason}));}catch{}
      return {record:cached,error:reason,cached:true};
    }
  })();priceFlights.set(flightKey,pending);
  try{return await pending;}finally{priceFlights.delete(flightKey);}
}
async function pricesApi(request,env,url){
  if(request.method!=='GET')return error(40500,'方法不支持',405);
  const match=/^\/api\/v1\/prices\/stock\/(\d{6})$/.exec(url.pathname);if(!match)return error(40001,'股票价格路径非法');
  if([...url.searchParams.keys()].some(k=>!['start','end'].includes(k))||url.searchParams.getAll('start').length>1||url.searchParams.getAll('end').length>1)return error(40001,'价格参数非法');
  const manifest=await getJSON(env.BUCKET,'market-latest.json');if(!manifest||!VERSION.test(manifest.version||'')||!dateOK(manifest.target_end))return error(50301,'股票目录尚未就绪',503);
  const catalog=await getJSON(env.BUCKET,mpath(manifest.version,'catalog')),stock=catalog?.items?.find(s=>s.stock_code===match[1]);if(!stock||!priceSymbol(stock.stock_code))return error(40401,'股票不在已核验目录中',404);
  const start=url.searchParams.get('start')||manifest.target_start,end=url.searchParams.get('end')||manifest.target_end;
  if(!dateOK(start)||!dateOK(end)||start<'1990-01-01'||start>end||end>manifest.target_end)return error(40001,'请选择已收盘数据范围内的日期');
  const listedDate=dateOK(stock.stock_listed_date)?stock.stock_listed_date:null;
  const effectiveStart=listedDate&&listedDate>start?listedDate:start;
  const windows=effectiveStart>end?[]:priceWindows(effectiveStart,end);if(windows.length>8)return error(40001,'价格范围超出上限');
  // Fund history begins in 2004. Prices require the independently published full exchange calendar.
  const marketMeta=await getJSON(env.BUCKET,mpath(manifest.version,'meta')),calendarManifest=await getJSON(env.BUCKET,'broad-latest.json');
  if(!marketMeta||marketMeta.version!==manifest.version||marketMeta.target_end!==manifest.target_end||!VERSION.test(marketMeta.history_version||'')||!calendarManifest||!VERSION.test(calendarManifest.version||'')||calendarManifest.target_end!==manifest.target_end||!dateOK(calendarManifest.target_start))return error(50301,'完整股价日历与当前市场版本尚未对齐',503);
  const calendarMeta=await getJSON(env.BUCKET,bpath(calendarManifest.version,'meta')),calendarData=await getJSON(env.BUCKET,bpath(calendarManifest.version,'calendar')),calendar=calendarData?.dates;
  if(!calendarMeta||calendarMeta.version!==calendarManifest.version||calendarMeta.history_version!==marketMeta.history_version||calendarMeta.target_start!==calendarManifest.target_start||calendarMeta.target_end!==manifest.target_end||!Array.isArray(calendar)||!calendar.length||calendar.length>12000||calendar[0]!==calendarManifest.target_start||calendar.at(-1)!==manifest.target_end||!calendar.every((d,i)=>dateOK(d)&&(!i||d>calendar[i-1])))return error(50301,'完整股价日历与当前市场版本尚未对齐',503);
  const openDates=new Set(calendar);
  const tradingDates=calendar.filter(d=>d>=effectiveStart&&d<=end);
  const eastmoney=await priceReadEastmoney(env,stock.stock_code,listedDate,manifest.target_end);
  const results=eastmoney?[{record:eastmoney,error:null,cached:true}]:await Promise.all(windows.map(w=>priceCachedWindow(env,stock.stock_code,w,manifest.target_end,listedDate)));
  const provider=eastmoney?'eastmoney':'tencent',sourceLabel=eastmoney?'东方财富不复权日K':'腾讯不复权日K',sourceURL=eastmoney?PRICE_EASTMONEY_SOURCE:PRICE_SOURCE;
  const sourceRows=results.flatMap(x=>x.record?.series||[]).filter(r=>r[0]>=effectiveStart&&r[0]<=end);
  const sourceCalendarExcludedCount=sourceRows.filter(r=>!openDates.has(r[0])).length;
  const series=sourceRows.filter(r=>openDates.has(r[0])).map(r=>({trade_date:r[0],close:r[1]})).sort((a,b)=>a.trade_date.localeCompare(b.trade_date));
  const failed=results.filter(x=>x.error).length,availableStart=series[0]?.trade_date||null,availableEnd=series.at(-1)?.trade_date||null,expectedLast=tradingDates.at(-1)||null;
  const checkedThrough=results.map(x=>x.record?.checked_through).filter(Boolean).sort().at(-1)||null;
  const sourcePending=expectedLast!==null&&(!checkedThrough||checkedThrough<expectedLast);
  const coverage={effective_start:effectiveStart,requested_start:start,requested_end:end,calendar_days:tradingDates.length,price_days:series.length,unpriced_calendar_days:Math.max(0,tradingDates.length-series.length),all_calendar_dates_priced:tradingDates.length>0&&series.length===tradingDates.length,listing_boundary_verified:listedDate!==null,source_checked_through:checkedThrough,source_calendar_excluded_count:sourceCalendarExcludedCount};
  const notes=['价格来源：'+sourceLabel+'。本条曲线仅使用这一来源，未与其他提供方拼接。','股价为不复权日收盘价，单位元/股；拆分和分红可能造成价格跳变，不能作为复权收益率。','仅显示来源真实成交日期；停牌、未披露或源缺失不填零、不沿用旧价冒充当日。'];
  if(eastmoney&&sourcePending)notes.push('该来源缓存已核验至 '+checkedThrough+'，尚未到所选期末；保留原日期，不拼接其他来源补尾。');
  if(failed)notes.push('部分历史窗口暂未取得新行情，已保留可核验旧记录及原日期，请稍后重试。');
  if(coverage.unpriced_calendar_days)notes.push('所选范围有 '+coverage.unpriced_calendar_days+' 个日历交易日没有有效收盘记录，可能为停牌或来源缺口。');
  if(sourceCalendarExcludedCount)notes.push('所选有效范围有 '+sourceCalendarExcludedCount+' 条源行情日期与当前正式交易日历不一致，待核验未绘图；原始记录保留，不据此推断休市。');
  if(!listedDate)notes.push('尚未核验该股票的正式上市日期，不将最早可用价格日推断为上市日。');
  if(availableEnd&&availableEnd!==expectedLast)notes.push('所选范围最近可用价格日期为 '+availableEnd+'，不代表目标日价格已披露。');
  return ok({stock_code:stock.stock_code,stock_name:stock.stock_name,stock_listed_date:listedDate,listed_date:listedDate,listing_status:listedDate?'verified':'unknown',start,end,target_end:manifest.target_end,market_version:manifest.version,history_version:marketMeta.history_version,calendar_version:calendarManifest.version,calendar_start:calendar[0],calendar_end:calendar.at(-1),trading_dates:tradingDates,coverage,price_unit:'元/股',adjustment:'none',provider,source_label:sourceLabel,source:sourceURL,source_url:sourceURL,source_checked_through:checkedThrough,source_calendar_excluded_count:sourceCalendarExcludedCount,source_record_count:eastmoney?eastmoney.provider_total:null,as_of:results.map(x=>x.record?.fetched_at).filter(Boolean).sort().at(-1)||null,available_start:availableStart,available_end:availableEnd,status:sourceCalendarExcludedCount?'partial':!series.length?'unavailable':failed||sourcePending||availableEnd!==expectedLast||coverage.unpriced_calendar_days?'partial':'ready',failed_windows:failed,series,notes});
}
