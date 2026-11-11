'use strict';
const inflate=require('./vendor/tiny-inflate');
const meta=require('./snapshot/meta');
const blocks={
  indices:()=>require('./snapshot/indices'),funds:()=>require('./snapshot/funds'),off:()=>require('./snapshot/off'),
  calendar:()=>require('./snapshot/calendar'),s300750:()=>require('./snapshot/s300750'),s600519:()=>require('./snapshot/s600519')
};
const decoded=Object.create(null);
const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
function decode(block){
  const bytes=new Uint8Array(Math.floor(block[1].length*3/4));
  let seek=0, bits=0, buffer=0;
  for(let i=0;i<block[1].length;i++){
    const code=block[1][i];
    if(code==='=')break;
    const v=alphabet.indexOf(code);
    if(v<0)throw Error('离线包编码无效');
    buffer=(buffer<<6)|v;bits+=6;
    if(bits>=8){bits-=8;bytes[seek++]=(buffer>>bits)&255;}
  }
  if(!Number.isInteger(block[0])||block[0]<0||block[0]>16*1024*1024)throw Error('离线数据块过大');
  const output=inflate(bytes.subarray(0,seek),new Uint8Array(block[0]));
  if(output.length!==block[0])throw Error('离线数据长度不符');
  // JSON originated as UTF-8. Native JS runtimes do not all provide TextDecoder.
  const pieces=[];let chars=[];
  for(let j=0;j<output.length;){
    const b=output[j++];let cp=b;
    if(b>=240)cp=((b&7)<<18)|((output[j++]&63)<<12)|((output[j++]&63)<<6)|(output[j++]&63);
    else if(b>=224)cp=((b&15)<<12)|((output[j++]&63)<<6)|(output[j++]&63);
    else if(b>=192)cp=((b&31)<<6)|(output[j++]&63);
    if(cp>65535){cp-=65536;chars.push(55296+(cp>>10),56320+(cp&1023));}else chars.push(cp);
    if(chars.length>=8192){pieces.push(String.fromCharCode.apply(null,chars));chars=[];}
  }
  pieces.push(String.fromCharCode.apply(null,chars));return JSON.parse(pieces.join(''));
}
function read(key){if(!decoded[key])decoded[key]=decode(blocks[key]());return decoded[key];}
function page(rows,params){
  const size=Number(params.page_size||20),n=Number(params.page||1);
  if(!Number.isInteger(size)||size<1||size>100||!Number.isInteger(n)||n<1)throw Error('页码无效');
  return {items:rows.slice((n-1)*size,n*size),page:n,page_size:size,total:rows.length,has_more:n*size<rows.length};
}
function ranges(params,min,max){
  const start=params.start||min,end=params.end||max;
  const isDate=s=>/^\d{4}-\d{2}-\d{2}$/.test(s)&&Number.isFinite(Date.parse(s+'T00:00:00Z'))&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;
  if(!isDate(start)||!isDate(end)||start<min||end>max||start>end)throw Error('本包可查日期为 '+min+' 至 '+max);
  return {start,end};
}
function reportPage(index,start,end,params){
  const codes=new Set(index.offCodes);
  const selected=read('off').filter(r=>codes.has(r.fund_code)&&r.period_end>=start&&r.period_end<=end);
  const result=page(selected,params);
  return {page:result.page,page_size:result.page_size,total:result.total,has_more:result.has_more,reports:result.items,
    members:[],combined_total:null,notes:meta.offNotes};
}
function member(f,start,end,dates,open){
  const [fund_code,fund_name,listed_date,delisted_date,index_code]=f;
  const source=read('funds')[fund_code], active=d=>(!listed_date||d>=listed_date)&&(!delisted_date||d<delisted_date);
  const rows=(source?source.rows:[]).filter(r=>open.has(r[0])&&active(r[0])&&r[5]==='estimated'&&Number.isFinite(r[4]));
  const value=rows.length?Math.round(rows.reduce((s,r)=>s+r[4],0)*100)/100:null;
  const expected=listed_date?dates.filter(active).length:null,endDate=dates[dates.length-1]||null,last=rows[rows.length-1];
  return {fund_code,fund_name,listed_date,delisted_date,index_code,period_start:start,period_end:end,
    period_observed_flow:value,period_complete_flow:expected>0&&rows.length===expected?value:null,
    period_valid_days:rows.length,period_expected_days:expected,end_date:endDate,end_date_flow:last&&last[0]===endDate?last[4]:null};
}
function query(route,params={}){
  if(route==='broad/meta')return meta.broad;
  if(route==='market/meta')return meta.market;
  if(route==='history/meta')return meta.history;
  if(route==='market/stocks'){
    const q=String(params.q||'').trim();
    return page(meta.stocks.filter(s=>s.stock_code.includes(q)||s.stock_name.includes(q)),params);
  }
  const stock=/^(market|prices)\/stock\/(\d{6})$/.exec(route);
  if(stock){
    const code=stock[2], info=meta.stocks.find(s=>s.stock_code===code);
    if(!info)throw Error('离线包预置宁德时代和贵州茅台，完整数据源可查询其他股票。');
    const src=read('s'+code);
    if(stock[1]==='prices'){
      const {start,end}=ranges(params,'1990-01-01',meta.end);
      const trading_dates=read('calendar').filter(d=>d>=start&&d>=info.stock_listed_date&&d<=end);
      const series=src.prices.filter(r=>r[0]>=start&&r[0]<=end).map(r=>({trade_date:r[0],close:r[1]}));
      const excluded=src.excluded.filter(d=>d>=start&&d<=end).length;
      return {...info,start,end,series,trading_dates,price_unit:'元/股',adjustment:'none',provider:'eastmoney',
        source_label:'东方财富不复权日K',source_calendar_excluded_count:excluded,status:!series.length?'unavailable':series.length===trading_dates.length&&!excluded?'ready':'partial'};
    }
    const detail=src.details[params.period||'1d'];if(!detail)throw Error('此周期暂不可用');
    const m=detail.movement;
    const selected=src.series.filter(r=>(m.start_exclusive?r[0]>m.window_start:r[0]>=m.window_start)&&r[0]<=m.window_end);
    const entries=detail.etf_contributions.map(([code,flow,valid_days,status])=>({...src.funds[code],flow,valid_days,status}));
    return {...detail,movement:{...m,trading_dates:selected.map(r=>r[0])},series:selected.map(r=>({trade_date:r[0],flow:r[1],valid_etfs:r[2],expected_etfs:r[3]})),etf_contributions:entries};
  }
  const {start,end}=ranges(params,meta.start,meta.end);
  const dates=read('calendar').filter(d=>d>=start&&d<=end);
  if(route==='broad/calendar')return {version:meta.broad.version,start,end,dates};
  const broad=/^broad\/(index|off)\/(all|\d{6})$/.exec(route);
  if(broad){
    const idx=read('indices')[broad[2]];if(!idx)throw Error('未收录该指数');
    if(broad[1]==='off')return {version:meta.broad.version,start,end,...reportPage(idx,start,end,params)};
    const open=new Set(dates);
    const series=idx.rows.filter(r=>open.has(r[0])).map(r=>({trade_date:r[0],observed_flow:r[1],estimated_flow:r[2],valid_funds:r[3],expected_funds:r[4],unknown_lifecycle_funds:r[5],index_close:r[6]}));
    return {version:meta.broad.version,history_version:meta.history.version,index:idx.index,start,end,trading_dates:dates,series,
      members:idx.members.map(f=>member(f,start,end,dates,open)),off_exchange:reportPage(idx,start,end,{page:params.off_page,page_size:params.off_page_size})};
  }
  const fund=/^history\/etf\/(\d{6})(\/chart)?$/.exec(route);
  if(fund){
    const f=read('funds')[fund[1]];if(!f)throw Error('此基金未收录在当前离线包中。');
    const items=f.rows.filter(r=>r[0]>=start&&r[0]<=end).map(r=>({trade_date:r[0],shares:r[1],unit_nav:r[2],acc_nav:r[3],estimated_flow:r[4],quality:r[5]}));
    const info={version:meta.history.version,start,end,fund:{fund_code:fund[1],fund_name:f.name,listed_date:f.listed,delisted_date:f.delisted},total:items.length};
    return fund[2]?{...info,series:items}:{...info,...page(items,params)};
  }
  throw Error('未知数据请求');
}
module.exports={query,meta};
