// mock/etfs.js —— mock /api/v1/etfs 覆盖清单（契约 §2.7）
// 数据取自 model/etf_universe.csv（行业/主题 + 主流宽基，共 19 只）。
const BASE = require('./_base');

function get(params) {
  const category = params.category || '';
  const page = Math.max(1, parseInt(params.page || '1', 10));
  const pageSize = Math.min(100, Math.max(1, parseInt(params.page_size || '20', 10)));

  let items = Object.keys(BASE.ETF_INFO).map(function (code) {
    const e = BASE.ETF_INFO[code];
    return {
      etf_code: code,
      etf_name: e.name,
      category: e.category,
      exchange: e.exchange,
      status: e.status,
      listing_date: e.listing_date,
      weight_source: e.weight_source,
      weight_date: e.weight_date
    };
  });

  if (category) {
    items = items.filter(function (it) {
      return it.category === category;
    });
  }

  const total = items.length;
  const start = (page - 1) * pageSize;
  const pageItems = items.slice(start, start + pageSize);

  return {
    code: 0,
    message: 'ok',
    data: {
      trade_date: BASE.LATEST_DATE,
      total: total,
      page: page,
      page_size: pageSize,
      has_more: start + pageSize < total,
      items: pageItems
    }
  };
}

module.exports = { get };
