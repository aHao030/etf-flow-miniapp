const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');

// Use the real Page definition with deterministic platform, API and formatting boundaries.
const source = readFileSync(join(__dirname,'../src/pages/broad/index.js'),'utf8');
const tap = (key,value) => ({currentTarget:{dataset:{[key]:value}}});
const pick = value => ({detail:{value}});
const dateEvent = (field,value) => ({...tap('field',field),detail:{value}});
const meta = {version:'b1',history_version:'h2',target_start:'2020-01-01',target_end:'2026-11-10',
  indexes:[{code:'000300',name:'沪深300'},{code:'000001',name:'上证综指'}]};
const series = [
  ['2026-11-03',10000,null,1,100],
  ['2026-11-06',0,0,2,110],
  ['2026-11-09',-2500,-2500,2,120],
  ['2026-11-10',null,null,0,null]
].map(([trade_date,observed_flow,estimated_flow,valid_funds,index_close])=>({
  trade_date,observed_flow,estimated_flow,valid_funds,index_close,expected_funds:2,unknown_lifecycle_funds:0
}));
function offPage(page=1) {
  return {members:[],total:11,page,page_size:10,reports:Array.from({length:page===1?10:1},(_,i)=>({
    fund_code:String(100000+(page-1)*10+i),period_start:'2026-07-01',period_end:'2026-09-30',subscription_shares:100,
    redemption_shares:null,net_subscription_shares:null,end_shares:400
  }))};
}
function fixture(path,params={}) {
  if(path==='broad/meta')return meta;
  const {start,end}=params, code=path.split('/').pop();
  if(path.startsWith('broad/off/'))return {...offPage(params.page),version:'b1',start,end};
  const rows=series.filter(row=>row.trade_date>=start && row.trade_date<=end);
  const members=Array.from({length:12},(_,i)=>({fund_code:String(510300+i),fund_name:'测试基金'+i,period_start:start,period_end:end,
    period_observed_flow:i===0?null:(i-5)*1000,estimated_flow:999999,period_valid_days:1,period_expected_days:2}));
  return {version:'b1',history_version:'h2',index:{code,name:'沪深300',benchmark_name:'沪深300'},start,end,
    members,series:rows,trading_dates:rows.map(row=>row.trade_date),off_exchange:offPage()};
}
function createPage() {
  const calls=[],navs=[],server={handler:fixture};let definition;
  const libs={
    '../../lib/data':{sourceInfo:()=>({mode:server.mode||'snapshot'}),get:async(path,params)=>{calls.push({path,params});return server.handler(path,params);}},
    '../../lib/platform':{nav:url=>navs.push(url)},
    '../../lib/format':{
      money:(value,unit)=>Number.isFinite(value)?value+' '+unit:'—',
      number:(value,places)=>Number.isFinite(value)?value.toFixed(places):'—',
      periodStart:(end,months,min)=>{const d=new Date(end+'T00:00:00Z');d.setUTCMonth(d.getUTCMonth()-months);const out=d.toISOString().slice(0,10);return out<min?min:out;}
    },
    '../../lib/chart':{chartModel:(rows,opts)=>({rows,opts})}
  };
  runInNewContext(source,{Page:page=>{definition=page;},require:id=>libs[id]});
  const page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(update){Object.assign(this.data,update);}};
  return {page,calls,navs,server};
}
function deferred() {let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}

test('real trading days, true zero and unavailable totals survive into the chart, daily view and benchmark',async()=>{
  const {page,calls}=createPage();await page.onLoad();
  assert.equal(calls[1].path,'broad/index/000300');assert.equal(calls[1].params.start,'2026-08-10');
  assert.equal(page.data.summary.amount,'7500 auto');assert.equal(page.data.summary.direction,'净流入');
  assert.equal(page.data.summary.recent,'2026-11-09');assert.equal(page.data.summary.coverage,'3 / 4 个交易日有值');
  assert.equal(page.data.summary.complete,'资料不足');assert.match(page.data.summary.ending,/2026-11-10.*当日暂无估算/);
  assert.equal(page.data.chart.rows[1].value,0);assert.equal(page.data.chart.rows[3].value,null);
  assert.equal(page.data.chart.opts.currency,true);assert.equal(page.data.chart.opts.overlayUnit,'点');
  assert.equal(page.data.quote.change,'+20.00%');assert.equal(page.data.quote.span,'2026-11-03 至 2026-11-09');
  assert.equal(page.data.dailyRows[0].amount,'—');assert.equal(page.data.dailyRows[2].amount,'0 auto');
  page.changeDate(dateEvent('start','2026-11-06'));page.changeDate(dateEvent('end','2026-11-06'));
  assert.match(page.data.dateNotice,/日期待应用/);assert.equal(calls.length,2);
  await page.applyDates();assert.equal(page.data.summary.amount,'0 auto');assert.equal(page.data.summary.complete,'0 auto');
  assert.equal(page.data.quote.change,'');assert.match(page.data.quote.span,/仅有一个/);
  page.changeDate(dateEvent('start','2026-11-07'));page.changeDate(dateEvent('end','2026-11-08'));
  await page.applyDates();assert.equal(page.data.summary.complete,'无交易日');assert.equal(page.data.summary.amount,'—');
});

test('offline presets respect the package boundary and never repeat the same range',async()=>{
  const {page,server,calls}=createPage();
  let offlineMeta={...meta,offline:true,target_start:'2026-08-10'};
  server.handler=(path,params)=>path==='broad/meta'?offlineMeta:fixture(path,params);
  await page.onLoad();assert.equal(page.data.periods.map(item=>item.id).join(','),'1,3');
  assert.equal(page.data.start,offlineMeta.target_start);
  const before=calls.length;await page.changePeriod(tap('id','60'));assert.equal(calls.length,before);
  offlineMeta={...offlineMeta,target_start:'2026-11-01'};await page.refresh();
  assert.equal(page.data.periods.map(item=>item.id).join(','),'1');assert.equal(page.data.start,'2026-11-01');
  server.handler=fixture;await page.refresh();assert.equal(page.data.periods.length,5);
});

test('foreground refresh follows the new close for presets while keeping an unapplied date edit',async()=>{
  const {page,server,calls}=createPage();
  let current={...meta};
  server.mode='live';
  server.handler=(path,params)=>path==='broad/meta'?current:{...fixture(path,params),version:current.version,history_version:current.history_version};
  await page.onLoad();await page.onShow();assert.equal(calls.length,2);
  page.changeDate(dateEvent('start','2026-11-05'));
  current={...current,version:'b2',history_version:'h3',target_end:'2026-11-11'};
  page.onHide();await page.onShow();
  assert.equal(page.data.summary.end,'2026-11-11');assert.equal(page.data.summary.start,'2026-08-11');
  assert.equal(page.data.start,'2026-11-05');assert.equal(page.data.end,'2026-11-10');
  assert.match(page.data.dateNotice,/日期待应用.*2026-11-11/);
  await page.applyDates();assert.equal(page.data.summary.start,'2026-11-05');assert.equal(page.data.summary.end,'2026-11-10');
  server.mode='snapshot';const before=calls.length;page.onHide();await page.onShow();assert.equal(calls.length,before);
});

test('member search, local pagination, unit switching and scoped fund navigation',async()=>{
  const {page,calls,navs}=createPage();await page.onLoad();
  page.changeSection(tap('id','members'));assert.equal(page.data.memberRows.length,10);
  page.memberPage(tap('step',1));assert.equal(page.data.memberRows.length,2);
  assert.equal(page.data.memberRows[1].code,'510300');assert.equal(page.data.memberRows[1].amount,'—');
  page.searchMembers(pick('510311'));assert.equal(page.data.memberTotal,1);assert.equal(page.data.memberPage,1);
  page.changeUnit(pick('2'));assert.equal(page.data.memberRows[0].amount,'6000 yi');assert.equal(page.data.chart.opts.moneyUnit,'yi');
  page.changeDate(dateEvent('start','2026-11-06'));
  page.openFund(tap('code','510311'));const outgoing=new URL(navs[0],'https://local.test');
  assert.equal(outgoing.pathname,'/pages/fund/index');assert.equal(outgoing.searchParams.get('start'),'2026-08-10');
  assert.equal(outgoing.searchParams.get('name'),'测试基金11');assert.equal(outgoing.searchParams.get('history_version'),'h2');
  assert.equal(outgoing.searchParams.get('unit'),'yi');assert.equal(calls.length,2);
  await page.changeIndex(pick('0'));assert.equal(calls.at(-1).params.start,'2026-11-06');assert.equal(page.data.period,'');
  assert.match(page.data.quote.name,/参考基准/);assert.equal(page.data.dateNotice,'');
});

test('late ranges and report pages cannot overwrite a later query or restore invalid input',async()=>{
  const {page,calls,server}=createPage();await page.onLoad();
  const oldRange=deferred(),oldOff=deferred();
  server.handler=(path,params)=>path.includes('/off/')?oldOff.promise:path.includes('/index/')&&params.start==='2026-10-10'?oldRange.promise:fixture(path,params);
  const off=page.reportPage(tap('step',1)), old=page.changePeriod(tap('id','1'));
  await page.changePeriod(tap('id','12'));
  assert.equal(page.data.summary.start,'2025-11-10');
  oldOff.resolve(fixture('broad/off/000300',{start:'2026-08-10',end:'2026-11-10',page:2}));
  oldRange.resolve(fixture('broad/index/000300',{start:'2026-10-10',end:'2026-11-10'}));
  await Promise.all([old,off]);assert.equal(page.data.summary.start,'2025-11-10');assert.equal(page.data.offPage,1);
  page.changeDate(dateEvent('start','2026-11-11'));const before=calls.length;
  await page.changeIndex(pick('0'));assert.equal(calls.length,before);assert.equal(page.data.ready,false);assert.equal(page.data.chart,null);
  assert.equal(page.data.memberRows.length,0);assert.equal(page.data.offRows.length,0);assert.match(page.data.error,/开始日期/);
  server.handler=fixture;await page.changePeriod(tap('id','3'));assert.equal(page.data.ready,true);assert.equal(page.data.code,'all');
  assert.equal(page.data.start,'2026-08-10');assert.equal(page.data.offError,'');
});

test('off-exchange pages retain missing shares and version failures leave on-exchange cash unchanged',async()=>{
  const {page,server}=createPage();await page.onLoad();const amount=page.data.summary.amount;
  await page.reportPage(tap('step',1));assert.equal(page.data.offPage,2);assert.equal(page.data.offRows.length,1);
  assert.equal(page.data.offRows[0].sold,'—');assert.equal(page.data.offRows[0].bought,'100.00 份');
  server.handler=(path,params)=>({...fixture(path,params),version:'old'});
  await page.reportPage(tap('step',-1));assert.equal(page.data.offRows.length,0);assert.match(page.data.offError,/快照/);
  assert.equal(page.data.summary.amount,amount);assert.equal(page.data.ready,true);
  server.handler=fixture;await page.retryReports();assert.equal(page.data.offPage,1);
  server.handler=(path,params)=>path.includes('/index/')?{...fixture(path,params),trading_dates:[]}:fixture(path,params);
  await page.applyDates();assert.match(page.data.error,/交易日历/);assert.equal(page.data.chart,null);
  server.handler=fixture;await page.refresh();assert.equal(page.data.ready,true);
});
