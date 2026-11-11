const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const source=readFileSync(path.join(__dirname,'../src/pages/stock/index.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const event=value=>({currentTarget:{dataset:{value}}});
const codeEvent=code=>({currentTarget:{dataset:{code}}});
const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {resolve,reject,promise};};
const dates=['2026-11-06','2026-11-09','2026-11-10'];
const listed='2001-08-27';

function stock(code,period) {
  return {stock_code:code,stock_name:code==='600519'?'贵州茅台':'宁德时代',stock_listed_date:listed,trade_date:dates[2],
    movement:{period,window_start:dates[0],window_end:dates[2],trading_dates:dates,observed_net_flow:20},
    series:dates.map((trade_date,i)=>({trade_date,flow:[20,0,null][i]})),
    etf_contributions:Array.from({length:23},(_,i)=>({etf_code:String(510000+i),etf_name:'ETF '+i,flow:i===22?null:i===21?-200:i,weight:0.01,weight_date:'2026-06-30',valid_days:2}))};
}
function prices(code,start,end,allDates=dates) {
  return {stock_code:code,stock_listed_date:listed,start,end,adjustment:'none',price_unit:'元/股',source_label:'东方财富不复权日K',
    source_calendar_excluded_count:1,trading_dates:allDates,series:allDates.filter((_,i)=>i!==1).map((trade_date,i)=>({trade_date,close:100+i}))};
}
function page(get,info={mode:'snapshot',stocks:[]}) {
  const calls=[],nav=[],saved={},charts=[];let definition;
  const libraries={
    '../../lib/fund-list':require('../src/lib/fund-list'),
    '../../lib/data':{get:(url,params)=>{calls.push({url,params});return get(url,params);},sourceInfo:()=>info},
    '../../lib/platform':{nav:url=>nav.push(url),storageGet:key=>saved[key],storageSet:(key,v)=>saved[key]=v},
    '../../lib/format':{money:(n,u)=>Number.isFinite(n)?n+' '+u:'—',number:(n,d)=>n.toFixed(d)},
    '../../lib/chart':{chartModel:(rows,options)=>{charts.push({rows,options});return {points:rows.slice(-5),count:rows.length,options};}}
  };
  vm.runInNewContext(source,{Page:d=>definition=d,require:name=>{if(!libraries[name])throw Error(name);return libraries[name];},setTimeout,clearTimeout});
  const p={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(patch){Object.assign(this.data,patch);}};
  p.onLoad({});return {p,calls,nav,saved,charts};
}

test('snapshot uses its actual available-stock manifest for one-tap entry; online has no sample list',async()=>{
  const info={mode:'snapshot',stocks:[{stock_code:'600519',stock_name:'贵州茅台'},{stock_code:'300750',stock_name:'宁德时代'}]};
  const demo=page((url,params)=>url.startsWith('market/')?Promise.resolve(stock('600519',params.period)):
    Promise.resolve(prices('600519',params.start,params.end)),info);
  assert.equal(demo.p.data.sampleStocks.length,2);await demo.p.chooseStock(codeEvent(demo.p.data.sampleStocks[0].code));await tick();
  assert.equal(demo.p.data.view,'detail');assert.equal(demo.p.data.name,'贵州茅台');
  assert.equal(page(()=>{throw Error('no catalogue request on startup');},{mode:'live',stocks:[]}).p.data.sampleStocks.length,0);
});

test('search keeps latest query, supports pagination and does not request an empty directory',async()=>{
  const requests=[];const {p,calls}=page(()=>{const d=deferred();requests.push(d);return d.promise;});
  p.inputSearch({detail:{value:'茅台'}});const old=p.submitSearch();p.inputSearch({detail:{value:'300750'}});const fresh=p.submitSearch();
  requests[1].resolve({items:[{stock_code:'300750',stock_name:'宁德时代'}],has_more:true});await fresh;
  requests[0].resolve({items:[{stock_code:'600519',stock_name:'贵州茅台'}]});await old;
  assert.equal(p.data.searchRows[0].code,'300750');const next=p.changeSearchPage(event(1));assert.equal(calls.at(-1).params.page,2);
  requests[2].resolve({items:[],has_more:false});await next;assert.equal(p.data.searchState,'empty');
  p.clearSearch();assert.equal(p.data.searchState,'idle');assert.equal(calls.length,3);p.onUnload();
});

test('funds load before prices, unit and pagination reuse data, full list navigates and watch returns to search',async()=>{
  const pricesPending=deferred();const {p,calls,charts,nav}=page((url,params)=>url.startsWith('market/stock/')?Promise.resolve(stock('600519',params.period)):pricesPending.promise);
  await p.chooseStock(codeEvent('600519'));assert.equal(calls[0].params.period,'20d');assert.equal(p.data.state,'ready');assert.equal(p.data.priceState,'loading');
  assert.equal(p.data.net,'20 auto');assert.equal(p.data.endAmount,'暂无估算');assert.equal(p.data.recentDate,'2026-11-09','real zero counts as a valid latest flow');
  assert.equal(p.data.top.length,10);assert.equal(p.data.top[0].code,'510021');assert.equal(p.data.fundCount,23);
  pricesPending.resolve(prices('600519',dates[0],dates[2]));await tick();assert.equal(p.data.priceState,'ready');
  assert.equal(charts.at(-1).rows[0].overlay,100);assert.equal(charts.at(-1).rows[1].overlay,null);assert.equal(charts.at(-1).rows[2].value,null);
  assert.equal(p.data.daily.items[0].date,dates[2]);assert.equal(p.data.daily.items[0].amount,'—');assert.equal(p.data.daily.items[0].close,'101.00');
  p.chooseUnit(event('yi'));assert.equal(p.data.net,'20 yi');assert.equal(p.data.daily.items[0].close,'101.00');
  p.toggleFunds();p.changeFunds(event(1));assert.equal(p.data.funds.items.length,3);assert.equal(p.data.funds.items[2].amount,'—');
  p.openFund(codeEvent('510022'));p.openFund(codeEvent('000000'));assert.equal(nav.length,1);
  const dest=new URL(nav[0],'https://unit.test');
  assert.equal(dest.pathname,'/pages/fund/index');
  assert.deepEqual(Object.fromEntries(dest.searchParams),{code:'510022',from:'stock',name:'ETF 22',start:dates[0],end:dates[2],unit:'yi'});
  p.toggleWatch();assert.equal(p.data.watched,true);p.backToSearch();assert.equal(p.data.view,'search');assert.equal(p.data.watch[0].code,'600519');assert.equal(calls.length,2);
});

test('searching all ETF sources preserves context on live fund return and clamps a shorter new list',async()=>{
  let count=23;
  const {p,calls}=page(async(url,params)=>{
    if(url==='market/meta')return {version:'m1',history_version:'h1',target_end:dates[2]};
    if(url.startsWith('market/stock/')){
      const data=stock(url.split('/').pop(),params.period);data.etf_contributions=data.etf_contributions.slice(0,count);return data;
    }
    return {...prices('600519',params.start,params.end),market_version:'m1',history_version:'h1',target_end:dates[2]};
  },{mode:'local',stocks:[]});
  await p.openStock('600519');await tick();p.toggleFunds();
  const requests=calls.length,headline=p.data.net;
  p.selectFundDirection(event('out'));assert.equal(p.data.funds.total,1);assert.equal(p.data.funds.items[0].code,'510021');
  p.searchFunds({detail:{value:'etf 22'}});assert.equal(p.data.funds.total,0);
  p.selectFundDirection(event('missing'));assert.equal(p.data.funds.items[0].code,'510022');
  assert.equal(p.data.net,headline);assert.equal(calls.length,requests);
  p.openFund(codeEvent('510022'));p.onHide();await p.onShow();await tick();
  assert.equal(p.data.fullOpen,true);assert.equal(p.data.fundQuery,'etf 22');assert.equal(p.data.fundDirection,'missing');
  assert.equal(p.data.funds.items[0].amount,'—');
  p.resetFunds();p.changeFunds(event(1));assert.equal(p.data.funds.current,2);assert(p.data.funds.items.some(row=>row.code==='510000'));
  p.onHide();await p.onShow();await tick();assert.equal(p.data.funds.current,2);
  count=4;p.onHide();await p.onShow();await tick();assert.equal(p.data.funds.current,1);assert.equal(p.data.funds.total,4);
  p.searchFunds({detail:{value:'510000'}});await p.openStock('300750');await tick();
  assert.equal(p.data.fullOpen,false);assert.equal(p.data.fundQuery,'');assert.equal(p.data.fundDirection,'all');
});

test('mismatched/stale stock or price responses never overwrite the new selection',async()=>{
  const requests=[];const {p}=page(()=>{const d=deferred();requests.push(d);return d.promise;});
  const one=p.openStock('300750'),two=p.openStock('600519');requests[1].resolve(stock('600519','20d'));await two;
  requests[0].resolve(stock('300750','20d'));await one;assert.equal(p.data.code,'600519');
  const change=p.choosePeriod(event('5y'));requests[3].resolve(stock('600519','5y'));await change;
  requests[2].resolve(prices('600519',dates[0],dates[2]));await tick();assert.equal(p.data.priceState,'loading');
  requests[4].resolve(prices('300750',dates[0],dates[2]));await tick();assert.equal(p.data.priceState,'error');assert.equal(p.data.state,'ready');
  const retry=p.loadOverlay();requests[5].resolve(prices('600519',dates[0],dates[2]));await retry;assert.equal(p.data.priceState,'ready');
  const wrong=p.choosePeriod(event('all'));requests[6].resolve(stock('300750','all'));await wrong;assert.equal(p.data.state,'error');
});

test('stock to ETF carries the live history version and converts the excluded start to an inclusive date',async()=>{
  const meta={version:'m1',history_version:'original-h',target_end:dates[2]};
  const {p,nav}=page((url,params)=>{
    if(url==='market/meta')return meta;
    if(url.startsWith('market/stock/')){
      const d=stock('600519',params.period);d.movement.start_exclusive=true;d.movement.window_start='2026-11-05';return d;
    }
    return {...prices('600519',params.start,params.end),market_version:meta.version,history_version:meta.history_version,target_end:meta.target_end};
  },{mode:'live',stocks:[]});
  await p.openStock('600519');p.openFund(codeEvent('510021'));
  const params=new URL(nav[0],'https://unit.test').searchParams;
  assert.equal(params.get('start'),'2026-11-06');assert.equal(params.get('history_version'),'original-h');
});

test('live funds and prices must share the verified publication even when the trading date is unchanged',async()=>{
  let version='m1',priceVersion='m2',reissueDuringFunds=false,net=20;
  const get=(url,params)=>{
    if(url==='market/meta')return {version,history_version:'h1',target_end:dates[2]};
    if(url.startsWith('market/stock/')){
      if(reissueDuringFunds){version='m3';reissueDuringFunds=false;}
      const d=stock('600519',params.period);d.movement.observed_net_flow=net;return d;
    }
    return {...prices('600519',params.start,params.end),market_version:priceVersion,history_version:'h1',target_end:dates[2]};
  };
  const {p,calls}=page(get,{mode:'live',stocks:[]});
  await p.openStock('600519');await tick();const before=calls.length;await p.onShow();assert.equal(calls.length,before);
  assert.equal(p.data.state,'ready');assert.equal(p.data.priceState,'error');assert.match(p.data.priceNote,/不同版本/);
  assert(p.data.chart.points.every(row=>row.overlay===null));
  version='m2';net=40;p.onHide();await p.onShow();await tick();
  assert.equal(p.data.net,'40 auto');assert.equal(p.data.priceState,'ready');
  await p.openLife();p.setLifeDate({currentTarget:{dataset:{field:'lifeFrom'}},detail:{value:'2026-11-06'}});
  reissueDuringFunds=true;await p.retryStock();assert.equal(p.data.state,'error');assert.match(p.data.error,/快照已更新/);
  priceVersion='m3';await p.retryStock();await tick();
  assert.equal(p.data.priceState,'ready');assert.equal(p.data.lifeState,'changed');
  assert.equal(p.data.lifeFrom,'2026-11-06');assert.equal(p.data.lifeChart,null);
});

test('lifetime query begins at IPO, reports calendar disagreement, pages newest first, clears invalid ranges and ignores late results',async()=>{
  const requests=[];const {p,calls}=page((url,params)=>{
    if(url.startsWith('market/'))return Promise.resolve(stock('600519',params.period));
    if(params.start===dates[0])return Promise.resolve(prices('600519',params.start,params.end));
    const d=deferred();requests.push(d);return d.promise;
  });
  await p.openStock('600519');await tick();const opening=p.openLife();assert.equal(calls.at(-1).params.start,listed);
  const many=Array.from({length:45},(_,i)=>new Date(Date.UTC(2001,7,27+i)).toISOString().slice(0,10)).concat(dates);
  requests[0].resolve(prices('600519',listed,dates[2],many));await opening;assert.equal(p.data.lifeState,'ready');assert.match(p.data.lifeNote,/来源日期待日历核验/);
  assert.equal(p.data.lifeDaily.items[0].date,dates[2]);const before=calls.length;p.toggleLifeDaily();p.changeLifeDaily(event(1));assert.equal(p.data.lifeDaily.current,2);assert.equal(calls.length,before);
  p.setLifeDate({currentTarget:{dataset:{field:'lifeFrom'}},detail:{value:'1990-01-02'}});await p.loadLife();assert.equal(p.data.lifeState,'error');assert.equal(p.data.lifeChart,null);assert.equal(calls.length,before);
  const repeat=p.openLife();p.closeLife();requests[1].resolve(prices('600519',listed,dates[2],many));await repeat;assert.equal(p.data.lifeOpen,false);assert.equal(p.data.lifeChart,null);
});
