const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
for(const host of ['wechat','xiaohongshu']){
 test(host+' opens an index or stock from a card and recreates the confirmed selection',async()=>{
  global[host==='wechat'?'wx':'xhs']={getStorageSync:()=>'',setStorageSync(){},request(){throw Error('offline must not request data');}};
  function load(page){
    let definition;global.Page=d=>definition=d;
    require(path.resolve(process.env.ETF_MINI_DIST || path.resolve(__dirname,'../dist'),host,'pages',page,'index.js'));
    return {...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(update){Object.assign(this.data,update);}};
  }
  function restored(share){
    const url=new URL(share.path,'https://test.invalid');
    if(host==='xiaohongshu'){assert.equal(url.search,'');url.search=share.query;}
    return Object.fromEntries(url.searchParams);
  }
  const broad=load('broad');
  const target=path.resolve(process.env.ETF_MINI_DIST || path.resolve(__dirname,'../dist'),host);
  const config=require(path.join(target,'lib/config.js'));
  if(config.mode!=='snapshot'){
    const data=require(path.join(target,'lib/data.js'));
    assert.deepEqual(data.sourceInfo().stocks,[]);
    assert.equal(data.sourceInfo().target_end,'');
    assert(!require('node:fs').existsSync(path.join(target,'lib/snapshot/meta.js')));
    return;
  }
  const meta=require(path.join(target,'lib/snapshot/meta.js')),start=meta.start,end=meta.end;
  await broad.onLoad({code:'000905',start,end});
  assert.equal(broad.data.ready,true);assert.deepEqual(restored(broad.onShareAppMessage()),{code:'000905',start,end});
  const dates=broad._result.trading_dates;
  if(dates.length){
    const day=dates[dates.length-1],record=broad._result.series.at(-1);
    broad.inspectDay({detail:{date:day}});
    assert.equal(broad.data.chartDay.date,day);assert.equal(broad.data.chartDay.listed,true);
    assert.equal(broad.data.chartDay.overlayText.includes('点'),Number.isFinite(record.index_close));
    broad.inspectDay({detail:{date:''}});assert.equal(broad.data.chartDay,null);
  }
  broad.onUnload();
  const stock=load('stock');await stock.onLoad({code:'300750',period:'5y'});
  assert.equal(stock.data.state,'ready');assert.deepEqual(restored(stock.onShareAppMessage()),{code:'300750',period:'5y'});
  stock.onUnload();
 });
}
