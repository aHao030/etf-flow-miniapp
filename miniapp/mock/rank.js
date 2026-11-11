// mock/rank.js —— mock /api/v1/rank 榜单（契约 §2.3）
// 字段：rank/stock_code/stock_name/industry/industry_name/flow/intensity_bp/valid_days/flags
const BASE = require('./_base');

function get(params) {
  const period = ['5d', '20d'].indexOf(params.period) >= 0 ? params.period : '1d';
  const industry = params.industry || '';
  const page = Math.max(1, parseInt(params.page || '1', 10));
  const pageSize = Math.min(100, Math.max(1, parseInt(params.page_size || '20', 10)));

  let list = BASE.STOCKS.map(function (s) {
    return {
      rank: 0,
      stock_code: s.code,
      stock_name: s.name,
      industry: s.industry,
      industry_name: s.industry_name,
      flow: period === '5d' ? s.f5 : period === '20d' ? s.f20 : s.f1,
      intensity_bp: period === '5d' ? s.i5 : period === '20d' ? s.i20 : s.i1,
      valid_days: period === '5d' ? s.v5 : period === '20d' ? s.v20 : '1/1',
      flags: s.flags || []
    };
  });

  if (industry) {
    list = list.filter(function (it) {
      return it.industry === industry;
    });
  }

  const direction = params.direction === 'out' ? 'out' : 'in';
  list = list.filter(function (it) { return direction === 'out' ? it.flow < 0 : it.flow > 0; });
  list.sort(function (a, b) { return direction === 'out' ? a.flow - b.flow : b.flow - a.flow; });
  list.forEach(function (it, i) {
    it.rank = i + 1;
  });

  const total = list.length;
  const start = (page - 1) * pageSize;
  const items = list.slice(start, start + pageSize);

  let industryName = '全市场';
  if (industry) {
    const found = BASE.INDUSTRIES.filter(function (ind) {
      return ind.industry_code === industry;
    })[0];
    if (!found) {
      return { code: 40403, message: '行业不存在', data: null };
    }
    industryName = found.industry_name;
  }

  return {
    code: 0,
    message: 'ok',
    data: {
      trade_date: BASE.LATEST_DATE,
      period: period,
      industry: industry,
      industry_name: industryName,
      direction: direction,
      total: total,
      page: page,
      page_size: pageSize,
      has_more: start + pageSize < total,
      items: items
    }
  };
}

module.exports = { get };
