// mock/industries.js —— mock /api/v1/industries（契约 §2.6）
const BASE = require('./_base');

function get() {
  const items = BASE.INDUSTRIES.map(function (ind) {
    const count = BASE.STOCKS.filter(function (s) {
      return s.industry === ind.industry_code;
    }).length;
    return {
      industry_code: ind.industry_code,
      industry_name: ind.industry_name,
      stock_count: count
    };
  });
  return {
    code: 0,
    message: 'ok',
    data: {
      version: 'mock-20261029',
      updated_date: BASE.LATEST_DATE,
      items: items
    }
  };
}

module.exports = { get };
