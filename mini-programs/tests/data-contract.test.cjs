const test=require('node:test'),assert=require('node:assert/strict');
const {chartModel}=require('../src/lib/chart'),{money,periodStart}=require('../src/lib/format');
const {readFileSync}=require('node:fs'),{deflateRawSync}=require('node:zlib');
const {runInNewContext}=require('node:vm');

// Exercise the production decoder and queries on compressed, synthetic records.
// Real package parity is checked separately by compare-local.mjs --pinned.
function fixture(){
  const dates=['2026-11-06','2026-11-09','2026-11-10'];
  const meta={start:dates[0],end:dates[2],broad:{version:'b',total_funds:1,indexes:[{code:'000300'}]},
    history:{version:'h'},stocks:[{stock_code:'300750',stock_name:'测试甲'},{stock_code:'600519',stock_name:'测试乙',stock_listed_date:dates[0]}]};
  const index={index:{code:'all'},offCodes:[],members:[['510300','测试基金',dates[0],null,'000300']],
    rows:dates.map((day,i)=>[day,i===1?0:null,i===1?0:null,i===1?1:0,1,0,100+i])};
  const blocks={calendar:dates,indices:{all:index},funds:{510300:{rows:[[dates[1],1,1,1,0,'estimated']]}},
    off:[],s600519:{prices:[[dates[0],10]],excluded:[]}};
  const module={exports:{}};
  function load(id){
    if(id==='./snapshot/meta')return meta;
    if(id==='./vendor/tiny-inflate')return require('../src/lib/vendor/tiny-inflate');
    const value=blocks[id.slice('./snapshot/'.length)];
    if(value===undefined)throw Error('unexpected fixture module '+id);
    const bytes=Buffer.from(JSON.stringify(value));
    return [bytes.length,deflateRawSync(bytes).toString('base64')];
  }
  runInNewContext(readFileSync(require.resolve('../src/lib/offline'),'utf8'),{module,require:load});
  return module.exports;
}
test('a late 401 from an old pairing cannot clear a new connection',async()=>{
  const {readFileSync}=require('node:fs'),vm=require('node:vm');
  const source=readFileSync(require.resolve('../src/lib/data'),'utf8');
  const module={exports:{}};let session='A',resolveOld,headers;
  const modules={'./config':{mode:'live',baseUrl:'https://unit.test'},'./offline':{meta:{}},'./platform':{request:opts=>{
    if(opts.method==='POST')return Promise.resolve({statusCode:200,data:{session}});
    headers=opts.header;
    if(headers.Authorization==='Bearer A')return new Promise(resolve=>{resolveOld=resolve;});
    return Promise.resolve({statusCode:200,data:{code:0,data:{version:'new'}}});
  }}};
  vm.runInNewContext(source,{module,require:id=>modules[id]});
  const api=module.exports;await api.pair('00000001');
  const old=api.get('broad/meta');session='B';await api.pair('00000002');
  resolveOld({statusCode:401});await assert.rejects(old,/连接状态已更新/);
  assert.equal(api.sourceInfo().paired,true);assert.equal((await api.get('broad/meta')).version,'new');
  assert.equal(headers.Authorization,'Bearer B');assert.equal(headers['Cache-Control'],'no-cache');
});
test('offline package lists only its own dates and searchable stock universe',()=>{
  const offline=fixture();
  const m=offline.meta;
  assert.equal(m.broad.total_funds,1);assert.equal(m.broad.indexes.length,1);
  assert.equal(offline.query('market/stocks',{q:'测试甲'}).items[0].stock_code,'300750');
  assert.equal(offline.query('market/stocks',{q:'600000'}).total,0);
  assert.throws(()=>offline.query('market/stock/600000'),/离线/);
  assert.equal(offline.query('prices/stock/600519',{start:m.start,end:m.start}).series[0].trade_date,m.start);
  assert.throws(()=>offline.query('broad/index/000300',{start:'2000-01-01',end:m.end}),/可查日期/);
});
test('closed dates, missing amounts and a reported zero remain distinct',()=>{
 const offline=fixture();
 const idx=offline.query('broad/index/all',{start:'2026-11-07',end:'2026-11-08'});
 assert.equal(idx.trading_dates.length,0);assert.equal(idx.series.length,0);assert(idx.members.every(f=>f.period_observed_flow===null));
 const zero=offline.query('broad/index/all',{start:'2026-11-09',end:'2026-11-09'});assert.equal(zero.members[0].period_observed_flow,0);
 const day=offline.query('broad/index/all',{start:offline.meta.end,end:offline.meta.end});
 assert.equal(day.series[0].observed_flow,null);assert.equal(money(null),'—');assert.equal(money(0),'0.00 万元');
 assert.equal(periodStart('2024-03-31',1,'2022-01-01'),'2024-02-29');
});
test('grouped long charts preserve reported flow totals, real dates and price gaps',()=>{
 const rows=Array.from({length:104},(_,i)=>({date:String(i).padStart(3,'0'),value:i%2?null:10,overlay:i<2?null:7}));
 const chart=chartModel(rows,{label:'当日估算',kind:'bar',currency:true,overlayLabel:'收盘'});
 assert.equal(chart.points.length,52);assert.equal(chart.points[0].coverage,'组内有值 1 / 2 日');
 assert.match(chart.label,/组内合计/);assert.equal(chart.points[0].date,'000 至 001');
 assert.equal(chart.points[0].overlayText,'暂无记录');
 const line=chartModel([{date:'a',value:2},{date:'b',value:null},{date:'c',value:3}],{kind:'line',unit:'元/股'});
 assert.equal(line.segments.length,0);assert.equal(line.points[0].tone,'neutral');
});
