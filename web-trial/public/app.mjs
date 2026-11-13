import { money, signClass, latestOnly, formatWeight, weightLabel, selectSeries, getMoneyUnit, setMoneyUnit, onMoneyUnitChange, revealSection, sourceDataDates, sourceDateLabel } from './logic.mjs';
import { renderTimeChart, renderContributions, renderCoverage } from './charts.mjs';
import { tradingDates, resetTradingCalendar } from './calendar.mjs';
import {beginPriceHistoryStock, setPriceHistoryStock, refreshPriceHistory} from './price-history.mjs';
const $ = id => document.getElementById(id);
const state = { stock: '', stockName: '', stockPeriod: '20d', rankPeriod: '1d', direction: 'in', industry: '', rankPage: 1, rankMore: false, searchPage: 1, searchMore: false, query: '' };
const rankGuard = latestOnly(), searchGuard = latestOnly(), stockGuard = latestOnly(), etfGuard = latestOnly(), metaGuard = latestOnly();
const statusNames = { complete: '完整', partial: '已披露部分持仓', disclosed_partial:'已披露贡献小计', no_ashare_holdings:'本期未列 A 股持仓', unavailable:'待取得有效披露', missing: '缺失', pending: '待纳入' };
function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
function signed(value) { const node=element('span',money(value),signClass(value));node.dataset.moneyValue=value==null?'':String(value);return node; }
function setSigned(id, value) { const node = $(id); node.textContent = money(value); node.className = signClass(value); }
function message(container, text, retry) {
  container.replaceChildren(element('span', text));
  if (retry) { const button = element('button', '重试'); button.type = 'button'; button.addEventListener('click', retry); container.append(' ', button); }
}
async function api(path, params = {}, dataset = 'market') {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== '' && value != null) query.set(key, String(value));
  const response = await fetch('/api/v1/' + dataset + path + (query.size ? '?' + query : ''), { headers: { Accept: 'application/json' } });
  let body; try { body = await response.json(); } catch { throw new Error('服务响应无法读取'); }
  if (!response.ok || body.code !== 0) throw new Error(body.message || '请求失败');
  return body.data;
}
function safeError(err) { return err && err.message ? err.message : '请求失败，请检查网络'; }
function activate(selector, value, attribute) {
  document.querySelectorAll(selector).forEach(button => {
    const active = button.dataset[attribute] === value;
    button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
  });
}
function readWatch() {
  try { const data = JSON.parse(localStorage.getItem('etf-flow-watch-v1') || '[]'); return Array.isArray(data) ? data.filter(x => x && /^\d{6}$/.test(x.code) && typeof x.name === 'string').slice(0, 100) : []; }
  catch { return []; }
}
function writeWatch(items) { try { localStorage.setItem('etf-flow-watch-v1', JSON.stringify(items)); return true; } catch { return false; } }
function renderWatch() {
  const watch = readWatch(); $('watch-count').textContent = String(watch.length); $('watch-list').replaceChildren();
  if (!watch.length) $('watch-list').append(element('div', '尚无自选，打开个股后可加入。', 'watch-empty'));
  watch.forEach(item => {
    const button = element('button', null, 'watch-row'), label = element('span', item.name);
    label.append(element('small', item.code)); button.append(label, element('span', '查看')); button.addEventListener('click', () => selectStock(item.code)); $('watch-list').append(button);
  });
  $('watch-toggle').textContent = watch.some(x => x.code === state.stock) ? '移出自选' : '加入自选';
}
let dailyRows = [], dailyPage = 1;
let contributionPage = 1;
const CONTRIBUTION_PAGE_SIZE = 20;
let currentChart = null, chartMode = 'flow', stockData = null, etfData = null, rankData = null;
let priceData = null;
const priceCache = new Map();
const DAILY_PAGE_SIZE = 50;
function renderDailyTable() {
  const body = $('series-body'); body.replaceChildren();
  const offset = (dailyPage - 1) * DAILY_PAGE_SIZE;
  // The chart uses ascending dates. Page backwards through those same rows for the table.
  for (let index = dailyRows.length - 1 - offset; index >= Math.max(0, dailyRows.length - offset - DAILY_PAGE_SIZE); index--) {
    const r = dailyRows[index];
    const tr = element('tr'); tr.append(element('td', r.trade_date || '—'));
    const td = element('td', money(r.flow), 'num');
    const close=priceData?.series.find(p=>p.trade_date===r.trade_date)?.close;
    tr.append(td, element('td',close==null?'—':new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:4}).format(close),'num')); body.append(tr);
  }
  $('series-page').textContent = '第 ' + dailyPage + ' 页 · 共 ' + dailyRows.length + ' 条';
  $('series-prev').disabled = dailyPage <= 1;
  $('series-next').disabled = offset + DAILY_PAGE_SIZE >= dailyRows.length;
}
function renderChart(series, movement, preservePage = false) {
  const start = movement.window_start || null, end = movement.window_end || null;
  if (currentChart?.series !== series || currentChart?.movement !== movement) {
    const allowedDates = Array.isArray(movement.trading_dates) ? new Set(movement.trading_dates) : null;
    const funds = selectSeries(series, start, end, !!movement.start_exclusive);
    const fundByDate = new Map(funds.map(row => [row.trade_date, row]));
    dailyRows = allowedDates ? [...allowedDates].sort().map(day => fundByDate.get(day) || {trade_date:day, flow:null}) : funds;
    currentChart = {series, movement, points:dailyRows.map(row => ({date:row.trade_date, value:row.flow}))};
    const last = dailyRows.at(-1), note = $('stock-latest-observation');
    const missingEnd = Array.isArray(series) && allowedDates?.has(end) && last?.trade_date === end && !Number.isFinite(last.flow);
    note.hidden = !missingEnd;
    note.textContent = '';
    if (missingEnd) {
      const recent = dailyRows.findLast(row => Number.isFinite(row.flow));
      note.textContent = end + ' 暂无可估算记录' + (recent ? '，本期最近有效日 ' + recent.trade_date : '');
    }
  }
  if (!preservePage) dailyPage = 1; renderDailyTable();
  $('chart-start').textContent = start ? (movement.start_exclusive ? start + '（不含）' : start) : '起点未知';
  $('chart-end').textContent = end || '终点未知';
  const cumulative=chartMode==='cumulative';
  $('chart-description').textContent=cumulative?'累计已有有效记录，红色为正、绿色为负；不代表缺口期间资金为零，不等于完整周期合计。':'红色为流入，绿色为流出；日期与金额可逐日查看，空白不代表零。';
  renderTimeChart($('chart'),currentChart.points,{instrument:stockData?.stock_code,start,end,tradingDates:movement.trading_dates,kind:cumulative?'line':'bar',cumulative,label:cumulative?'已有记录累计':'单日估算净流入',unit:'元',overlay:priceData?{label:'不复权收盘价',unit:'元/股',decimals:2,rows:priceData.series.map(r=>({date:r.trade_date,value:r.close}))}:undefined});
}
async function loadPrice(d, sequence) {
  const movement=d.movement||{}, code=d.stock_code,start=movement.window_start,end=movement.window_end;
  if(!start||!end||!Array.isArray(movement.trading_dates)||!movement.trading_dates.length){$('price-status').textContent='当前范围暂无可核验交易日，股价暂不展示。';return;}
  const key=[code,start,end].join('|'); message($('price-status'),'正在读取不复权历史收盘价…');
  try {
    let data=priceCache.get(key);
    if(!data){
      const response=await fetch('/api/v1/prices/stock/'+encodeURIComponent(code)+'?'+new URLSearchParams({start,end}),{headers:{Accept:'application/json'}});
      let body;try{body=await response.json();}catch{throw Error('股价暂不可用，请稍后重试');}
      if(!response.ok||body.code!==0)throw Error(body.message||'股价读取失败');data=body.data;
      if(!data||data.stock_code!==code||data.start!==start||data.end!==end||data.price_unit!=='元/股'||data.adjustment!=='none'||!Array.isArray(data.series)||data.series.length>12000)throw Error('股价身份或日期范围不匹配');
      let previous='';const days=new Set(movement.trading_dates);
      for(const row of data.series){if(typeof row.trade_date!=='string'||row.trade_date<=previous||row.trade_date<start||row.trade_date>end||!Number.isFinite(row.close)||row.close<=0)throw Error('股价记录未通过检查');previous=row.trade_date;}
      data={...data,series:data.series.filter(row=>days.has(row.trade_date))};
      if(data.series.length&&data.status==='ready'){priceCache.set(key,data);if(priceCache.size>20)priceCache.delete(priceCache.keys().next().value);}
    }
    if(!stockGuard.current(sequence)||stockData!==d)return;
    priceData=data;renderChart(d.series,movement,true);
    const span=data.series.length?(data.series[0].trade_date+' 至 '+data.series.at(-1).trade_date+' · '+data.series.length+' 个交易日'):'暂无可用股价';
    message($('price-status'),'来源：'+(data.source_label||'腾讯不复权日K')+'。实际覆盖：'+span+'。单位元/股；停牌或缺失留空。'+(data.failed_windows?'有 '+data.failed_windows+' 个历史分段暂不可用。':''),data.status==='ready'?null:()=>loadPrice(d,sequence));
  } catch(error) {if(!stockGuard.current(sequence)||stockData!==d)return;priceData=null;renderChart(d.series,movement,true);message($('price-status'),safeError(error)+'；资金图可继续查看。',()=>loadPrice(d,sequence));}
}
function stockStatus(data) {
  if (data.data_status === 'no_data') return '暂无可核验的关联持仓；不能据此判断没有 ETF 持有该股票，空值不代表零。';
  if (data.data_status === 'disclosed_partial') return '按当前披露权重回溯估算；缺失未计入，不代表真实买卖资金。';
  if (data.data_status === 'sse_only' || data.data_status === 'accumulating') return '深市 ETF 贡献暂缺，沪深个股均可能受影响。';
  return '';
}
function renderStock(d) {
  if(stockData!==d) contributionPage=1;
  stockData=d;
  setPriceHistoryStock(d);
  const movement = d.movement || {}, coverage = d.coverage || {}, contributions = Array.isArray(d.etf_contributions) ? d.etf_contributions : [];
  state.stockName = d.stock_name || d.stock_code; $('stock-heading').textContent = state.stockName + '  ' + d.stock_code;
  $('stock-subtitle').textContent = (d.industry_name || '未分类') + ' · 可查询至 ' + (d.trade_date || '—');
  $('stock-status').textContent = stockStatus(d); $('stock-status').hidden = !stockStatus(d);
  $('stock-status').classList.toggle('compact-notice', !!stockStatus(d));
  $('stock-content').hidden = false; $('watch-toggle').hidden = false; renderWatch();
  const available = contributions.filter(c=>c.flow!=null&&Number.isFinite(c.flow));
  setSigned('stock-net', movement.observed_net_flow ?? movement.net_flow);
  setSigned('stock-in', available.length ? available.reduce((n,c)=>n+Math.max(0,c.flow),0) : movement.inflow);
  setSigned('stock-out', available.length ? available.reduce((n,c)=>n+Math.min(0,c.flow),0) : movement.outflow);
  $('valid-days').textContent = '有效 ' + (movement.valid_days ?? '—') + ' / ' + (movement.window_days ?? '未知') + ' 日' + (movement.complete ? '' : ' · 数据不完整');
  $('window-coverage').textContent = '所选范围：' + (movement.requested_window_start || movement.window_start || '未知') + (movement.start_exclusive ? '（不含）' : '') + ' 至 ' + (movement.window_end || '未知') + '；实际覆盖：' + (movement.available_start || '未知') + ' 至 ' + (movement.available_end || '未知') + '；缺口：' + (movement.missing_days ?? '未知') + ' 个交易日。';
  $('observed-total').hidden = movement.complete || movement.observed_net_flow == null;
  $('observed-total').textContent = '已有记录累计：' + money(movement.observed_net_flow) + '；覆盖不完整，按所选披露报告的权重回溯估算。';
  $('in-count').textContent = (movement.inflow_etf_count ?? '—') + ' 只 ETF'; $('out-count').textContent = (movement.outflow_etf_count ?? '—') + ' 只 ETF';
  $('stock-coverage').textContent = (coverage.note || '仅显示追踪池内与该股相关的 ETF') + ' 关联 ' + (coverage.tracked_etfs ?? '—') + ' 只，待纳入 ' + (coverage.pending_etfs ?? '—') + ' 只。';
  $('etf-count').textContent = contributions.length + ' 只'; $('etf-detail').hidden = true;
  renderContributionTable();
  const note = typeof d.history_note === 'string' ? d.history_note : '';
  renderContributions($('contribution-chart'), contributions, {onShowAll:() => revealSection('stock-etf-columns')});
  renderCoverage($('coverage-visual'), movement.valid_days, movement.window_days);
  $('history-note').textContent = note || '按所选披露报告权重回溯估算，并非时点持仓，不适用于历史策略回测。';
  const flags = d.quality && Array.isArray(d.quality.flags) ? d.quality.flags.join('、') : '';
  $('quality-note').textContent = [d.quality && d.quality.note, flags && ('质量标记：' + flags)].filter(Boolean).join('；');
  renderChart(d.series, movement); activate('[data-stock-period]', state.stockPeriod, 'stockPeriod');
}
function renderContributionTable(){
  const items=Array.isArray(stockData?.etf_contributions)?stockData.etf_contributions:[];
  const pages=Math.max(1,Math.ceil(items.length/CONTRIBUTION_PAGE_SIZE));contributionPage=Math.min(contributionPage,pages);
  $('etf-body').replaceChildren();$('etf-state').textContent=items.length?'':'当前范围暂无可核验的 ETF 贡献，缺失不代表零。';
  for(const item of items.slice((contributionPage-1)*CONTRIBUTION_PAGE_SIZE,contributionPage*CONTRIBUTION_PAGE_SIZE)){
    const row=element('tr'),name=element('td'),button=element('button',(item.etf_name||'ETF')+' '+item.etf_code,'table-link');
    button.addEventListener('click',()=>loadEtf(item.etf_code));name.append(button);
    const amount=element('td',null,'num');amount.append(signed(item.flow));
    row.append(name,amount,element('td',formatWeight(item.weight),'num'),element('td',weightLabel(item.weight_source)+' / '+(item.weight_date||'—')),element('td',(statusNames[item.status]||item.status||'—')+' · 有效 '+(item.valid_days??'—')+' 日'));
    $('etf-body').append(row);
  }
  $('stock-etf-page').textContent='第 '+contributionPage+' / '+pages+' 页 · 共 '+items.length+' 只';
  $('stock-etf-prev').disabled=contributionPage<=1;$('stock-etf-next').disabled=contributionPage>=pages;
}
function selectStock(code) {
  openStock(code);
  revealSection('stock-heading');
}
async function openStock(code) {
  if (!/^\d{6}$/.test(code)) return;
  $('stock-panel').hidden=false;
  beginPriceHistoryStock(code);
  state.stock = code; const seq = stockGuard.next(); etfGuard.next(); stockData=null;etfData=null;priceData=null;currentChart=null; $('stock-content').hidden = true; $('watch-toggle').hidden = true;
  $('stock-heading').textContent = '正在加载'; $('stock-subtitle').textContent = code; $('stock-status').hidden = false;
  $('stock-status').classList.toggle('compact-notice', false); message($('stock-status'), '正在读取个股数据…');
  try { const d = await api('/stock/' + encodeURIComponent(code), { period: state.stockPeriod });
    if (!stockGuard.current(seq)) return;
    if (!Array.isArray(d.movement?.trading_dates) && d.movement?.window_start && d.movement?.window_end) {
      d.movement.trading_dates = await tradingDates(d.movement.window_start,d.movement.window_end);
      if (d.movement.start_exclusive) d.movement.trading_dates = d.movement.trading_dates.filter(day => day > d.movement.window_start);
    }
    if (!stockGuard.current(seq)) return; renderStock(d); loadPrice(d,seq); }
  catch (err) { if (!stockGuard.current(seq)) return; $('stock-heading').textContent = '个股数据未加载'; message($('stock-status'), safeError(err), () => openStock(code)); }
}
function loadEtf(code) {
  if (!/^\d{6}$/.test(code)) return;
  const item=stockData?.etf_contributions?.find(row=>row.etf_code===code);if(!item)return;
  etfData={...item,stock_code:stockData.stock_code,stock_name:stockData.stock_name,start:stockData.movement?.window_start,end:stockData.movement?.window_end,start_exclusive:!!stockData.movement?.start_exclusive};renderEtf();
}
function renderEtf(){
  if(!etfData)return;const d=etfData,box=$('etf-detail');box.hidden=false;
  box.replaceChildren(element('strong',(d.etf_name||'ETF')+' '+d.etf_code),element('div','对 '+(d.stock_name||d.stock_code)+' 的期间估算贡献：'+money(d.flow)),element('div',(d.start||'—')+(d.start_exclusive?'（不含）':'')+' 至 '+(d.end||'—')+' · 有效 '+(d.valid_days??'—')+' 日'),element('div','披露权重 '+formatWeight(d.weight)+' · '+weightLabel(d.weight_source)+' / '+(d.weight_date||'—')),element('div','这是该 ETF 对所选股票的估算贡献；ETF 自身净申赎见基金历史。'));
  const open=element('button','查看同期基金历史','outline-button');open.type='button';open.id='stock-fund-history';
  open.addEventListener('click',()=>{
    let start=d.start;
    if(d.start_exclusive && start){
      const first=new Date(start+'T00:00:00Z');
      if(Number.isFinite(first.getTime())){first.setUTCDate(first.getUTCDate()+1);start=first.toISOString().slice(0,10);}
    }
    const range=start&&d.end?{range_start:start,range_end:d.end}:{};
    document.dispatchEvent(new CustomEvent('etf-open-history',{detail:{fund_code:d.etf_code,fund_name:d.etf_name,return_view:'stock',...range}}));
  });
  const close=element('button','收起','text-button');close.type='button';close.addEventListener('click',()=>{etfData=null;box.hidden=true;});box.append(open,' ',close);
}
async function loadSearch(reset = true) {
  const query = $('search').value.trim();
  if (!query) { searchGuard.next(); state.query = ''; $('search-results').replaceChildren(); return; }
  state.query = query; const seq = searchGuard.next(), page = reset ? 1 : state.searchPage + 1;
  if (reset) message($('search-results'), '正在搜索…');
  try {
    const d = await api('/stocks', { q: query, page, page_size: 10 });
    if (!searchGuard.current(seq)) return;
    const results = $('search-results'); if (reset) results.replaceChildren();
    const oldMore = results.querySelector('.search-more'); if (oldMore) oldMore.remove();
    state.searchPage = page; state.searchMore = !!d.has_more;
    if (!d.items.length && reset) { message(results, '未找到匹配股票。'); return; }
    d.items.forEach(item => {
      const button = element('button', null, 'result'), label = element('span', item.stock_name || '—');
      label.append(element('small', item.stock_code + ' · ' + (item.data_status === 'no_data' ? '当前 ETF 池未覆盖' : (item.industry_name || '未分类'))));
      const value = element('span', null, 'result-value'); value.append(signed(item.flow_1d), element('small', '单日已有贡献小计')); button.append(label, value);
      button.addEventListener('click', () => { $('search-results').replaceChildren(); $('search').value = ''; state.query = ''; searchGuard.next(); selectStock(item.stock_code); });
      results.append(button);
    });
    if (state.searchMore) { const more = element('button', '加载更多', 'result search-more'); more.addEventListener('click', () => loadSearch(false)); results.append(more); }
  } catch (err) { if (searchGuard.current(seq)) message($('search-results'), safeError(err), () => loadSearch(reset)); }
}
async function loadRank() {
  const seq = rankGuard.next(), list = $('rank-list'); message(list, '正在加载榜单…');
  $('rank-prev').disabled = true; $('rank-next').disabled = true;
  try {
    const d = await api('/rank', { period: state.rankPeriod, direction: state.direction, industry: state.industry, page: state.rankPage, page_size: 10 });
    if (!rankGuard.current(seq)) return; list.replaceChildren(); state.rankMore = !!d.has_more;
    $('rank-page').textContent = '第 ' + state.rankPage + ' 页 · 共 ' + (d.total ?? '—') + ' 只';
    $('rank-prev').disabled = state.rankPage <= 1; $('rank-next').disabled = !state.rankMore;
    if (!d.items.length) message(list, '当前筛选条件下暂无数据。');
    d.items.forEach(item => {
      const button = element('button', null, 'rank-row'), label = element('span', item.stock_name || '—');
      label.append(element('small', item.stock_code + ' · ' + (item.industry_name || '未分类')));
      const value = signed(item.flow);value.className='rank-value '+signClass(item.flow);
      button.append(element('span', String(item.rank), 'rank-number'), label, value);
      button.addEventListener('click', () => selectStock(item.stock_code)); list.append(button);
    });
  } catch (err) {
    if (!rankGuard.current(seq)) return;
    message(list, safeError(err), loadRank); $('rank-prev').disabled = state.rankPage <= 1;
  }
}
async function loadMeta() {
  const sequence = metaGuard.next();
  $('trade-date').textContent = '正在读取…';
  $('refresh-time').textContent = '正在读取…';
  $('refresh-time').dateTime = '';
  try {
    const data = await api('/meta'), coverage = data.coverage || {};
    if (!metaGuard.current(sequence)) return;
    const history = data.history_version ? await api('/meta', {}, 'history').catch(() => null) : null;
    if (!metaGuard.current(sequence)) return;
    const dates = sourceDataDates(history, data.history_version);
    $('trade-date').textContent = sourceDateLabel(dates.shares);
    const stamp = $('refresh-time');
    stamp.dateTime = dates.nav.date || '';
    stamp.textContent = sourceDateLabel(dates.nav);
    $('coverage').textContent = '全市场目录 ' + (coverage.tracked_etfs ?? '—') + ' 只 ETF，已有 A 股披露持仓 ' + (coverage.active_etfs ?? '—') + ' 只；其余 ' + (coverage.pending_etfs ?? '—') + ' 只的状态可在下方逐只查看。披露持仓可能只是部分股票，目录覆盖不等于资金计算完整。';
    $('disclosure').textContent = (data.disclosure && data.disclosure.short) || '估算值非真实买卖资金，不构成投资建议。';
  } catch (err) { if (!metaGuard.current(sequence)) return; $('trade-date').textContent = '加载失败'; $('refresh-time').textContent = '暂无'; $('refresh-time').dateTime = ''; $('coverage').textContent = '数据范围暂不可用，请刷新重试。'; }
}
async function loadIndustries() {
  try { const d = await api('/industries'); const select = $('industry');
    (d.items || []).forEach(item => { const option = element('option', item.industry_name); option.value = item.industry_code; select.append(option); });
  } catch { $('industry').insertAdjacentText('afterend', '行业加载失败，刷新页面重试。'); }
}
let searchTimer;
$('search').addEventListener('input', () => { clearTimeout(searchTimer); searchGuard.next(); const q = $('search').value.trim(); if (!q) { $('search-results').replaceChildren(); return; } message($('search-results'), '正在搜索…'); searchTimer = setTimeout(() => loadSearch(true), 250); });
$('clear-search').addEventListener('click', () => { clearTimeout(searchTimer); searchGuard.next(); $('search').value = ''; $('search-results').replaceChildren(); $('search').focus(); });
$('search').addEventListener('keydown', event => { if (event.key === 'Escape') { clearTimeout(searchTimer); searchGuard.next(); $('search-results').replaceChildren(); } if (event.key === 'Enter') { event.preventDefault(); clearTimeout(searchTimer); loadSearch(true); } });
document.querySelectorAll('[data-stock-period]').forEach(button => button.addEventListener('click', () => { state.stockPeriod = button.dataset.stockPeriod; activate('[data-stock-period]', state.stockPeriod, 'stockPeriod'); if (state.stock) openStock(state.stock); }));
document.querySelectorAll('[data-rank-period]').forEach(button => button.addEventListener('click', () => { state.rankPeriod = button.dataset.rankPeriod; state.rankPage = 1; activate('[data-rank-period]', state.rankPeriod, 'rankPeriod'); loadRank(); }));
document.querySelectorAll('[data-direction]').forEach(button => button.addEventListener('click', () => { state.direction = button.dataset.direction; state.rankPage = 1; activate('[data-direction]', state.direction, 'direction'); loadRank(); }));
$('industry').addEventListener('change', () => { state.industry = $('industry').value; state.rankPage = 1; loadRank(); });
$('rank-prev').addEventListener('click', () => { if (state.rankPage > 1) { state.rankPage--; loadRank(); revealSection('rank-heading'); } });
$('rank-next').addEventListener('click', () => { if (state.rankMore) { state.rankPage++; loadRank(); revealSection('rank-heading'); } });
$('watch-toggle').addEventListener('click', () => {
  const items = readWatch(), found = items.some(x => x.code === state.stock);
  const updated = found ? items.filter(x => x.code !== state.stock) : items.concat([{ code: state.stock, name: state.stockName }]);
  if (!writeWatch(updated)) { message($('stock-status'), '浏览器无法保存自选，请检查存储权限。'); $('stock-status').hidden = false; return; }
  renderWatch();
});
$('series-prev').addEventListener('click', () => { if (dailyPage > 1) { dailyPage--; renderDailyTable(); revealSection('series-heading'); } });
$('series-next').addEventListener('click', () => { if (dailyPage * DAILY_PAGE_SIZE < dailyRows.length) { dailyPage++; renderDailyTable(); revealSection('series-heading'); } });
$('stock-etf-prev').addEventListener('click',()=>{if(contributionPage>1){contributionPage--;renderContributionTable();revealSection('stock-etf-heading');}});
$('stock-etf-next').addEventListener('click',()=>{if(contributionPage*CONTRIBUTION_PAGE_SIZE<(stockData?.etf_contributions?.length||0)){contributionPage++;renderContributionTable();revealSection('stock-etf-heading');}});
document.querySelectorAll('[data-chart-mode]').forEach(button=>button.addEventListener('click',()=>{chartMode=button.dataset.chartMode;activate('[data-chart-mode]',chartMode,'chartMode');if(currentChart)renderChart(currentChart.series,currentChart.movement);}));
$('refresh').addEventListener('click', () => { resetTradingCalendar(); priceCache.clear(); refreshPriceHistory(); loadMeta(); loadRank(); if (state.stock) openStock(state.stock); });
const unitControl=$('money-unit');unitControl.value=getMoneyUnit();
unitControl.addEventListener('change',()=>setMoneyUnit(unitControl.value));
onMoneyUnitChange(()=>{
  unitControl.value=getMoneyUnit();
  document.querySelectorAll('[data-money-value]').forEach(node=>{node.textContent=money(node.dataset.moneyValue===''?null:Number(node.dataset.moneyValue));});
  if(stockData){const page=dailyPage;renderStock(stockData);dailyPage=page;renderDailyTable();renderEtf();}
});
activate('[data-stock-period]', state.stockPeriod, 'stockPeriod'); activate('[data-rank-period]', state.rankPeriod, 'rankPeriod'); activate('[data-direction]', state.direction, 'direction');
renderWatch(); loadMeta(); loadIndustries(); loadRank();

let fundPage=1;const fundGuard=latestOnly();
async function loadFundCoverage(){
  const sequence=fundGuard.next();$('market-funds-body').replaceChildren();$('market-funds-state').textContent='正在读取全市场核验状态…';
  try{const d=await api('/etfs',{page:fundPage,page_size:50});if(!fundGuard.current(sequence))return;
    for(const f of d.items||[]){const row=element('tr');row.append(element('td',(f.fund_name||f.fund_code)+' '+f.fund_code),element('td',statusNames[f.status]||'待核验'),element('td',f.report_date||'未取得报告期'),element('td',Number.isFinite(f.coverage_pct)?f.coverage_pct.toFixed(2)+'%':'—'),element('td',String(f.stock_count??0)));$('market-funds-body').append(row);}
    $('market-funds-state').textContent='第 '+fundPage+' 页 · 全目录 '+d.total+' 只；比例为已披露 A 股持仓占基金净值，不代表所有持仓已经完整。';
    $('market-funds-prev').disabled=fundPage<=1;$('market-funds-next').disabled=!d.has_more;
  }catch(error){if(fundGuard.current(sequence))message($('market-funds-state'),safeError(error),loadFundCoverage);}
}
$('market-funds-detail').addEventListener('toggle',()=>{if($('market-funds-detail').open)loadFundCoverage();});
$('market-funds-prev').addEventListener('click',()=>{if(fundPage>1){fundPage--;loadFundCoverage();revealSection('market-funds-heading');}});
$('market-funds-next').addEventListener('click',()=>{fundPage++;loadFundCoverage();revealSection('market-funds-heading');});
