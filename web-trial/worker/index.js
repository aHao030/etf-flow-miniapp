// The build prepends a path-to-asset map named ASSETS. No external runtime dependencies.
const REQUIRED = ['meta','catalog','ranks','industries','etfs','etf-details'];
const MAX_BYTES = 2 * 1024 * 1024;
const CODE = /^\d{6}$/;
const VERSION = /^[a-z0-9-]{1,64}$/;
const KEY = /^(?:meta|catalog|ranks|industries|etfs|etf-details|stocks-\d{4})$/;
const RANK_PERIODS = ['1d','5d','20d'];
const STOCK_PERIODS = [...RANK_PERIODS,'3m','6m','1y','2y','3y','5y'];
const LONG_PERIODS = STOCK_PERIODS.slice(RANK_PERIODS.length);
const HEADERS = {
  'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; object-src 'none'",
  'Cache-Control': 'no-store'
};
const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {...HEADERS, 'Content-Type':'application/json; charset=utf-8'}});
const ok = data => json({code:0,message:'ok',data});
const error = (code,message,status=400) => json({code,message,data:null},status);
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const dateOK = x => typeof x === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(x) && !Number.isNaN(Date.parse(x)) && new Date(x).toISOString().slice(0,10) === x;
const instantOK = x => typeof x === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(x) && dateOK(x.slice(0,10)) && !Number.isNaN(Date.parse(x));
const dataItems = data => Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : null;
const stockValidWindow = w => object(w) && dateOK(w.window_start) && dateOK(w.window_end) && w.window_start <= w.window_end && typeof w.start_exclusive === 'boolean' && ['verified','observed'].includes(w.calendar_status) && (w.window_days === null || Number.isSafeInteger(w.window_days) && w.window_days >= 0) && (w.calendar_status !== 'verified' || w.window_days !== null);
const stockFiniteOrNull = x => x === null || (typeof x === 'number' && Number.isFinite(x));
const stockValidCompactSeries = rows => Array.isArray(rows) && rows.length <= 5000 && rows.every((r,i) => Array.isArray(r) && r.length === 3 && dateOK(r[0]) && stockFiniteOrNull(r[1]) && stockFiniteOrNull(r[2]) && (i === 0 || rows[i-1][0] < r[0]));
function stockExpectedAnchor(period, end) {
  const months={'3m':3,'6m':6,'1y':12,'2y':24,'3y':36,'5y':60}[period];
  if (!months || !dateOK(end)) return null;
  const [year,month,day]=end.split('-').map(Number), offset=year*12+month-1-months;
  const y=Math.floor(offset/12), m=offset-y*12+1;
  if (y < 0 || y > 9999) return null;
  const leap=y%4===0 && (y%100!==0 || y%400===0);
  const last=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31][m-1];
  return String(y).padStart(4,'0')+'-'+String(m).padStart(2,'0')+'-'+String(Math.min(day,last)).padStart(2,'0');
}
const stockValidLongWindow = (w,period) => stockValidWindow(w) && w.start_exclusive === true && w.window_start === stockExpectedAnchor(period,w.window_end) && (w.calendar_status !== 'observed' || w.window_days === null);
const stockValidShortWindow = (w,period) => stockValidWindow(w) && w.start_exclusive === false && w.window_days === {'1d':1,'5d':5,'20d':20}[period];
const stockValidLongMovement = (m, period) => {
  if (!stockValidLongWindow(m,period) || m.period !== period || typeof m.complete !== 'boolean' || !Number.isSafeInteger(m.valid_days) || m.valid_days < 0 || (m.window_days !== null && m.valid_days > m.window_days)) return false;
  if (m.missing_days !== (m.window_days === null ? null : m.window_days - m.valid_days)) return false;
  if (m.valid_days === 0) {
    if (m.available_start !== null || m.available_end !== null || m.observed_net_flow !== null) return false;
  } else if (!dateOK(m.available_start) || !dateOK(m.available_end) || m.available_start <= m.window_start || m.available_start > m.available_end || m.available_end > m.window_end || m.observed_net_flow === null) return false;
  if (!['net_flow','inflow','outflow','observed_net_flow'].every(k => stockFiniteOrNull(m[k]))) return false;
  if (!m.complete) return m.net_flow === null && m.inflow === null && m.outflow === null;
  return m.calendar_status === 'verified' && m.window_days > 0 && m.valid_days === m.window_days && [m.net_flow,m.inflow,m.outflow,m.observed_net_flow].every(x => x !== null);
};
const shape = (key, data) => {
  if (!object(data)) return false;
  if (key === 'meta') return dateOK(data.trade_date || data.latest_date) && VERSION.test(data.version || '') && instantOK(data.generated_at) && (data.stock_windows === undefined || (object(data.stock_windows) && Object.entries(data.stock_windows).every(([p,w]) => STOCK_PERIODS.includes(p) && (LONG_PERIODS.includes(p) ? stockValidLongWindow(w,p) : stockValidShortWindow(w,p)))));
  if (key === 'catalog') return Array.isArray(data.items) && data.items.every(x => object(x) && CODE.test(x.stock_code || '') && typeof x.data_status === 'string') && new Set(data.items.map(x => x.stock_code)).size === data.items.length;
  if (['industries','etfs'].includes(key)) return Array.isArray(data.items) && data.items.every(object);
  if (key === 'ranks') return RANK_PERIODS.every(p => Array.isArray(data[p]) && data[p].every(x => object(x) && CODE.test(x.stock_code || '') && (x.flow === null || (typeof x.flow === 'number' && Number.isFinite(x.flow)))));
  if (key === 'etf-details') return Object.entries(data).every(([k,v]) => CODE.test(k) && object(v));
  if (key.startsWith('stocks-')) return Object.entries(data).every(([k,v]) => CODE.test(k) && k.startsWith(key.slice(-4)) && object(v) && object(v.base) && (v.base.series_compact === undefined || (v.base.series === undefined && stockValidCompactSeries(v.base.series_compact))) && object(v.periods) && RANK_PERIODS.every(p => object(v.periods[p])) && Object.entries(v.periods).every(([p,x]) => STOCK_PERIODS.includes(p) && object(x) && (!LONG_PERIODS.includes(p) || stockValidLongMovement(x.movement,p))));
  return false;
};
async function getJSON(bucket, key) {
  const value = await bucket.get(key);
  if (!value) return null;
  return JSON.parse(await value.text());
}
async function bodyJSON(request) {
  const len = request.headers.get('content-length');
  if (len && (!/^\d+$/.test(len) || Number(len) > MAX_BYTES)) throw new Error('size');
  if (!/^application\/json(?:\s*;|\s*$)/i.test(request.headers.get('content-type') || '')) throw new Error('type');
  const reader = request.body?.getReader();
  if (!reader) throw new Error('body');
  let size = 0, chunks = [];
  while (true) {
    const part = await reader.read();
    if (part.done) break;
    size += part.value.byteLength;
    if (size > MAX_BYTES) { await reader.cancel(); throw new Error('size'); }
    chunks.push(part.value);
  }
  const data = new Uint8Array(size); let i = 0;
  for (const c of chunks) { data.set(c,i); i += c.byteLength; }
  return {value:JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(data)),bytes:data};
}
const filesValid = files => Array.isArray(files) && files.length <= 200 && files.every(k => typeof k === 'string' && KEY.test(k)) && new Set(files).size === files.length && REQUIRED.every(k => files.includes(k));
const hashesValid = (files,hashes) => object(hashes) && Object.keys(hashes).length === files.length && files.every(k => typeof hashes[k] === 'string' && /^[0-9a-f]{64}$/.test(hashes[k]));
const digest = async bytes => [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x => x.toString(16).padStart(2,'0')).join('');
const paginate = (url) => {
  const p=url.searchParams.get('page') ?? '1', s=url.searchParams.get('page_size') ?? '20';
  if (!/^\d+$/.test(p) || !/^\d+$/.test(s)) return null;
  const page=Number(p), page_size=Number(s);
  if (!Number.isSafeInteger(page) || page<1 || !Number.isSafeInteger(page_size) || page_size<1 || page_size>100) return null;
  return {page,page_size};
};
function paging(items, pg, td, extra = {}) {
  const start = (pg.page-1)*pg.page_size;
  return {trade_date:td,...extra,total:items.length,...pg,has_more:start+pg.page_size<items.length,items:items.slice(start,start+pg.page_size)};
}
const snapshotKey = (version,key) => `snapshots/${version}/${key}.json`;
const weightSource = value => !value ? null : value.startsWith('index') ? 'index' : /^(report|quarterly)/.test(value) ? 'quarterly' : 'none';

async function internal(request, env, url) {
  const secret = env.UPDATE_SECRET;
  if (typeof secret !== 'string' || !secret || request.headers.get('authorization') !== `Bearer ${secret}`) return error(40101,'未授权',401);
  if (request.method === 'PUT') {
    const m = /^\/internal\/snapshots\/([^/]+)\/([^/]+)\.json$/.exec(url.pathname);
    if (!m || !VERSION.test(m[1]) || !KEY.test(m[2])) return error(40001,'版本或文件名非法');
    let body, bytes;
    try { ({value:body,bytes}=await bodyJSON(request)); } catch { return error(40001,'JSON 无效、类型错误或超过大小限制'); }
    if (!shape(m[2],body)) return error(40001,'数据结构无效');
    if (await env.BUCKET.get(`snapshots/${m[1]}/committed.json`)) return error(40901,'版本已发布，不能修改',409);
    const sha256 = await digest(bytes);
    await env.BUCKET.put(snapshotKey(m[1],m[2]), bytes, {httpMetadata:{contentType:'application/json'},customMetadata:{sha256}});
    return ok({version:m[1],key:m[2],sha256});
  }
  if (request.method === 'POST' && url.pathname === '/internal/commit') {
    let body;
    try { ({value:body}=await bodyJSON(request)); } catch { return error(40001,'JSON 无效、类型错误或超过大小限制'); }
    if (!object(body) || !VERSION.test(body.version || '') || !filesValid(body.files) || !hashesValid(body.files,body.hashes) || !dateOK(body.trade_date)) return error(40001,'发布参数非法');
    if (await env.BUCKET.get(`snapshots/${body.version}/committed.json`)) return error(40901,'版本已发布，不能重复使用',409);
    // Keep only the small cross-file indexes; release each parsed stock/rank
    // chunk before parsing the next one to bound peak memory on large snapshots.
    const loaded = {}, stockCodes = new Set(), etfCodes = new Set();
    for (const key of body.files) {
      const head = await env.BUCKET.head(snapshotKey(body.version,key));
      if (!head || head.customMetadata?.sha256 !== body.hashes[key] || head.size > MAX_BYTES) return error(40902,'快照文件摘要不匹配',409);
      const d = await getJSON(env.BUCKET,snapshotKey(body.version,key));
      if (d === null || !shape(key,d)) return error(40902,'快照不完整或文件结构无效',409);
      if (key === 'meta' && ((d.trade_date || d.latest_date) !== body.trade_date || d.version !== body.version)) return error(40902,'快照日期或版本不匹配',409);
      if (key === 'meta' || key === 'catalog' || key === 'etfs') loaded[key] = d;
      else if (key === 'etf-details') for (const code of Object.keys(d)) etfCodes.add(code);
      else if (key.startsWith('stocks-')) for (const code of Object.keys(d)) {
        if (stockCodes.has(code)) return error(40902,'快照包含重复股票详情',409);
        stockCodes.add(code);
      }
    }
    for (const stock of loaded.catalog.items) {
      if (stock.data_status !== 'no_data' && !stockCodes.has(stock.stock_code)) return error(40902,'快照缺少覆盖股票详情',409);
    }
    for (const etf of loaded.etfs.items) {
      if (!CODE.test(etf.etf_code || '') || !etfCodes.has(etf.etf_code)) return error(40902,'快照缺少 ETF 详情',409);
    }
    const current = await getJSON(env.BUCKET,'latest.json');
    if (current && (body.trade_date < current.trade_date || (body.trade_date === current.trade_date && (!instantOK(current.generated_at) || Date.parse(loaded.meta.generated_at) <= Date.parse(current.generated_at))))) return error(40903,'快照比当前数据更旧',409);
    const latest = {version:body.version,files:body.files,hashes:body.hashes,published_at:new Date().toISOString(),trade_date:body.trade_date,generated_at:loaded.meta.generated_at};
    await env.BUCKET.put(`snapshots/${body.version}/committed.json`,JSON.stringify(latest));
    await env.BUCKET.put('latest.json',JSON.stringify(latest),{httpMetadata:{contentType:'application/json'}});
    return ok(latest);
  }
  return error(40400,'接口不存在',404);
}
async function api(request,env,url) {
  if (request.method !== 'GET') return error(40500,'方法不支持',405);
  const manifest=await getJSON(env.BUCKET,'latest.json');
  if (!manifest || !VERSION.test(manifest.version || '') || !filesValid(manifest.files) || !dateOK(manifest.trade_date)) return error(50301,'数据未就绪',503);
  const td=manifest.trade_date;
  const wantedDate=url.searchParams.get('trade_date');
  if (wantedDate && wantedDate !== td) return error(40404,'所请求日期不可用',404);
  const read=async k => manifest.files.includes(k) ? getJSON(env.BUCKET,snapshotKey(manifest.version,k)) : null;
  const p=url.pathname;
  if (p === '/api/v1/meta') {
    const meta=await read('meta');
    return meta ? ok({...meta,latest_date:td}) : error(50301,'数据未就绪',503);
  }
  if (p === '/api/v1/industries') { const d=await read('industries'); return d ? ok(d) : error(50301,'数据未就绪',503); }
  if (p === '/api/v1/stocks') {
    const pg=paginate(url), q=(url.searchParams.get('q') || '').trim();
    if (!pg || q.length>50) return error(40001,'搜索或分页参数非法');
    const d=await read('catalog'); if (!d || !Array.isArray(d.items)) return error(50301,'数据未就绪',503);
    const filtered=d.items.filter(r => typeof r.stock_code === 'string' && (r.stock_code.includes(q) || (r.stock_name || '').includes(q)));
    return ok(paging(filtered,pg,td));
  }
  if (p === '/api/v1/rank') {
    const pg=paginate(url), period=url.searchParams.get('period') || '1d', direction=url.searchParams.get('direction') || 'in', industry=url.searchParams.get('industry') || '';
    if (!pg || !RANK_PERIODS.includes(period) || !['in','out'].includes(direction)) return error(40002,'排序或分页参数非法');
    const [r,ind]=await Promise.all([read('ranks'),read('industries')]);
    if (!r || !Array.isArray(r[period]) || !ind) return error(50301,'数据未就绪',503);
    const found=industry && dataItems(ind)?.find(x => x.industry_code===industry);
    if (industry && !found) return error(40403,'行业不存在',404);
    const rows=r[period].filter(x => typeof x.flow === 'number' && Number.isFinite(x.flow) && (direction==='in' ? x.flow>0 : x.flow<0) && (!industry || x.industry===industry || x.industry_code===industry)).slice().sort((a,b) => (direction==='in' ? b.flow-a.flow : a.flow-b.flow) || a.stock_code.localeCompare(b.stock_code)).map((x,i) => ({...x,rank:i+1}));
    return ok(paging(rows,pg,td,{period,direction,industry:industry||null,industry_name:found?.industry_name || '全部行业'}));
  }
  if (p === '/api/v1/etfs') {
    const pg=paginate(url), category=url.searchParams.get('category');
    if (!pg || (category && !['宽基','行业','主题'].includes(category))) return error(40002,'分类或分页参数非法');
    const data=await read('etfs'); if (!data || !Array.isArray(data.items)) return error(50301,'数据未就绪',503);
    return ok(paging(data.items.filter(x=>!category || x.category===category),pg,td));
  }
  const stock=/^\/api\/v1\/stock\/([^/]+)$/.exec(p);
  if (stock) {
    const code=stock[1], period=url.searchParams.get('period') || '1d';
    if (!CODE.test(code) || !STOCK_PERIODS.includes(period)) return error(40001,'股票代码或周期非法');
    const [catalog,chunk]=await Promise.all([read('catalog'),read('stocks-'+code.slice(0,4))]);
    const item=catalog?.items?.find(x=>x.stock_code===code);
    if (!item) return error(40401,'股票不存在',404);
    const found=chunk?.[code];
    if (found) {
      const ext=found.periods?.[period];
      if (!object(found.base) || !object(ext)) return error(50301,'所选周期数据暂不可用',503);
      const movement=ext.movement;
      if (LONG_PERIODS.includes(period) && (!stockValidLongMovement(movement,period) || movement.window_end !== td)) return error(50301,'所选周期数据不完整',503);
      let series=found.base.series;
      if (found.base.series_compact !== undefined) {
        if (!stockValidCompactSeries(found.base.series_compact) || series !== undefined) return error(50301,'股票序列数据不完整',503);
        series=found.base.series_compact.map(([trade_date,flow,intensity_bp])=>({trade_date,flow,intensity_bp}));
      }
      if (stockValidWindow(movement) && movement.window_end === td && Array.isArray(series)) {
        series=series.filter(r=>object(r) && dateOK(r.trade_date) && (movement.start_exclusive ? r.trade_date > movement.window_start : r.trade_date >= movement.window_start) && r.trade_date <= movement.window_end);
      }
      const {series_compact,...base}=found.base;
      return ok({...base,...ext,...(series !== undefined ? {series} : {}),trade_date:td});
    }
    if (item.data_status !== 'no_data') return error(50301,'股票数据不完整',503);
    let window;
    if (LONG_PERIODS.includes(period)) {
      const meta=await read('meta'); window=meta?.stock_windows?.[period];
      if (!stockValidLongWindow(window,period) || window.window_end !== td) return error(50301,'所选周期日历暂不可用',503);
    } else {
      window={window_days:{'1d':1,'5d':5,'20d':20}[period]};
    }
    return ok({...item,trade_date:td,stock_code:code,flow_1d:null,flow_5d:null,flow_20d:null,intensity_1d_bp:null,intensity_5d_bp:null,intensity_20d_bp:null,float_mcap:null,valid_days:{d1:'0/1',d5:'0/5',d20:'0/20'},series:[],top_etfs:[],etf_contributions:[],data_status:'no_data',quality:{weight_source:null,coverage:null,flags:[],note:''},movement:{...window,period,net_flow:null,inflow:null,outflow:null,observed_net_flow:null,available_start:null,available_end:null,etf_count:0,inflow_etf_count:0,outflow_etf_count:0,missing_etf_count:0,valid_days:0,missing_days:window.window_days,complete:false},coverage:{tracked_etfs:0,active_etfs:0,contributing_etfs:0,pending_etfs:0,note:'仅统计追踪池内 ETF。'},history_basis:'current_weights',history_note:catalog.history_note || '历史数据按当前权重回溯估算，不适用于历史策略回测。'});
  }
  const etf=/^\/api\/v1\/etf\/([^/]+)$/.exec(p);
  if (etf) {
    if (!CODE.test(etf[1])) return error(40001,'ETF 代码非法');
    const d=await read('etf-details'); return d?.[etf[1]] ? ok({...d[etf[1]],trade_date:td}) : error(40402,'ETF 不存在',404);
  }
  return error(40400,'接口不存在',404);
}
export default {
  async fetch(request,env) {
    try {
      const url=new URL(request.url);
      if (url.pathname.startsWith('/api/v1/prices/')) return await pricesApi(request,env,url);
      if (url.pathname.startsWith('/internal/market/')) return await marketInternal(request,env,url);
      if (url.pathname.startsWith('/api/v1/market/')) return await marketApi(request,env,url);
      if (url.pathname.startsWith('/internal/broad/')) return await broadInternal(request,env,url);
      if (url.pathname.startsWith('/api/v1/broad/')) return await broadApi(request,env,url);
      if (url.pathname.startsWith('/internal/history/')) return await historyInternal(request,env,url);
      if (url.pathname.startsWith('/api/v1/history/')) return await historyApi(request,env,url);
      if (url.pathname.startsWith('/internal/')) return await internal(request,env,url);
      if (url.pathname.startsWith('/api/')) return await api(request,env,url);
      if (request.method !== 'GET' && request.method !== 'HEAD') return error(40500,'方法不支持',405);
      const path=url.pathname === '/' ? '/index.html' : url.pathname;
      const asset=ASSETS[path];
      if (!asset || !Object.prototype.hasOwnProperty.call(ASSETS,path)) return new Response('Not found',{status:404,headers:HEADERS});
      return new Response(request.method==='HEAD' ? null : asset.body,{headers:{...HEADERS,'Content-Type':asset.contentType}});
    } catch { return error(50001,'服务暂时不可用',500); }
  }
};
