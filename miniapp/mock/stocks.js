// Mock search uses the demonstration stock catalog only.
const BASE = require('./_base');
function get(params) {
  const q = String(params.q || '').trim().toLowerCase();
  const page = Math.max(1, parseInt(params.page || '1', 10));
  const pageSize = Math.min(100, Math.max(1, parseInt(params.page_size || '20', 10)));
  const catalog = BASE.STOCKS.concat([{ code: '000000', name: '模拟未覆盖股票', industry_name: '未分类', f1: null }]);
  const matches = catalog.filter(s => !q || s.code.includes(q) || s.name.toLowerCase().includes(q));
  const start = (page - 1) * pageSize;
  return { code: 0, message: 'ok', data: {
    trade_date: BASE.LATEST_DATE, total: matches.length, page,
    has_more: start + pageSize < matches.length,
    items: matches.slice(start, start + pageSize).map(s => ({
      stock_code: s.code, stock_name: s.name, industry_name: s.industry_name,
      data_status: s.code === '000000' ? 'no_data' : 'sse_only', flow_1d: s.f1
    }))
  } };
}
module.exports = { get };
