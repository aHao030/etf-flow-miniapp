// Independent, bounded archive snapshots. The build concatenates this before index.js.
const HKEY = /^(?:meta|catalog|data-\d{4})$/;
const HQUALITY = ['raw_observation','estimated','missing_previous_shares','missing_current_shares','missing_previous_nav','missing_current_nav','possible_split','events_unverified','fund_event','money_market'];
const hkey = (version,key) => `history/${version}/${key}.json`;
const finiteOrNull = x => x === null || (typeof x === 'number' && Number.isFinite(x));
function hshape(key,data) {
  if (!object(data)) return false;
  if (key==='meta') return ['2021-01-01','2004-12-30'].includes(data.target_start) && dateOK(data.target_end) && data.target_end>=data.target_start && VERSION.test(data.version || '') && instantOK(data.generated_at) && data.complete===false && Number.isSafeInteger(data.total_funds);
  if (key==='catalog') return Array.isArray(data.items) && data.items.length<=10000 && data.items.every(f=>object(f)&&CODE.test(f.fund_code||'')&&['SH','SZ'].includes(f.market)&&typeof f.fund_name==='string'&&/^data-\d{4}$/.test(f.file||'')) && new Set(data.items.map(x=>x.fund_code)).size===data.items.length;
  if (key.startsWith('data-')) {
    if (!object(data.funds) || Object.keys(data.funds).length>500) return false;
    return Object.entries(data.funds).every(([code,rows])=>CODE.test(code)&&Array.isArray(rows)&&rows.length<=8000&&rows.every((r,i)=>Array.isArray(r)&&r.length===6&&dateOK(r[0])&&r[0]>='2004-12-30'&&[r[1],r[2],r[3],r[4]].every(finiteOrNull)&&(r[1]===null||r[1]>=0)&&(r[2]===null||r[2]>0)&&HQUALITY.includes(r[5])&&(r[5]==='estimated'?r[4]!==null:r[4]===null)&&(!i||r[0]>rows[i-1][0])));
  }
  return false;
}
async function historyInternal(request,env,url) {
  if (!env.UPDATE_SECRET || request.headers.get('authorization')!==`Bearer ${env.UPDATE_SECRET}`) return error(40101,'未授权',401);
  if (request.method==='PUT') {
    const m=/^\/internal\/history\/([^/]+)\/([^/]+)\.json$/.exec(url.pathname);
    if (!m||!VERSION.test(m[1])||!HKEY.test(m[2])) return error(40001,'历史版本或文件名非法');
    if (await env.BUCKET.get(hkey(m[1],'committed'))) return error(40901,'历史版本已发布',409);
    let b; try { b=await bodyJSON(request); } catch { return error(40001,'历史数据格式或大小非法'); }
    if (!hshape(m[2],b.value)) return error(40001,'历史数据结构非法');
    const sha256=await digest(b.bytes), metadata={sha256};
    if (m[2].startsWith('data-')) metadata.codes=JSON.stringify(Object.keys(b.value.funds).sort());
    await env.BUCKET.put(hkey(m[1],m[2]),b.bytes,{httpMetadata:{contentType:'application/json'},customMetadata:metadata});
    return ok({version:m[1],key:m[2],sha256});
  }
  if (request.method==='POST'&&url.pathname==='/internal/history/commit') {
    let b; try { b=(await bodyJSON(request)).value; } catch { return error(40001,'历史发布参数非法'); }
    if (!object(b)||!VERSION.test(b.version||'')||!Array.isArray(b.files)||b.files.length>500||new Set(b.files).size!==b.files.length||!b.files.every(k=>HKEY.test(k))||!['meta','catalog'].every(k=>b.files.includes(k))||!hashesValid(b.files,b.hashes)||!['2021-01-01','2004-12-30'].includes(b.target_start)||!dateOK(b.target_end)||!instantOK(b.generated_at)) return error(40001,'历史清单非法');
    if (await env.BUCKET.get(hkey(b.version,'committed'))) return error(40901,'历史版本已发布',409);
    const codes={};
    // Validate digests and per-shard membership without loading the entire history into memory.
    for (const k of b.files) {
      const head=await env.BUCKET.head(hkey(b.version,k));
      if (!head||head.size>MAX_BYTES||head.customMetadata?.sha256!==b.hashes[k]) return error(40902,'历史文件缺失或摘要不符',409);
      if (k.startsWith('data-')) {
        let members; try { members=JSON.parse(head.customMetadata.codes); } catch { return error(40902,'历史分片无成员清单',409); }
        if (!Array.isArray(members)) return error(40902,'历史分片清单非法',409);
        for(const code of members) { if(codes[code]) return error(40902,'历史 ETF 重复',409); codes[code]=k; }
      }
    }
    const meta=await getJSON(env.BUCKET,hkey(b.version,'meta')),catalog=await getJSON(env.BUCKET,hkey(b.version,'catalog'));
    if(!hshape('meta',meta)||!hshape('catalog',catalog)||meta.version!==b.version||meta.generated_at!==b.generated_at||meta.target_end!==b.target_end||catalog.items.length!==meta.total_funds||catalog.items.length!==Object.keys(codes).length||!catalog.items.every(f=>codes[f.fund_code]===f.file)) return error(40902,'历史目录与分片不一致',409);
    const current=await getJSON(env.BUCKET,'history-latest.json');
    if(current&&(b.target_end<current.target_end||Date.parse(b.generated_at)<=Date.parse(current.generated_at))) return error(40903,'拒绝历史版本回退',409);
    const latest={...b,published_at:new Date().toISOString()};
    await env.BUCKET.put(hkey(b.version,'committed'),JSON.stringify(latest));
    await env.BUCKET.put('history-latest.json',JSON.stringify(latest),{httpMetadata:{contentType:'application/json'}});
    return ok(latest);
  }
  return error(40400,'接口不存在',404);
}
function hcoverage(rows,index) {
  const days=rows.filter(r=>r[index]!==null).map(r=>r[0]);
  return {count:days.length,start:days[0]||null,end:days.at(-1)||null};
}
function hwatchItem(fund, rows, date) {
  const current=rows.find(r=>r[0]===date), shares=[...rows].reverse().find(r=>r[0]<=date&&r[1]!==null), nav=[...rows].reverse().find(r=>r[0]<=date&&r[2]!==null);
  const previous=[...rows].reverse().find(r=>r[0]<date&&r[1]!==null);
  const estimated=current?.[5]==='estimated'&&current[4]!==null;
  return {fund_code:fund.fund_code,fund_name:fund.fund_name,market:fund.market,status:current?'observed':'pending',target_date:date,
    shares:current?.[1]??null,shares_date:current?.[1]!=null?date:null,
    unit_nav:current?.[2]??null,nav_date:current?.[2]!=null?date:null,
    latest_shares:shares?.[1]??null,latest_shares_date:shares?.[0]??null,latest_nav:nav?.[2]??null,latest_nav_date:nav?.[0]??null,
    previous_observed_shares:previous?.[1]??null,previous_observed_shares_date:previous?.[0]??null,
    share_change:estimated&&previous&&current[1]!==null?current[1]-previous[1]:null,
    estimated_flow:current?.[4]??null,flow_date:estimated?date:null,flow_quality:current?.[5]||'missing_current_shares'};
}
function historyDataDates(meta, catalog) {
  const dates = {shares:{date:null,count:0},nav:{date:null,count:0},flow:{date:null,count:0}};
  if (!hshape('catalog',catalog) || catalog.items.length!==meta.total_funds || !dateOK(meta.target_end)) return dates;
  for (const fund of catalog.items) {
    for (const field of Object.keys(dates)) {
      const coverage = fund[field];
      if (!object(coverage) || !Number.isSafeInteger(coverage.count) || coverage.count<=0 || !dateOK(coverage.end) || coverage.end<meta.target_start || coverage.end>meta.target_end) continue;
      const latest = dates[field];
      if (latest.date===null || coverage.end>latest.date) {
        latest.date = coverage.end;
        latest.count = 1;
      } else if (coverage.end===latest.date) {
        latest.count++;
      }
    }
  }
  return dates;
}
async function historyApi(request,env,url) {
  if(request.method!=='GET') return error(40500,'方法不支持',405);
  const manifest=await getJSON(env.BUCKET,'history-latest.json');
  if(!manifest||!VERSION.test(manifest.version||'')||!Array.isArray(manifest.files)) return error(50301,'历史数据正在准备',503);
  const read=k=>manifest.files.includes(k)?getJSON(env.BUCKET,hkey(manifest.version,k)):null;
  if(url.pathname==='/api/v1/history/meta') {
    const meta=await read('meta');
    if (!meta) return error(50301,'历史数据未就绪',503);
    let catalog;
    try { catalog=await read('catalog'); } catch { catalog=null; }
    return ok({...meta,data_dates:historyDataDates(meta,catalog)});
  }
  const pg=paginate(url); if(!pg) return error(40001,'分页参数非法');
  const catalog=await read('catalog'); if(!catalog?.items) return error(50301,'历史目录未就绪',503);
  if(url.pathname==='/api/v1/history/watchlist') {
    const input=(url.searchParams.get('codes')||'').split(',');
    if(input.length>30||!input.length||!input.every(code=>CODE.test(code))) return error(40001,'监控列表限1至30个有效ETF代码');
    const codes=[...new Set(input)], funds=codes.map(code=>catalog.items.find(f=>f.fund_code===code)), groups=new Map(), results=new Map();
    funds.forEach((fund,index)=>{
      if(!fund) results.set(codes[index],{fund_code:codes[index],status:'not_found',target_date:manifest.target_end});
      else { if(!groups.has(fund.file)) groups.set(fund.file,[]); groups.get(fund.file).push(fund); }
    });
    // Read each shard only once, sequentially, so a broad watchlist stays below Worker memory limits.
    for(const [file,members] of groups) {
      const chunk=await read(file);
      for(const fund of members) {
        const rows=chunk?.funds?.[fund.fund_code];
        if(!Array.isArray(rows)||rows.length>8000) return error(50301,'历史分片未就绪',503);
        results.set(fund.fund_code,hwatchItem(fund,rows,manifest.target_end));
      }
    }
    return ok({version:manifest.version,target_date:manifest.target_end,items:codes.map(code=>results.get(code)),note:'当日字段仅取目标日原始记录；最近披露值另列源日期，不冒充当日。份额变化仅在净申赎估算条件已核验时提供；其他情况留空。'});
  }
  if(url.pathname==='/api/v1/history/etfs') {
    const q=(url.searchParams.get('q')||'').trim(),market=url.searchParams.get('market')||'';
    if(q.length>50||!['','SH','SZ'].includes(market)) return error(40001,'搜索参数非法');
    return ok(paging(catalog.items.filter(f=>(!market||f.market===market)&&(f.fund_code.includes(q)||f.fund_name.includes(q))),pg,manifest.target_end));
  }
  const m=/^\/api\/v1\/history\/etf\/([^/]+)(\/chart)?$/.exec(url.pathname);
  if(m) {
    if(!CODE.test(m[1])) return error(40001,'ETF 代码非法');
    const start=url.searchParams.get('start')||manifest.target_start,end=url.searchParams.get('end')||manifest.target_end;
    if(!dateOK(start)||!dateOK(end)||start>end||start<manifest.target_start||end>manifest.target_end) return error(40001,'日期超出可查询范围');
    const fund=catalog.items.find(x=>x.fund_code===m[1]); if(!fund) return error(40402,'ETF 不存在',404);
    const chunk=await read(fund.file),data=chunk?.funds?.[m[1]]; if(!Array.isArray(data)||data.length>8000) return error(50301,'历史分片未就绪',503);
    const rows=data.filter(r=>r[0]>=start&&r[0]<=end),meta=await read('meta');
    const items=rows.map(r=>({trade_date:r[0],shares:r[1],unit_nav:r[2],acc_nav:r[3],estimated_flow:r[4],quality:r[5]}));
    const shared={version:manifest.version,fund,start,end,coverage:{shares:hcoverage(rows,1),nav:hcoverage(rows,2),flow:hcoverage(rows,4)},notes:meta?.notes||[]};
    if(m[2]) return ok({...shared,total:rows.length,series:items.map(({trade_date,shares,unit_nav,estimated_flow,quality})=>({trade_date,shares,unit_nav,estimated_flow,quality}))});
    return ok({...paging(items,pg,manifest.target_end),...shared});
  }
  return error(40400,'接口不存在',404);
}
