const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname, '../src/pages/fund/index.js'), 'utf8');
const meta = { version: 'H1', target_start: '2026-11-02', target_end: '2026-11-10' };
const dates = ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-09', '2026-11-10'];
const records = [
  { trade_date: dates[0], shares: 10000, unit_nav: 2, estimated_flow: 0, quality: 'estimated' },
  { trade_date: dates[2], shares: 16000, unit_nav: null, estimated_flow: 12000, quality: 'estimated' },
  { trade_date: dates[6], shares: null, unit_nav: 2.12345678, estimated_flow: null, quality: 'missing_current_shares' }
];
const options = { code: '510300', name: '沪深300ETF', start: '2026-11-01', end: '2026-11-10', unit: 'wan', history_version: 'H1' };
const tick = () => new Promise(resolve => setImmediate(resolve));

function standard(route, params) {
  if (route === 'history/meta') return meta;
  const { start, end } = params;
  if (route === 'broad/calendar') return { start, end, dates: dates.filter(d => d >= start && d <= end) };
  const rows = records.filter(row => row.trade_date >= start && row.trade_date <= end);
  const shared = { start, end, version: 'H1', fund: { fund_code: '510300', fund_name: '沪深300ETF' }, total: rows.length };
  if (route === 'history/etf/510300/chart') return { ...shared, series: rows };
  if (route === 'history/etf/510300') {
    return { ...shared, page: params.page, page_size: params.page_size, items: rows.map(row => ({ ...row, acc_nav: null })) };
  }
  throw new Error('Unexpected route ' + route);
}

function pageWith(fetchData = standard, mode='snapshot') {
  let definition;
  const calls = [];
  const modules = {
    '../../lib/data': {sourceInfo:()=>({mode}), get: async (route, params) => { calls.push({ route, params }); return fetchData(route, params); } },
    '../../lib/chart': { chartModel: (rows, opts) => ({ rows, opts }) },
    '../../lib/format': {
      money: (value, unit) => value == null ? '—' : value + ' ' + unit,
      number: value => value == null ? '—' : String(value)
    },
    '../../lib/platform': { back() {} }
  };
  vm.runInNewContext(source, {
    require(id) { assert.ok(modules[id], 'Unexpected import ' + id); return modules[id]; },
    Page(page) { definition = page; }
  });
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  return { page, calls };
}
function dateChange(page, field, value) {
  page.changeDate({ currentTarget: { dataset: { field } }, detail: { value } });
}
function metric(page, name) {
  page.selectMetric({ currentTarget: { dataset: { metric: name } } });
}

test('clipped inherited dates, true sessions and missing/zero values survive metric changes', async () => {
  const { page, calls } = pageWith();
  await page.onLoad(options);
  assert.equal(page.data.start, dates[0]);
  assert.equal(page.data.end, dates[6]);
  assert.match(page.data.rangeNote, /2026-11-01 至 2026-11-10/);
  assert.equal(page.data.summaryValue, '12000 wan');
  assert.match(page.data.coverage, /7 个开市日，有值 2 日/);
  assert.deepEqual(Array.from(page.data.chart.rows, row => row.value), [0, null, 12000, null, null, null, null]);
  assert.equal(page.data.chart.opts.currency, true);
  metric(page, 'nav');
  assert.equal(page.data.chart.opts.currency, false);
  assert.equal(page.data.summaryValue, '2.12345678 元');
  metric(page, 'shares');
  assert.equal(page.data.summaryValue, '16000 份');
  assert.equal(page.data.chart.rows[6].value, null);
  assert.equal(calls.length, 3, 'switching a metric uses the same aligned snapshot');
  await page.toggleDetails();
  assert.equal(page.data.detailRows[0].flow, '0 wan');
  assert.equal(page.data.detailRows[2].shares, '—');
  assert.equal(page.data.detailRows[2].quality, '当日份额缺失');
});

test('nonoverlapping or malformed inherited dates require a new explicit query', async () => {
  for (const range of [
    { start: '2019-01-01', end: '2020-03-01' },
    { start: '2026-02-30', end: dates[2] }, { start: dates[2], end: '' }
  ]) {
    const { page, calls } = pageWith();
    await page.onLoad({ ...options, ...range });
    assert.equal(calls.length, 1);
    assert.equal(page.data.ready, false);
    assert.match(page.data.error, /调整日期后应用/);
    dateChange(page, 'draftStart', dates[0]); dateChange(page, 'draftEnd', dates[2]);
    await page.applyDates();
    assert.equal(page.data.ready, true);
    assert.equal(page.data.start, dates[0]);
    assert.equal(page.data.end, dates[2]);
  }
});

test('version mismatch and a wrong fund identity do not display earlier values', async () => {
  const { page, calls } = pageWith((route, params) => {
    const result = standard(route, params);
    if (route.endsWith('/chart')) result.fund = { fund_code: '159999' };
    return result;
  });
  await page.onLoad({ ...options, history_version: 'other' });
  assert.equal(calls.length, 1);
  assert.equal(page.data.ready, false);
  assert.match(page.data.error, /版本已变化/);
  await page.onLoad(options);
  assert.equal(page.data.ready, false);
  assert.equal(page.data.chart, null);
  assert.match(page.data.error, /不一致/);
});

test('a stale handoff can open the newer history, and foreground refresh preserves the applied and draft ranges',async()=>{
  let revision='H2',calendarVersion='B2',calendarHistory='H2',targetEnd=dates[6];
  const {page,calls}=pageWith((route,params)=>{
    if(route==='history/meta')return {...meta,version:revision,target_end:targetEnd};
    if(route==='broad/meta')return {version:calendarVersion,history_version:calendarHistory,target_end:targetEnd};
    if(route==='broad/calendar')return {...standard(route,params),version:calendarVersion};
    return {...standard(route,params),version:revision};
  },'live');
  await page.onLoad(options);await page.onShow();assert.equal(calls.length,1);
  assert.equal(page.data.needsNewVersion,true);assert.equal(page.data.retryable,true);assert.equal(page.data.chart,null);
  await page.retry();assert.equal(page.data.ready,true);assert.equal(page.data.end,dates[6]);
  dateChange(page,'draftStart',dates[1]);dateChange(page,'draftEnd',dates[4]);await page.applyDates();
  dateChange(page,'draftStart',dates[2]);revision='H3';calendarHistory=revision;calendarVersion='B3';targetEnd='2026-11-11';
  page.onHide();await page.onShow();
  assert.equal(page.data.start,dates[1]);assert.equal(page.data.end,dates[4]);assert.equal(page.data.draftStart,dates[2]);
  assert.equal(page.data.datePending,true);assert.equal(page.data.maxDate,targetEnd);
  calendarHistory='old';await page.retry();
  assert.equal(page.data.chart,null);assert.match(page.data.chartNotice,/未对齐/);
  assert.equal(page.data.summaryValue,'');
});

test('a late chart or detail request cannot restore data after a newer invalid range', async () => {
  let waitChart = false, waitDetail = false, settleRequest;
  const { page } = pageWith((route, params) => {
    const result = standard(route, params);
    if (waitChart && route.endsWith('/chart') || waitDetail && route === 'history/etf/510300') {
      return new Promise(resolve => { settleRequest = () => resolve(result); });
    }
    return result;
  });
  await page.onLoad(options);
  waitDetail = true;
  const detail = page.toggleDetails();
  await tick();
  dateChange(page, 'draftStart', dates[6]); dateChange(page, 'draftEnd', dates[0]);
  await page.applyDates(); settleRequest(); await detail;
  assert.equal(page.data.detailRows.length, 0);
  assert.equal(page.data.ready, false);
  waitDetail = false; waitChart = true;
  dateChange(page, 'draftStart', dates[0]);
  const pending = page.applyDates();
  await tick();
  dateChange(page, 'draftStart', dates[6]);
  await page.applyDates(); settleRequest(); await pending;
  assert.equal(page.data.ready, false);
  assert.equal(page.data.chart, null);
  assert.match(page.data.error, /有效起止日期/);
});

test('calendar failure keeps validated dated observations accessible and rejects a mismatched detail snapshot', async () => {
  const { page } = pageWith((route, params) => {
    const data = standard(route, params);
    if (route === 'broad/calendar') throw new Error('交易日历暂未取得。');
    if (route === 'history/etf/510300') data.items[0].shares = 99999;
    return data;
  });
  await page.onLoad(options);
  assert.equal(page.data.ready, true);
  assert.equal(page.data.chart, null);
  assert.match(page.data.chartNotice, /交易日历/);
  await page.toggleDetails();
  assert.match(page.data.detailError, /不一致/);
  assert.equal(page.data.detailRows.length, 0);
});

test('a weekend remains empty and a missing flow stays separate from an available NAV', async () => {
  const { page } = pageWith();
  await page.onLoad({ ...options, start: '2026-11-07', end: '2026-11-08' });
  assert.match(page.data.chartNotice, /无开市日/);
  assert.equal(page.data.chart, null);
  dateChange(page, 'draftStart', dates[6]); dateChange(page, 'draftEnd', dates[6]);
  await page.applyDates();
  assert.match(page.data.chartNotice, /暂无可核验/);
  assert.equal(page.data.summaryValue, '');
  metric(page, 'nav');
  assert.equal(page.data.summaryValue, '2.12345678 元');
});

test('offline history limits and missing fund data remain explicit and retryable', async () => {
  let missing = true;
  const { page, calls } = pageWith((route, params) => {
    if (route === 'history/meta') return { ...meta, offline: true };
    if (route.endsWith('/chart') && missing) throw new Error('基金未收录。');
    return standard(route, params);
  });
  await page.onLoad(options);
  assert.equal(page.data.retryable, true);
  assert.match(page.data.error, /离线包.*基金未收录/);
  assert.equal(page.data.chart, null);
  missing = false;
  await page.retry();
  assert.equal(calls.filter(call => call.route === 'history/meta').length, 2);
  assert.equal(page.data.ready, true);
  assert.equal(page.data.start, meta.target_start);
});

test('the second detail page uses the applied interval and clears previous rows while loading', async () => {
  const historyDates = Array.from({ length: 21 }, (_, i) =>
    new Date(Date.UTC(2026, 9, i + 1)).toISOString().slice(0, 10));
  const history = historyDates.map(date => ({ ...records[0], trade_date: date }));
  const first = historyDates[0], last = historyDates[20];
  const { page, calls } = pageWith((route, params) => {
    if (route === 'history/meta') return { ...meta, target_start: first, target_end: last };
    if (route === 'broad/calendar') return { start: first, end: last, dates: historyDates };
    const common = { fund: { fund_code: options.code }, version: meta.version, start: first, end: last, total: 21 };
    if (route.endsWith('/chart')) return { ...common, series: history };
    return { ...common, page: params.page, page_size: 20, items: history.slice((params.page - 1) * 20, params.page * 20)
      .map(row => ({ ...row, acc_nav: 2 })) };
  });
  await page.onLoad({ ...options, start: first, end: last });
  await page.toggleDetails();
  assert.equal(page.data.detailRows.length, 20);
  const pending = page.turnPage({ currentTarget: { dataset: { step: '1' } } });
  assert.equal(page.data.detailRows.length, 0);
  await pending;
  assert.equal(page.data.detailRows.length, 1);
  assert.equal(page.data.detailRows[0].date, last);
  assert.equal(page.data.detailPage, 2);
  assert.equal(calls.at(-1).params.start, first);
  assert.equal(calls.at(-1).params.end, last);
});
