// mock/stock.js —— mock /api/v1/stock/{code} 个股详情（契约 §2.4）
const BASE = require('./_base');

function makeSeries(s, rnd) {
  const dates = BASE.tradingDates(60);
  const series = dates.map(function (date) {
    const ratio = 0.25 + rnd() * 1.1;
    const sign = rnd() < 0.3 ? -1 : 1;
    const flow = BASE.round2(s.f1 * ratio * sign);
    return {
      trade_date: date,
      flow: flow,
      intensity_bp: BASE.round2((flow / s.float_mcap) * 10000)
    };
  });
  // 末位与指标卡对齐
  series[series.length - 1] = {
    trade_date: BASE.LATEST_DATE,
    flow: s.f1,
    intensity_bp: s.i1
  };
  return series;
}

function makeTopEtfs(s, rnd, periodFlow, validDays, paramsPeriod) {
  const pool = BASE.INDUSTRY_ETF_POOL[s.industry] || BASE.DEFAULT_ETF_POOL;
  const total = periodFlow;
  const fracs = pool.map(function () {
    return 0.05 + rnd() * 0.45;
  });
  const sum = fracs.reduce(function (a, b) {
    return a + b;
  }, 0);
  return pool
    .map(function (code, i) {
      const e = BASE.ETF_INFO[code];
      const flow = BASE.round2((total * fracs[i]) / sum);
      return {
        etf_code: code,
        etf_name: e.name,
        flow: flow,
        share_change: Math.round(flow / e.nav_prev),
        nav: e.nav_prev,
        weight: BASE.round2(0.002 + rnd() * 0.05),
        weight_source: e.weight_source,
        weight_date: e.weight_date,
        valid_days: validDays,
        status: validDays === parseInt(paramsPeriod, 10) ? 'complete' : 'partial'
      };
    })
    .sort(function (a, b) {
      return Math.abs(b.flow) - Math.abs(a.flow);
    })
    .slice(0, 5);
}

function get(code, params) {
  if (code === '000000') {
    const period = ['1d', '5d', '20d'].includes(params.period) ? params.period : '1d';
    const windowDays = parseInt(period, 10);
    return { code: 0, message: 'ok', data: {
      trade_date: BASE.LATEST_DATE, stock_code: code, stock_name: '模拟未覆盖股票',
      industry_name: '未分类', float_mcap: null, flow_1d: null, flow_5d: null, flow_20d: null,
      intensity_1d_bp: null, intensity_5d_bp: null, intensity_20d_bp: null,
      valid_days: { d1: '0/1', d5: '0/5', d20: '0/20' }, series: [], quality: {},
      data_status: 'no_data', note: '模拟数据', etf_contributions: [],
      movement: { period, net_flow: null, inflow: null, outflow: null, etf_count: 0,
        inflow_etf_count: 0, outflow_etf_count: 0, missing_etf_count: 0,
        valid_days: 0, window_days: windowDays, complete: false },
      coverage: { tracked_etfs: 0, active_etfs: 0, contributing_etfs: 0,
        pending_etfs: 0, note: '模拟数据，仅用于演示。' },
      history_note: '模拟数据，历史数据使用当前权重回溯。'
    } };
  }
  const s = BASE.STOCKS.filter(function (x) {
    return x.code === code;
  })[0];
  if (!s) {
    return { code: 40401, message: '股票不存在或不在覆盖池', data: null };
  }

  const rnd = BASE.seeded(code + ':' + BASE.LATEST_DATE);
  const series = makeSeries(s, rnd);
  const period = ['1d', '5d', '20d'].includes(params.period) ? params.period : '1d';
  const windowDays = parseInt(period, 10);
  const validDays = period === '5d' ? parseInt(s.v5, 10) : period === '20d' ? parseInt(s.v20, 10) : 1;
  const periodFlow = period === '5d' ? s.f5 : period === '20d' ? s.f20 : s.f1;
  const topEtfs = makeTopEtfs(s, rnd, periodFlow, validDays, period);
  const inflow = BASE.round2(topEtfs.reduce((sum, e) => sum + (e.flow > 0 ? e.flow : 0), 0));
  const outflow = BASE.round2(topEtfs.reduce((sum, e) => sum + (e.flow < 0 ? e.flow : 0), 0));

  // 深市个股（000/002/300 开头）：深市份额历史积累中 → 仅沪市贡献（契约 §2.4 data_status）
  const isSzStock = /^(000|002|300|301)/.test(code);
  const dataStatus = isSzStock ? 'sse_only' : 'ok';
  const note = isSzStock ? '深市 ETF 份额历史积累中：当前仅有沪市贡献' : null;

  const weightSource = s.qws || 'quarterly';
  const qualityNote =
    weightSource === 'index'
      ? '权重取自中证指数官网（最新发布日）'
      : '权重取自 2026Q3 基金季报（滞后约 1 个月）';

  return {
    code: 0,
    message: 'ok',
    data: {
      trade_date: BASE.LATEST_DATE,
      stock_code: s.code,
      stock_name: s.name,
      industry: s.industry,
      industry_name: s.industry_name,
      float_mcap: s.float_mcap,
      flow_1d: s.f1,
      flow_5d: s.f5,
      flow_20d: s.f20,
      intensity_1d_bp: s.i1,
      intensity_5d_bp: s.i5,
      intensity_20d_bp: s.i20,
      valid_days: { d1: '1/1', d5: s.v5, d20: s.v20 },
      series: series,
      top_etfs: topEtfs,
      etf_contributions: topEtfs,
      movement: {
        period, net_flow: periodFlow, inflow, outflow, etf_count: topEtfs.length,
        inflow_etf_count: topEtfs.filter(e => e.flow > 0).length,
        outflow_etf_count: topEtfs.filter(e => e.flow < 0).length,
        missing_etf_count: validDays < windowDays ? topEtfs.length : 0,
        valid_days: validDays, window_days: windowDays, complete: validDays === windowDays
      },
      coverage: { tracked_etfs: topEtfs.length, active_etfs: topEtfs.length,
        contributing_etfs: topEtfs.length, pending_etfs: 0, note: '模拟数据，仅用于演示。' },
      history_note: '模拟数据，历史数据使用当前权重回溯。',
      quality: {
        weight_source: weightSource,
        coverage: s.coverage || 0.96,
        flags: s.flags || [],
        note: qualityNote
      },
      data_status: dataStatus,
      note: note
    }
  };
}

module.exports = { get };
