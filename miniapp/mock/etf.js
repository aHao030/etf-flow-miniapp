// mock/etf.js —— mock /api/v1/etf/{code} ETF 详情（契约 §2.5 + v1.1 序列补丁）
// 字段与契约一致：share_total/share_change/nav_prev/est_flow/top_stocks/flags/note
// + share_series {trade_date, share_total, share_change}、scale_series {trade_date, scale}。
const BASE = require('./_base');

const INDUSTRY_STOCK_FILTER = {
  // 行业 ETF → 优先展示对应行业个股（构造值）
  '512690': ['801120'],
  '512170': ['801150'],
  '512480': ['801080'],
  '512760': ['801080'],
  '515790': ['801730'],
  '512660': ['801080', '801730'],
  '512880': ['801790'],
  '159928': ['801120', '801110']
};

function pickTopStocks(e, code, rnd) {
  const industries = INDUSTRY_STOCK_FILTER[code] || null;
  let pool = BASE.STOCKS;
  if (industries) {
    pool = BASE.STOCKS.filter(function (s) {
      return industries.indexOf(s.industry) >= 0;
    });
    if (pool.length < 3) pool = BASE.STOCKS.slice(0, 12);
  } else {
    pool = BASE.STOCKS.slice(0, 15);
  }

  const shareChange = BASE.round2((rnd() - 0.45) * 2.4e9 * (e.category === '宽基' ? 1.5 : 0.7));
  const estFlow = BASE.round2(shareChange * e.nav_prev);

  const weights = pool.map(function () {
    return 0.005 + rnd() * 0.05;
  });
  const sum = weights.reduce(function (a, b) {
    return a + b;
  }, 0);

  return pool
    .map(function (s, i) {
      return {
        stock_code: s.code,
        stock_name: s.name,
        flow: BASE.round2((estFlow * weights[i]) / sum),
        weight: BASE.round2(weights[i] / sum)
      };
    })
    .sort(function (a, b) {
      return Math.abs(b.flow) - Math.abs(a.flow);
    })
    .slice(0, 5);
}

// 契约 v1.1：share_series ≤60 项 {trade_date, share_total, share_change}（首日 share_change=null）
function makeShareSeries(e, code, rnd) {
  let prev = e.share_total;
  return BASE.tradingDates(60).map(function (date, i) {
    const delta = i === 0 ? null : Math.round((rnd() - 0.45) * 3.6e9);
    if (delta !== null) prev += delta;
    return {
      trade_date: date,
      share_total: Math.max(0, prev),
      share_change: delta
    };
  });
}

// 契约 v1.1：scale_series ≤60 项 {trade_date, scale}（scale=份额×NAV 元，首日 null）
function makeScaleSeries(e, code, rnd) {
  return BASE.tradingDates(60).map(function (date, i) {
    return {
      trade_date: date,
      scale: i === 0 ? null : BASE.round2((e.share_total + (rnd() - 0.5) * 8e9) * e.nav_prev)
    };
  });
}

function get(code, params) {
  const e = BASE.ETF_INFO[code];
  if (!e) {
    return { code: 40402, message: 'ETF 不在覆盖清单', data: null };
  }

  const rnd = BASE.seeded('etf:' + code + ':' + BASE.LATEST_DATE);
  const shareChange = BASE.round2((rnd() - 0.45) * 2.4e9 * (e.category === '宽基' ? 1.5 : 0.7));
  const estFlow = BASE.round2(shareChange * e.nav_prev);
  const accumulating = e.status === 'accumulating';

  return {
    code: 0,
    message: 'ok',
    data: {
      trade_date: BASE.LATEST_DATE,
      etf_code: code,
      etf_name: e.name,
      exchange: e.exchange,
      category: e.category,
      share_total: e.share_total,
      share_change: shareChange,
      nav_prev: e.nav_prev,
      est_flow: estFlow,
      weight_source: e.weight_source,
      weight_date: e.weight_date,
      coverage: e.coverage,
      top_stocks: accumulating ? [] : pickTopStocks(e, code, rnd),
      note: accumulating ? '深市 ETF：份额历史积累中，当前仅快照数据，估算贡献暂缺' : null,
      flags: accumulating ? ['share_gap'] : [],
      // —— 契约 v1.1：share_series / scale_series ——
      share_series: makeShareSeries(e, code, rnd),
      scale_series: makeScaleSeries(e, code, rnd)
    }
  };
}

module.exports = { get };
