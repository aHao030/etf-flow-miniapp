import { latestOnly, money, onMoneyUnitChange, revealSection } from './logic.mjs';
import { renderTimeChart } from './charts.mjs';
import { tradingDates, resetTradingCalendar } from './calendar.mjs';
const $ = id => document.getElementById(id);
const state = { targetStart:'2021-01-01', targetEnd:'', query:'', market:'', catalogPage:1, catalogTotal:0, code:'', start:'2021-01-01', end:'', historyPage:1, historyTotal:0 };
const catalogGuard = latestOnly(), detailGuard = latestOnly(), metaGuard = latestOnly(), chartGuard = latestOnly();
let metaLoading = false;
let followLatestEnd = true;
let entryRange = null, openingFund = false;
let chartKey = '', chartData = null, chartCleanup = null, historyMetric = 'flow', detailItems = null;
const marketNames = { SH:'上海', SZ:'深圳' };
const lifecycleNames = { current_seen:'当前目录在列', listed:'上市中', active:'上市中', delisted:'已退市', terminated:'已终止', unknown:'存续待核实' };
const qualityNames = {
  raw_observation: '原始观测', estimated: '估算', missing_previous_shares: '前日缺份额',
  missing_current_shares: '当日缺份额', missing_previous_nav: '前日缺净值',
  missing_current_nav: '当日缺净值', possible_split: '疑似折算',
  events_unverified: '事件校验未完成', fund_event: '基金事件待核实', money_market: '货币基金：仅展示原始数据'
};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function setMessage(target, text, retry) {
  target.replaceChildren(el('span', text));
  if (retry) { const button = el('button', '重试'); button.type = 'button'; button.addEventListener('click', retry); target.append(' ', button); }
}
function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits:digits }).format(Number(value));
}
function dateRange(info) {
  if (!info || info.count === null || info.count === undefined) return '覆盖范围未知';
  if (info.count === 0) return '暂无记录';
  return formatNumber(info.count) + ' 条 · ' + (info.start || '—') + ' 至 ' + (info.end || '—');
}
function showNotes(target, notes) {
  target.replaceChildren();
  if (Array.isArray(notes)) notes.forEach(note => target.append(el('p', note)));
}
function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(value + 'T00:00:00Z');
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0,10) === value;
}
async function api(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key,value] of Object.entries(params)) if (value !== '' && value != null) query.set(key, String(value));
  const response = await fetch('/api/v1' + path + (query.size ? '?' + query : ''), {headers:{Accept:'application/json'}});
  let body; try { body = await response.json(); } catch { throw new Error('服务响应无法读取'); }
  if (!response.ok || body.code !== 0) throw new Error(body.message || '请求失败');
  return body.data;
}
function errorText(error) { return (error && error.message) || '请求失败，请检查网络'; }
function toggleView(history) {
  $('history-view').hidden = !history; $('stock-view').hidden = history;
  $('history-tab')?.classList.toggle('active', history); $('stock-tab').classList.toggle('active', !history);
  $('history-tab')?.setAttribute('aria-pressed', String(history)); $('stock-tab').setAttribute('aria-pressed', String(!history));
  if ($('broad-view')) $('broad-view').hidden = true;
  if ($('broad-tab')) { $('broad-tab').classList.toggle('active',false); $('broad-tab').setAttribute('aria-pressed','false'); }
}
function clearChart(text = '') {
  chartGuard.next(); chartKey = ''; chartData = null;
  if (chartCleanup) chartCleanup(); chartCleanup = null;
  $('history-chart').replaceChildren(); $('history-chart-state').textContent = text;
}
function renderHistoryChart() {
  document.querySelectorAll('[data-history-metric]').forEach(button => {
    const active = button.dataset.historyMetric === historyMetric;
    button.classList.toggle('active',active); button.setAttribute('aria-pressed',String(active));
  });
  if (!chartData) return;
  const metrics = {
    flow:{key:'estimated_flow',label:'ETF 估算净申赎',unit:'元',kind:'bar',note:'仅展示通过份额、净值身份和事件核验的估算；空白不是零。'},
    nav:{key:'unit_nav',label:'单位净值',unit:'元',kind:'line',decimals:8,signed:false,note:'单位净值原始记录；缺失区间保留空白，不沿用前值。'},
    shares:{key:'shares',label:'基金份额',unit:'份',kind:'line',signed:false,note:'基金份额原始记录；份额变化不等于实际股票买卖资金。'}
  };
  const metric = metrics[historyMetric];
  if (chartCleanup) chartCleanup();
  chartCleanup = renderTimeChart($('history-chart'),chartData.series.map(row=>({date:row.trade_date,value:row[metric.key]})),{
    start:chartData.start,end:chartData.end,tradingDates:chartData.trading_dates,...metric
  });
  $('history-chart-state').textContent = '图表覆盖所选完整范围，共 '+formatNumber(chartData.total)+' 个观测日期；下方表格按页展示。';
}
async function loadChart(detail) {
  const key = [state.code,detail.start,detail.end,detail.version].join('|');
  if (key === chartKey) return;
  clearChart('正在读取完整范围图表…'); chartKey = key;
  const seq = chartGuard.next(), code = state.code;
  try {
    const data = await api('/history/etf/'+encodeURIComponent(code)+'/chart',{start:detail.start,end:detail.end});
    if (!chartGuard.current(seq)) return;
    if (!detail.version || data.version !== detail.version || data.fund?.fund_code !== code || data.start !== detail.start || data.end !== detail.end || !Array.isArray(data.series) || data.series.length > 8000 || data.total !== data.series.length || data.total !== detail.total) {
      throw new Error('图表与明细版本不一致，请刷新数据');
    }
    data.trading_dates = await tradingDates(data.start,data.end);
    if (!chartGuard.current(seq)) return;
    chartData = data; renderHistoryChart();
  } catch (error) {
    if (!chartGuard.current(seq)) return;
    clearChart(); setMessage($('history-chart-state'),'图表加载失败：'+errorText(error),()=>loadHistory(true));
  }
}
function appendStat(target, label, value, suffix) {
  const item = el('div', null, 'history-stat'); item.append(el('span',label), el('strong',formatNumber(value) + suffix)); target.append(item);
}
function applyEntryRange(writeInputs = true) {
  const requested = entryRange || {start:state.targetStart,end:state.targetEnd};
  const valid = validDate(requested.start) && validDate(requested.end) && requested.start <= requested.end;
  const start = requested.start < state.targetStart ? state.targetStart : requested.start;
  const end = requested.end > state.targetEnd ? state.targetEnd : requested.end;
  const available = valid && start <= end;
  state.start = available ? start : requested.start;
  state.end = available ? end : requested.end;
  if (writeInputs) {
    $('history-start').value = state.start || ''; $('history-end').value = state.end || '';
  }
  openingFund = false;
  if (!available) {
    const message = valid ? '宽基所选区间 '+requested.start+' 至 '+requested.end+' 与基金历史可查询范围 '+state.targetStart+' 至 '+state.targetEnd+' 没有交集，请调整日期。' : '传入的宽基日期范围无效，请选择有效的开始和结束日期。';
    clearChart(message); setMessage($('history-rows-state'), message);
    $('history-page').textContent = '未查询'; $('history-prev').disabled = true; $('history-next').disabled = true;
  }
  return available;
}
async function loadMeta() {
  const seq = metaGuard.next();
  metaLoading = true;
  detailItems = null;
  detailGuard.next();
  clearChart(state.code?'正在更新历史范围…':'');
  $('history-apply').disabled = true;
  if (state.code) {
    $('history-rows').replaceChildren(); $('history-coverage').replaceChildren(); $('history-detail-notes').replaceChildren();
    $('history-prev').disabled = true; $('history-next').disabled = true;
    setMessage($('history-rows-state'), '正在更新历史范围…');
  }
  try {
    const data = await api('/history/meta'); if (!metaGuard.current(seq)) return;
    if (!validDate(data.target_start) || !validDate(data.target_end) || data.target_end < data.target_start) throw new Error('历史范围不可用');
    const previousEnd = state.targetEnd;
    const inputsMatch = $('history-start').value === state.start && $('history-end').value === state.end;
    const followLatest = followLatestEnd && state.end === previousEnd && inputsMatch;
    state.targetStart = data.target_start; state.targetEnd = data.target_end;
    $('history-start').min = data.target_start; $('history-start').max = data.target_end;
    $('history-end').min = data.target_start; $('history-end').max = data.target_end;
    let rangeAvailable = true;
    if (openingFund || entryRange || !state.end) {
      rangeAvailable = applyEntryRange(openingFund || inputsMatch);
    } else if (followLatest) {
      state.end = data.target_end; $('history-end').value = state.end;
    }
    metaLoading = false;
    $('history-apply').disabled = false;
    $('history-asof').textContent = '目标范围 ' + data.target_start + ' 至 ' + data.target_end + ' · 覆盖尚未完成';
    const summary = $('history-summary'); summary.replaceChildren();
    appendStat(summary, '目录 ETF', data.total_funds, ' 只'); appendStat(summary, '有份额记录', data.shares_funds, ' 只');
    appendStat(summary, '有净值记录', data.nav_funds, ' 只'); appendStat(summary, '份额观测', data.shares_rows, ' 条'); appendStat(summary, '净值观测', data.nav_rows, ' 条');
    showNotes($('history-notes'), data.notes);
    if (data.generated_at) $('history-notes').append(el('p', '历史数据快照生成时间：' + data.generated_at));
    if (state.code && rangeAvailable) {
      if (state.start < state.targetStart || state.end > state.targetEnd || state.start > state.end) {
        setMessage($('history-rows-state'), '已选范围不在最新可查询范围内，请重新选择日期。');
      } else {
        loadHistory(true);
      }
    }
  } catch (error) {
    if (!metaGuard.current(seq)) return;
    metaLoading = false;
    setMessage($('history-asof'), '覆盖情况更新失败：' + errorText(error), loadMeta);
    if (state.code) setMessage($('history-rows-state'), '历史范围更新失败，请重试。', loadMeta);
  }
}
function renderCatalogItem(item) {
  const button = el('button', null, 'catalog-row'); button.type = 'button';
  const label = el('span'); label.append(el('strong', (item.fund_name || '未命名') + '  ' + item.fund_code));
  const lifecycle = lifecycleNames[item.lifecycle_status] || item.lifecycle_status || '状态未知';
  label.append(el('small', (marketNames[item.market] || item.market || '交易所未知') + ' · ' + lifecycle + ' · 上市 ' + (item.listed_date || '未知')));
  const count = el('span', null, 'catalog-count');
  count.append(el('span', formatNumber(item.shares && item.shares.count) + ' / ' + formatNumber(item.nav && item.nav.count)), el('small','份额 / 净值'));
  button.append(label, count); button.addEventListener('click', () => openFund(item)); return button;
}
async function loadCatalog() {
  const seq = catalogGuard.next(), target = $('etf-catalog-list'); setMessage(target, '正在加载目录…');
  $('catalog-prev').disabled = true; $('catalog-next').disabled = true;
  try {
    const data = await api('/history/etfs', {q:state.query, market:state.market, page:state.catalogPage, page_size:20});
    if (!catalogGuard.current(seq)) return;
    const items = Array.isArray(data.items) ? data.items : []; state.catalogTotal = Number(data.total) || 0;
    target.replaceChildren(); if (!items.length) setMessage(target, '没有匹配的 ETF。'); else items.forEach(item => target.append(renderCatalogItem(item)));
    $('catalog-page').textContent = '第 ' + data.page + ' 页 · 共 ' + formatNumber(data.total) + ' 只';
    $('catalog-prev').disabled = data.page <= 1; $('catalog-next').disabled = data.page * data.page_size >= data.total;
  } catch (error) {
    if (!catalogGuard.current(seq)) return;
    setMessage(target, '目录加载失败：' + errorText(error), loadCatalog); $('catalog-prev').disabled = state.catalogPage <= 1;
  }
}
function openFund(item) {
  if (!/^\d{6}$/.test(item.fund_code || '')) return;
  entryRange = item.return_view !== 'stock' && ('range_start' in item || 'range_end' in item)
    ? {start:item.range_start,end:item.range_end} : null;
  openingFund = true; followLatestEnd = !entryRange;
  state.code = item.fund_code; state.historyPage = 1; state.historyTotal = 0;
  detailGuard.next(); detailItems = null; $('history-rows').replaceChildren();
  $('history-coverage').replaceChildren(); $('history-detail-notes').replaceChildren();
  $('history-prev').disabled = true; $('history-next').disabled = true; $('history-page').textContent = '';
  clearChart('正在读取基金图表…');
  $('history-fund-heading').textContent = (item.fund_name || 'ETF') + '  ' + item.fund_code;
  $('history-fund-subtitle').textContent = (marketNames[item.market] || item.market || '交易所未知') + ' · 历史数据';
  $('history-detail-content').hidden = false; $('history-detail-status').hidden = true;
  if (metaLoading || !state.targetEnd) { setMessage($('history-rows-state'), '等待历史范围信息…'); return; }
  if (applyEntryRange()) loadHistory(true);
  $('history-fund-heading').scrollIntoView({behavior:'smooth',block:'start'});
}
function coverageBlock(title, info) {
  const block = el('div', null, 'coverage-item'); block.append(el('strong', title), el('span', dateRange(info))); return block;
}
function renderHistoryRows() {
  if (!detailItems) return;
  $('history-rows').replaceChildren();
  detailItems.forEach(row => {
    const tr = el('tr'); tr.append(el('td',row.trade_date || '—'), el('td',formatNumber(row.shares, 2),'num'), el('td',formatNumber(row.unit_nav, 8),'num'), el('td',formatNumber(row.acc_nav, 8),'num'), el('td',money(row.estimated_flow),'num'), el('td',qualityNames[row.quality] || '状态未知')); $('history-rows').append(tr);
  });
}
async function loadHistory(refreshChart = false) {
  if (!state.code || !state.targetEnd) return;
  const seq = detailGuard.next(), box = $('history-rows-state');
  detailItems = null;
  if (refreshChart) clearChart('正在读取完整范围图表…');
  $('history-rows').replaceChildren(); $('history-coverage').replaceChildren(); $('history-detail-notes').replaceChildren();
  setMessage(box, '正在加载历史记录…'); $('history-prev').disabled = true; $('history-next').disabled = true;
  try {
    const data = await api('/history/etf/' + encodeURIComponent(state.code), {start:state.start,end:state.end,page:state.historyPage,page_size:100});
    if (!detailGuard.current(seq)) return;
    const fund = data.fund || {}, coverage = data.coverage || {}, items = Array.isArray(data.items) ? data.items : [];
    if (fund.fund_code !== state.code || data.start !== state.start || data.end !== state.end) throw new Error('返回的基金或日期范围与选择不符，请刷新重试');
    $('history-fund-heading').textContent = (fund.fund_name || 'ETF') + '  ' + state.code;
    $('history-fund-subtitle').textContent = (marketNames[fund.market] || fund.market || '交易所未知') + ' · 请求范围 ' + data.start + ' 至 ' + data.end;
    if (entryRange && (entryRange.start !== data.start || entryRange.end !== data.end)) {
      $('history-fund-subtitle').textContent += '（宽基所选 '+entryRange.start+' 至 '+entryRange.end+'，已按可查询范围调整）';
    }
    $('history-coverage').append(coverageBlock('份额记录', coverage.shares), coverageBlock('净值记录', coverage.nav));
    if (coverage.flow) $('history-coverage').append(coverageBlock('ETF 净申赎估算', coverage.flow));
    state.historyTotal = Number(data.total) || 0; $('history-rows').replaceChildren(); box.replaceChildren();
    if (!items.length) setMessage(box, '所选日期范围暂无份额或净值记录。');
    detailItems = items; renderHistoryRows();
    $('history-page').textContent = '第 ' + data.page + ' 页 · 共 ' + formatNumber(data.total) + ' 条';
    $('history-prev').disabled = data.page <= 1; $('history-next').disabled = data.page * data.page_size >= data.total;
    showNotes($('history-detail-notes'), data.notes);
    loadChart(data);
  } catch (error) {
    if (!detailGuard.current(seq)) return;
    clearChart();
    setMessage(box, '历史数据加载失败：' + errorText(error), loadHistory); $('history-prev').disabled = state.historyPage <= 1;
  }
}
$('refresh').addEventListener('click', () => { if (state.code) { resetTradingCalendar(); loadMeta(); } });
$('history-tab')?.addEventListener('click', () => toggleView(true)); $('stock-tab').addEventListener('click', () => toggleView(false));
let queryTimer;
$('etf-query').addEventListener('input', () => {
  clearTimeout(queryTimer); catalogGuard.next(); state.query = $('etf-query').value.trim(); state.catalogPage = 1;
  setMessage($('etf-catalog-list'), '正在搜索…'); queryTimer = setTimeout(loadCatalog, 250);
});
$('etf-query').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); clearTimeout(queryTimer); loadCatalog(); } });
$('etf-market').addEventListener('change', () => { clearTimeout(queryTimer); state.market = $('etf-market').value; state.catalogPage = 1; loadCatalog(); });
$('catalog-prev').addEventListener('click', () => { if (state.catalogPage > 1) { state.catalogPage--; loadCatalog(); } });
$('catalog-next').addEventListener('click', () => { if (state.catalogPage * 20 < state.catalogTotal) { state.catalogPage++; loadCatalog(); } });
$('history-apply').addEventListener('click', () => {
  const start = $('history-start').value, end = $('history-end').value;
  if (!validDate(start) || !validDate(end) || start < state.targetStart || end > state.targetEnd || start > end) {
    clearChart('日期范围无效，请重新选择。');
    detailGuard.next(); detailItems = null; $('history-rows').replaceChildren(); $('history-coverage').replaceChildren(); $('history-detail-notes').replaceChildren();
    $('history-prev').disabled = true; $('history-next').disabled = true;
    setMessage($('history-rows-state'), '请选择 ' + state.targetStart + ' 至 ' + state.targetEnd + ' 内的有效日期范围。'); return;
  }
  entryRange = null; openingFund = false;
  state.start = start; state.end = end; followLatestEnd = false; state.historyPage = 1; loadHistory(true);
});
$('history-prev').addEventListener('click', () => { if (state.historyPage > 1) { state.historyPage--; loadHistory(); revealSection('history-rows-heading'); } });
$('history-next').addEventListener('click', () => { if (state.historyPage * 100 < state.historyTotal) { state.historyPage++; loadHistory(); revealSection('history-rows-heading'); } });
document.querySelectorAll('[data-history-metric]').forEach(button=>button.addEventListener('click',()=>{
  if (!['flow','nav','shares'].includes(button.dataset.historyMetric)) return;
  historyMetric = button.dataset.historyMetric; renderHistoryChart();
}));
document.addEventListener?.('etf-open-history',event=>{
  const item=event.detail;
  if (!item || !/^\d{6}$/.test(item.fund_code || '')) return;
  if($('history-back')){$('history-back').dataset.returnView=item.return_view==='stock'?'stock':'broad';$('history-back').textContent=item.return_view==='stock'?'返回个股观察':'返回宽基指数观察';}
  toggleView(true); openFund(item);
  if (!state.targetEnd && !metaLoading) loadMeta();
});
onMoneyUnitChange(() => { renderHistoryRows(); renderHistoryChart(); });
