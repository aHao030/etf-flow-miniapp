// Independent index and broad ETF snapshots, with bounded atomic publication.
const BKEY=/^(?:meta|calendar|index-(?:all|\d{6})|off-(?:all|\d{6})-\d{4})$/;
const BCOUNT=12000;
const bpath=(version,key)=>`broad/${version}/${key}.json`;
const bday=day=>dateOK(day)&&day>='1990-01-01';
const bcount=n=>Number.isSafeInteger(n)&&n>=0&&n<=10000;
function bshape(key,data){
  if(!object(data))return false;
  if(key==='meta')return VERSION.test(data.version||'')&&instantOK(data.generated_at)&&bday(data.target_start)&&bday(data.target_end)&&data.target_start<=data.target_end&&Array.isArray(data.indexes)&&data.indexes.length<=30&&data.indexes.every(x=>object(x)&&CODE.test(x.code||'')&&typeof x.name==='string'&&bcount(x.fund_count))&&new Set(data.indexes.map(x=>x.code)).size===data.indexes.length&&bcount(data.total_funds)&&data.mapping_complete===false;
  if(key==='calendar')return Array.isArray(data.dates)&&data.dates.length>0&&data.dates.length<=BCOUNT&&data.dates.every((d,i)=>bday(d)&&(!i||d>data.dates[i-1]));
  if(key.startsWith('off-'))return Array.isArray(data.reports)&&data.reports.length<=1000&&data.reports.every(r=>object(r)&&CODE.test(r.fund_code||'')&&dateOK(r.period_end)&&(r.period_start===null||dateOK(r.period_start))&&[r.subscription_shares,r.redemption_shares,r.net_subscription_shares,r.end_shares].every(finiteOrNull));
  if(key.startsWith('index-'))return object(data.index)&&data.index.code===key.slice(6)&&Array.isArray(data.members)&&data.members.length<=10000&&data.members.every(m=>object(m)&&CODE.test(m.fund_code||'')&&CODE.test(m.index_code||'')&&typeof m.fund_name==='string'&&[m.estimated_flow,m.shares,m.unit_nav].every(finiteOrNull))&&new Set(data.members.map(m=>m.fund_code)).size===data.members.length&&Array.isArray(data.rows)&&data.rows.length>0&&data.rows.length<=BCOUNT&&data.rows.every((r,i)=>Array.isArray(r)&&r.length===7&&bday(r[0])&&(!i||r[0]>data.rows[i-1][0])&&[r[1],r[2],r[6]].every(finiteOrNull)&&[r[3],r[4],r[5]].every(bcount)&&r[3]<=r[4]&&(r[3]===0?r[1]===null:r[1]!==null)&&(r[2]===null||r[3]===r[4]&&r[4]>0&&r[5]===0&&r[2]===r[1])&&(r[6]===null||r[6]>0));
  return false;
}
async function broadInternal(request,env,url){
  if(!env.UPDATE_SECRET||request.headers.get('authorization')!==`Bearer ${env.UPDATE_SECRET}`)return error(40101,'未授权',401);
  if(request.method==='PUT'){
    const m=/^\/internal\/broad\/([^/]+)\/([^/]+)\.json$/.exec(url.pathname);
    if(!m||!VERSION.test(m[1])||!BKEY.test(m[2]))return error(40001,'宽基文件名非法');
    if(await env.BUCKET.get(bpath(m[1],'committed')))return error(40901,'宽基版本已发布',409);
    let body;try{body=await bodyJSON(request);}catch{return error(40001,'宽基数据格式或大小非法');}
    if(!bshape(m[2],body.value))return error(40001,'宽基数据结构非法');
    const sha256=await digest(body.bytes);
    await env.BUCKET.put(bpath(m[1],m[2]),body.bytes,{httpMetadata:{contentType:'application/json'},customMetadata:{sha256}});
    return ok({version:m[1],key:m[2],sha256});
  }
  if(request.method==='POST'&&url.pathname==='/internal/broad/commit'){
    let b;try{b=(await bodyJSON(request)).value;}catch{return error(40001,'宽基清单格式非法');}
    if(!object(b)||!VERSION.test(b.version||'')||!Array.isArray(b.files)||b.files.length>500||new Set(b.files).size!==b.files.length||!b.files.every(k=>BKEY.test(k))||!['meta','calendar','index-all'].every(k=>b.files.includes(k))||!hashesValid(b.files,b.hashes)||!bday(b.target_start)||!bday(b.target_end)||!instantOK(b.generated_at))return error(40001,'宽基清单非法');
    if(await env.BUCKET.get(bpath(b.version,'committed')))return error(40901,'宽基版本已发布',409);
    for(const key of b.files){const head=await env.BUCKET.head(bpath(b.version,key));if(!head||head.size>MAX_BYTES||head.customMetadata?.sha256!==b.hashes[key])return error(40902,'宽基文件缺失或摘要不符',409);}
    const meta=await getJSON(env.BUCKET,bpath(b.version,'meta')),calendar=await getJSON(env.BUCKET,bpath(b.version,'calendar'));
    if(!bshape('meta',meta)||!bshape('calendar',calendar)||['version','target_start','target_end','generated_at'].some(k=>meta[k]!==b[k])||calendar.dates[0]!==b.target_start||calendar.dates.at(-1)!==b.target_end||b.files.filter(k=>!k.startsWith('off-')).length!==meta.indexes.length+3||!meta.indexes.every(x=>b.files.includes('index-'+x.code)))return error(40902,'宽基目录不一致',409);
    let allMembers=null;const mapped=new Map(),usedOff=new Set();
    for(const key of b.files.filter(k=>k.startsWith('index-'))){
      const data=await getJSON(env.BUCKET,bpath(b.version,key));
      if(!bshape(key,data)||data.rows.length!==calendar.dates.length||data.rows.some((r,i)=>r[0]!==calendar.dates[i]))return error(40902,'宽基日历不一致',409);
      if(data.off_exchange){
        const off=data.off_exchange;
        if(!Array.isArray(off.files)||!Array.isArray(off.members)||off.combined_total!==null||!Number.isSafeInteger(off.report_count)||off.report_count<0||off.report_count>250000)return error(40902,'场外目录非法',409);
        let count=0;const seen=new Set(),codes=new Set(off.members.map(x=>x.fund_code));
        for(const file of off.files){if(!file.startsWith('off-'+data.index.code+'-')||!b.files.includes(file)||usedOff.has(file))return error(40902,'场外分片引用非法',409);usedOff.add(file);const part=await getJSON(env.BUCKET,bpath(b.version,file));if(!bshape(file,part))return error(40902,'场外报告非法',409);for(const row of part.reports){const rk=row.fund_code+':'+row.period_end;if(!codes.has(row.fund_code)||seen.has(rk)||row.period_end>b.target_end)return error(40902,'场外报告归属或日期非法',409);seen.add(rk);}count+=part.reports.length;}
        if(count!==off.report_count)return error(40902,'场外报告数不一致',409);
      }
      if(key==='index-all'){allMembers=data.members.map(m=>m.fund_code).sort();continue;}
      const index=meta.indexes.find(x=>x.code===data.index.code);
      if(!index||index.fund_count!==data.members.length||data.members.some(m=>m.index_code!==index.code))return error(40902,'宽基成员不一致',409);
      for(const m of data.members){if(mapped.has(m.fund_code))return error(40902,'宽基成员重复',409);mapped.set(m.fund_code,index.code);}
    }
    if(!allMembers||allMembers.length!==meta.total_funds||JSON.stringify([...mapped.keys()].sort())!==JSON.stringify(allMembers))return error(40902,'宽基去重合计范围不一致',409);
    if(b.files.filter(k=>k.startsWith('off-')).length!==usedOff.size)return error(40902,'场外分片未引用',409);
    const old=await getJSON(env.BUCKET,'broad-latest.json');
    if(old&&(old.target_end>b.target_end||Date.parse(old.generated_at)>=Date.parse(b.generated_at)))return error(40903,'拒绝宽基版本回退',409);
    const latest={...b,published_at:new Date().toISOString()};
    await env.BUCKET.put(bpath(b.version,'committed'),JSON.stringify(latest));
    await env.BUCKET.put('broad-latest.json',JSON.stringify(latest));return ok(latest);
  }
  return error(40400,'接口不存在',404);
}
const broadPeriodCache=new Map(),broadPeriodFlights=new Map();let broadPeriodCacheBytes=0,broadPeriodTail=Promise.resolve();
async function broadPeriodMembers(env,meta,members,start,end,dates){
  const key=[meta.version,meta.history_version,start,end,members.map(m=>m.fund_code).join(',')].join(':');
  if(broadPeriodCache.has(key)){const saved=broadPeriodCache.get(key);broadPeriodCache.delete(key);broadPeriodCache.set(key,saved);return JSON.parse(saved.body);}
  if(broadPeriodFlights.has(key))return broadPeriodFlights.get(key);
  // Rapid period switches share duplicate work and process raw history serially across requests.
  const task=broadPeriodTail.then(async()=>{
    const result=await broadPeriodMembersUncached(env,meta,members,start,end,dates);
    if(result.members_period_status==='computed'){
      const body=JSON.stringify(result),bytes=new TextEncoder().encode(body).byteLength;
      if(bytes<=2*1024*1024){
        while(broadPeriodCache.size>=12||broadPeriodCacheBytes+bytes>2*1024*1024){const oldest=broadPeriodCache.keys().next().value;broadPeriodCacheBytes-=broadPeriodCache.get(oldest).bytes;broadPeriodCache.delete(oldest);}
        broadPeriodCache.set(key,{body,bytes});broadPeriodCacheBytes+=bytes;
      }
    }
    return result;
  });
  broadPeriodTail=task.then(()=>{},()=>{});broadPeriodFlights.set(key,task);
  try{return await task;}finally{broadPeriodFlights.delete(key);}
}
async function broadPeriodMembersUncached(env,meta,members,start,end,dates){
  const endDate=dates.at(-1)||null;
  const blank=m=>({...m,period_start:start,period_end:end,period_observed_flow:null,period_complete_flow:null,period_valid_days:0,period_expected_days:dateOK(m.listed_date)?dates.filter(d=>d>=m.listed_date&&(!dateOK(m.delisted_date)||d<m.delisted_date)).length:null,period_available_start:null,period_available_end:null,end_date:endDate,end_date_flow:null,period_status:'unavailable'});
  const empty=()=>({members:members.map(blank),members_period_status:'unavailable'});
  if(!VERSION.test(meta.history_version||'')||dates.length>BCOUNT||members.length>1000)return empty();
  try{
    const version=meta.history_version;
    const hmeta=await getJSON(env.BUCKET,hkey(version,'meta')),catalog=await getJSON(env.BUCKET,hkey(version,'catalog'));
    if(!hshape('meta',hmeta)||hmeta.version!==version||hmeta.target_end!==meta.target_end||!hshape('catalog',catalog))return empty();
    const catalogByCode=new Map(catalog.items.map(f=>[f.fund_code,f])),groups=new Map(),out=new Map(),openDates=new Set(dates);
    for(const member of members){const fund=catalogByCode.get(member.fund_code);if(!fund)return empty();if(!groups.has(fund.file))groups.set(fund.file,[]);groups.get(fund.file).push(member);}
    if(groups.size>100)return empty();
    // Process one bounded history shard at a time. Never keep all 45+ raw shards in memory.
    for(const [file,group] of groups){
      let chunk=await getJSON(env.BUCKET,hkey(version,file));
      for(const member of group){
        const rows=chunk?.funds?.[member.fund_code];
        if(!Array.isArray(rows)||rows.length>8000)return empty();
        const result=blank(member);let total=0,previous='';
        for(const row of rows){
          if(!Array.isArray(row)||row.length!==6||!dateOK(row[0])||row[0]<=previous||!finiteOrNull(row[4])||!HQUALITY.includes(row[5])||(row[5]==='estimated'?row[4]===null:row[4]!==null))return empty();
          previous=row[0];
          if(row[0]<start||row[0]>end||!openDates.has(row[0])||member.listed_date&&row[0]<member.listed_date||member.delisted_date&&row[0]>=member.delisted_date||row[5]!=='estimated')continue;
          total+=row[4];if(!Number.isFinite(total)||!Number.isFinite(total*100))return empty();result.period_valid_days++;result.period_available_start??=row[0];result.period_available_end=row[0];
          if(row[0]===endDate)result.end_date_flow=row[4];
        }
        result.period_observed_flow=result.period_valid_days?Math.round(total*100)/100:null;
        result.period_complete_flow=result.period_expected_days>0&&result.period_valid_days===result.period_expected_days?result.period_observed_flow:null;
        result.period_status=!result.period_valid_days?'unavailable':result.period_complete_flow===null?'partial':'ready';out.set(member.fund_code,result);
      }
      chunk=null;
    }
    return {members:members.map(m=>out.get(m.fund_code)),members_period_status:'computed'};
  }catch{return empty();}
}
async function broadApi(request,env,url){
  if(request.method!=='GET')return error(40500,'方法不支持',405);
  const manifest=await getJSON(env.BUCKET,'broad-latest.json');
  if(!manifest||!VERSION.test(manifest.version||'')||!Array.isArray(manifest.files))return error(50301,'指数与宽基数据正在准备',503);
  const read=k=>manifest.files.includes(k)?getJSON(env.BUCKET,bpath(manifest.version,k)):null;
  const meta=await read('meta');if(!meta)return error(50301,'指数目录未就绪',503);
  if(url.pathname==='/api/v1/broad/meta')return ok(meta);
  if(url.pathname==='/api/v1/broad/indexes')return ok({version:manifest.version,items:meta.indexes});
  const from=url.searchParams.get('start')||manifest.target_start,to=url.searchParams.get('end')||manifest.target_end;
  if(!dateOK(from)||!dateOK(to)||from>to)return error(40001,'日期范围非法');
  const start=from<manifest.target_start?manifest.target_start:from,end=to>manifest.target_end?manifest.target_end:to;
  const calendar=await read('calendar');if(!calendar)return error(50301,'交易日历未就绪',503);
  const dates=calendar.dates.filter(d=>d>=start&&d<=end);
  if(url.pathname==='/api/v1/broad/calendar')return ok({version:manifest.version,start,end,dates,source:calendar.source});
  const m=/^\/api\/v1\/broad\/(index|off)\/(all|\d{6})$/.exec(url.pathname);
  if(m){
    const data=await read('index-'+m[2]);if(!data)return error(40402,'指数不存在',404);
    const off=data.off_exchange||{members:[],report_count:0,files:[],combined_total:null,combined_reason:'frequency_or_double_count',notes:['场外披露记录尚未就绪。']};
    const pu=new URL(url);pu.searchParams.set('page',url.searchParams.get(m[1]==='off'?'page':'off_page')||'1');pu.searchParams.set('page_size',url.searchParams.get(m[1]==='off'?'page_size':'off_page_size')||'20');
    const pg=paginate(pu);if(!pg)return error(40001,'场外分页参数非法');
    const reports=[],offset=(pg.page-1)*pg.page_size;let total=0;
    for(const file of off.files){const range=off.file_ranges?.[file];if(range&&(range.end<start||range.start>end))continue;const part=await read(file);if(!part)return error(50301,'场外报告未就绪',503);for(const row of part.reports){if(row.period_end<start||row.period_end>end)continue;if(total>=offset&&reports.length<pg.page_size)reports.push(row);total++;}}
    const {files,file_ranges,...offPublic}=off;
    const offResult={...offPublic,...pg,total,has_more:offset+reports.length<total,reports};
    if(m[1]==='off')return ok({version:manifest.version,start,end,...offResult});
    const periodMembers=await broadPeriodMembers(env,meta,data.members,start,end,dates);
    return ok({version:manifest.version,history_version:meta.history_version||null,index:data.index,...periodMembers,members_asof:manifest.target_end,start,end,trading_dates:dates,
      off_exchange:offResult,
      series:data.rows.filter(r=>r[0]>=start&&r[0]<=end).map(r=>({trade_date:r[0],observed_flow:r[1],estimated_flow:r[2],valid_funds:r[3],expected_funds:r[4],unknown_lifecycle_funds:r[5],index_close:r[6]})),notes:meta.notes});
  }
  return error(40400,'接口不存在',404);
}
