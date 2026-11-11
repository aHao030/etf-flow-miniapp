// mock/index.js —— mock 路由：按契约路径分发，返回完整信封 {code, message, data}
const meta = require('./meta');
const rank = require('./rank');
const stock = require('./stock');
const etf = require('./etf');
const industries = require('./industries');
const etfs = require('./etfs');
const stocks = require('./stocks');

function route(path, params) {
  const seg = path.replace(/^\//, '').split('/');
  const head = seg[0];
  if (head === 'meta') return meta.get();
  if (head === 'health') return meta.health();
  if (head === 'rank') return rank.get(params);
  if (head === 'stocks') return stocks.get(params);
  if (head === 'stock') return stock.get(seg[1], params);
  if (head === 'etf') return etf.get(seg[1], params);
  if (head === 'industries') return industries.get();
  if (head === 'etfs') return etfs.get(params);
  return { code: 40401, message: 'mock 未实现的接口：' + path, data: null };
}

module.exports = { route };
