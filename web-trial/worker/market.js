// Factorized disclosed-holdings model. Each ETF factor shard is read once per request.
const MKEY=/^(?:meta|calendar|catalog|funds|ranks|industries|weights-\d{4}|factors-\d{4})$/;
const MREQUIRED=['meta','calendar','catalog','funds','ranks','industries'];
const mpath=(v,k)=>`market/${v}/${k}.json`;
function mshape(key,d){
  if(!object(d))return false;
  if(key==='meta')return VERSION.test(d.version||'')&&instantOK(d.generated_at)&&bday(d.target_start)&&bday(d.target_end)&&d.target_start<=d.target_end&&bcount(d.total_funds);
  if(key==='calendar')return bshape('calendar',d);
  if(key==='catalog')return Array.isArray(d.items)&&d.items.length<=10000&&d.items.every(s=>object(s)&&CODE.test(s.stock_code||'')&&(s.stock_listed_date===undefined||s.stock_listed_date===null||dateOK(s.stock_listed_date))&&(s.listing_status===undefined||['verified','unknown'].includes(s.listing_status))&&(s.file===null||/^weights-\d{4}$/.test(s.file)))&&new Set(d.items.map(s=>s.stock_code)).size===d.items.length;
  if(key==='funds')return Array.isArray(d.items)&&d.items.length<=10000&&d.items.every(f=>object(f)&&CODE.test(f.fund_code||'')&&(f.factor_file===null||/^factors-\d{4}$/.test(f.factor_file))&&(!f.listed_date||dateOK(f.listed_date))&&(!f.delisted_date||dateOK(f.delisted_date)))&&new Set(d.items.map(f=>f.fund_code)).size===d.items.length;
  if(key==='ranks')return RANK_PERIODS.every(p=>Array.isArray(d[p])&&d[p].length<=10000&&d[p].every(r=>CODE.test(r.stock_code||'')&&Number.isFinite(r.flow)));
  if(key==='industries')return Array.isArray(d.items)&&d.items.every(object);
  if(key.startsWith('weights-'))return Object.entries(d).length<=10000&&Object.entries(d).every(([s,rows])=>CODE.test(s)&&Array.isArray(rows)&&rows.length<=10000&&rows.every(r=>Array.isArray(r)&&r.length===2&&CODE.test(r[0])&&Number.isFinite(r[1])&&r[1]>0&&r[1]<=1)&&new Set(rows.map(r=>r[0])).size===rows.length);
  if(key.startsWith('factors-'))return Object.entries(d).length<=10000&&Object.entries(d).every(([f,rows])=>CODE.test(f)&&Array.isArray(rows)&&rows.length<=8000&&rows.every((r,i)=>Array.isArray(r)&&r.length===2&&bday(r[0])&&Number.isFinite(r[1])&&(!i||r[0]>rows[i-1][0])));
  return false;
}
async function marketInternal(request,env,url){
  if(!env.UPDATE_SECRET||request.headers.get('authorization')!==`Bearer ${env.UPDATE_SECRET}`)return error(40101,'未授权',401);
  if(request.method==='PUT'){
    const m=/^\/internal\/market\/([^/]+)\/([^/]+)\.json$/.exec(url.pathname);
    if(!m||!VERSION.test(m[1])||!MKEY.test(m[2]))return error(40001,'全市场文件名非法');
    if(await env.BUCKET.get(mpath(m[1],'committed')))return error(40901,'全市场版本已发布',409);
    let b;try{b=await bodyJSON(request);}catch{return error(40001,'全市场数据格式或大小非法');}
    if(!mshape(m[2],b.value))return error(40001,'全市场数据结构非法');const sha256=await digest(b.bytes);
    await env.BUCKET.put(mpath(m[1],m[2]),b.bytes,{httpMetadata:{contentType:'application/json'},customMetadata:{sha256}});return ok({sha256,version:m[1],key:m[2]});
  }
  if(request.method==='POST'&&url.pathname==='/internal/market/commit'){
    let b;try{b=(await bodyJSON(request)).value;}catch{return error(40001,'清单非法');}
    if(!object(b)||!VERSION.test(b.version||'')||!Array.isArray(b.files)||b.files.length>500||new Set(b.files).size!==b.files.length||!b.files.every(k=>MKEY.test(k))||!MREQUIRED.every(k=>b.files.includes(k))||!hashesValid(b.files,b.hashes)||!bday(b.target_start)||!bday(b.target_end)||!instantOK(b.generated_at))return error(40001,'全市场清单非法');
    if(await env.BUCKET.get(mpath(b.version,'committed')))return error(40901,'版本已发布',409);
    const loaded={},weightRefs=new Map(),factorRefs=new Map(),references=new Set();
    for(const k of b.files){const head=await env.BUCKET.head(mpath(b.version,k));if(!head||head.size>MAX_BYTES||head.customMetadata?.sha256!==b.hashes[k])return error(40902,'全市场摘要不符',409);const d=await getJSON(env.BUCKET,mpath(b.version,k));if(!mshape(k,d))return error(40902,'全市场文件非法',409);
      if(MREQUIRED.includes(k))loaded[k]=d;
      else if(k.startsWith('weights-'))for(const [s,rows] of Object.entries(d)){if(weightRefs.has(s))return error(40902,'股票权重重复',409);weightRefs.set(s,k);rows.forEach(r=>references.add(r[0]));}
      else for(const f of Object.keys(d)){if(factorRefs.has(f)||d[f].some(r=>r[0]<b.target_start||r[0]>b.target_end))return error(40902,'基金因素重复或日期越界',409);factorRefs.set(f,k);}
    }
    const meta=loaded.meta,funds=new Map(loaded.funds.items.map(f=>[f.fund_code,f]));
    if(['version','generated_at','target_start','target_end'].some(k=>meta[k]!==b[k])||loaded.calendar.dates[0]<b.target_start||loaded.calendar.dates.at(-1)!==b.target_end||meta.total_funds!==funds.size||!loaded.catalog.items.every(s=>(s.file||null)===(weightRefs.get(s.stock_code)||null))||weightRefs.size!==loaded.catalog.items.filter(s=>s.file).length||!loaded.funds.items.every(f=>(f.factor_file||null)===(factorRefs.get(f.fund_code)||null))||factorRefs.size!==loaded.funds.items.filter(f=>f.factor_file).length||[...references].some(f=>!funds.has(f)||!factorRefs.has(f)))return error(40902,'全市场跨分片引用不一致',409);
    const old=await getJSON(env.BUCKET,'market-latest.json');if(old&&(old.target_end>b.target_end||Date.parse(old.generated_at)>=Date.parse(b.generated_at)))return error(40903,'拒绝全市场版本回退',409);
    const latest={...b,published_at:new Date().toISOString()};await env.BUCKET.put(mpath(b.version,'committed'),JSON.stringify(latest));await env.BUCKET.put('market-latest.json',JSON.stringify(latest));return ok(latest);
  }return error(40400,'接口不存在',404);
}
async function marketApi(request,env,url){
  if(request.method!=='GET')return error(40500,'方法不支持',405);
  const manifest=await getJSON(env.BUCKET,'market-latest.json');if(!manifest||!VERSION.test(manifest.version||'')||!Array.isArray(manifest.files))return error(50301,'全市场披露持仓数据正在准备',503);
  const read=k=>manifest.files.includes(k)?getJSON(env.BUCKET,mpath(manifest.version,k)):null,td=manifest.target_end,p=url.pathname.replace('/api/v1/market',''),pg=paginate(url);
  if(!pg)return error(40001,'分页参数非法');
  if(p==='/meta'||p==='/industries')return ok(await read(p.slice(1)));
  if(p==='/stocks'){const q=(url.searchParams.get('q')||'').trim();if(q.length>50)return error(40001,'搜索参数非法');const d=await read('catalog');return ok(paging(d.items.filter(s=>s.stock_code.includes(q)||(s.stock_name||'').includes(q)),pg,td));}
  if(p==='/etfs'){const d=await read('funds');return ok(paging(d.items,pg,td));}
  if(p==='/rank'){const period=url.searchParams.get('period')||'1d',direction=url.searchParams.get('direction')||'in',industry=url.searchParams.get('industry')||'';if(!RANK_PERIODS.includes(period)||!['in','out'].includes(direction))return error(40001,'排行参数非法');const ranks=await read('ranks'),catalog=await read('catalog'),byCode=new Map(catalog.items.map(s=>[s.stock_code,s])),rows=ranks[period].map(r=>({...byCode.get(r.stock_code),...r})).filter(r=>(direction==='in'?r.flow>0:r.flow<0)&&(!industry||r.industry_code===industry)).sort((a,b)=>(direction==='in'?b.flow-a.flow:a.flow-b.flow)||a.stock_code.localeCompare(b.stock_code)).map((r,i)=>({...r,rank:i+1}));return ok(paging(rows,pg,td,{period,direction}));}
  const etf=/^\/etf\/(\d{6})$/.exec(p);if(etf){const f=(await read('funds')).items.find(f=>f.fund_code===etf[1]);return f?ok({...f,etf_name:f.fund_name,trade_date:td,weight_date:f.report_date,weight_source:'quarterly',est_flow:null,note:'仅采用已披露持仓，未披露部分不能推断为零。'}):error(40402,'基金不存在',404);}
  const stock=/^\/stock\/(\d{6})$/.exec(p);
  if(stock){
    const period=url.searchParams.get('period')||'1d';if(![...STOCK_PERIODS,'all'].includes(period))return error(40001,'周期非法');
    const catalog=await read('catalog'),s=catalog.items.find(s=>s.stock_code===stock[1]);if(!s)return error(40401,'股票不存在',404);
    const calendar=(await read('calendar')).dates,allFunds=(await read('funds')).items,funds=new Map(allFunds.map(f=>[f.fund_code,f]));
    let exclusive=LONG_PERIODS.includes(period);let start=period==='all'?manifest.target_start:exclusive?stockExpectedAnchor(period,td):calendar[Math.max(0,calendar.length-({'1d':1,'5d':5,'20d':20}[period]))];
    const requestedStart=start;if(s.stock_listed_date&&s.stock_listed_date>start){start=s.stock_listed_date>td?td:s.stock_listed_date;exclusive=false;}const dates=calendar.filter(d=>(exclusive?d>start:d>=start)&&d<=td&&(!s.stock_listed_date||d>=s.stock_listed_date)),sums=new Map(dates.map(d=>[d,{flow:0,valid:0,expected:0}]));
    const weights=s.file?(await read(s.file))[s.stock_code]:[],groups=new Map(),contributions=[];
    for(const [code,weight] of weights){const f=funds.get(code);if(!groups.has(f.factor_file))groups.set(f.factor_file,[]);groups.get(f.factor_file).push({f,weight});for(const day of dates)if((!f.listed_date||day>=f.listed_date)&&(!f.delisted_date||day<f.delisted_date))sums.get(day).expected++;}
    for(const [file,members] of groups){const chunk=await read(file);for(const {f,weight} of members){let total=0,valid=0;for(const [day,flow] of chunk[f.fund_code]){const row=sums.get(day);if(!row||f.listed_date&&day<f.listed_date||f.delisted_date&&day>=f.delisted_date)continue;const v=flow*weight;row.flow+=v;row.valid++;total+=v;valid++;}contributions.push({etf_code:f.fund_code,etf_name:f.fund_name,flow:valid?total:null,weight,weight_date:f.report_date,weight_source:'quarterly',status:valid?'disclosed_partial':'unavailable',valid_days:valid,holdings_coverage_pct:f.coverage_pct});}}
    const series=dates.map(day=>{const r=sums.get(day);return{trade_date:day,flow:r.valid?r.flow:null,intensity_bp:null,valid_etfs:r.valid,expected_etfs:r.expected};}),valid=series.filter(r=>r.flow!==null),observed=valid.length?valid.reduce((a,r)=>a+r.flow,0):null;
    contributions.sort((a,b)=>Math.abs(b.flow||0)-Math.abs(a.flow||0));
    return ok({...s,trade_date:td,series,etf_contributions:contributions,top_etfs:contributions.slice(0,10),data_status:weights.length?'disclosed_partial':'no_data',quality:{flags:['已披露持仓小计','所选披露报告权重回溯'],note:'全目录已列出，但未披露A股持仓和未核验基金无法分配至具体股票。'},history_basis:'selected_disclosed_report_weights',history_note:'按所选披露报告权重（各基金实际报告期见明细）计算的回溯情景，并非历史时点持仓或真实资金。'+(s.stock_listed_date?'已按交易所上市日 '+s.stock_listed_date+' 剔除上市前日期。':'该股上市边界未核验，早期情景可能早于上市。')+'当前小计不含未披露和未核验部分。',coverage:{tracked_etfs:weights.length,active_etfs:contributions.filter(r=>r.valid_days).length,pending_etfs:contributions.filter(r=>!r.valid_days).length,note:'与该股关联的已披露持仓 ETF；未披露关联关系未知。'},movement:{period,requested_window_start:requestedStart,window_start:start,window_end:td,start_exclusive:exclusive,calendar_status:'verified',window_days:dates.length,trading_dates:dates,net_flow:null,inflow:null,outflow:null,observed_net_flow:observed,observed_inflow:observed===null?null:contributions.reduce((a,r)=>a+(r.flow>0?r.flow:0),0),observed_outflow:observed===null?null:contributions.reduce((a,r)=>a+(r.flow<0?r.flow:0),0),available_start:valid[0]?.trade_date||null,available_end:valid.at(-1)?.trade_date||null,valid_days:valid.length,missing_days:dates.length-valid.length,complete:false,etf_count:weights.length,inflow_etf_count:contributions.filter(r=>r.flow>0).length,outflow_etf_count:contributions.filter(r=>r.flow<0).length}});
  }
  return error(40400,'接口不存在',404);
}
