// mock/_base.js —— mock 共享基准数据
// 数值为构造样例（量级参考 M1 模型输出），非真实行情；仅供 M3 骨架阶段 UI 演示。
// 字段命名与取值形状严格对齐 docs/m2-api-契约.md §2。

const LATEST_DATE = '2026-10-29';

// 行业字典（申万一级，代码为常用申万一级代码；联调以契约 §2.6 后端下发为准）
const INDUSTRIES = [
  { industry_code: '801010', industry_name: '农林牧渔' },
  { industry_code: '801050', industry_name: '有色金属' },
  { industry_code: '801080', industry_name: '电子' },
  { industry_code: '801110', industry_name: '家用电器' },
  { industry_code: '801120', industry_name: '食品饮料' },
  { industry_code: '801150', industry_name: '医药生物' },
  { industry_code: '801160', industry_name: '公用事业' },
  { industry_code: '801170', industry_name: '交通运输' },
  { industry_code: '801180', industry_name: '房地产' },
  { industry_code: '801200', industry_name: '商贸零售' },
  { industry_code: '801710', industry_name: '建筑材料' },
  { industry_code: '801730', industry_name: '电力设备' },
  { industry_code: '801750', industry_name: '计算机' },
  { industry_code: '801780', industry_name: '银行' },
  { industry_code: '801790', industry_name: '非银金融' },
  { industry_code: '801880', industry_name: '汽车' }
];

// 榜单基准个股：code/name/industry/industry_name/float_mcap(元)/f1/f5/f20(元)/i1/i5/i20(bp)/v5/v20/flags/qws/coverage
const STOCKS = [
  { code: '300750', name: '宁德时代', industry: '801730', industry_name: '电力设备', float_mcap: 9.5e11, f1: 3.42e8, f5: 1.51e9, f20: 4.86e9, i1: 3.60, i5: 15.89, i20: 51.16, v5: '5/5', v20: '20/20', flags: [] },
  { code: '688981', name: '中芯国际', industry: '801080', industry_name: '电子', float_mcap: 5.6e11, f1: 4.58e8, f5: 2.02e9, f20: 5.9e9, i1: 8.18, i5: 36.07, i20: 105.36, v5: '5/5', v20: '20/20', flags: [] },
  { code: '300059', name: '东方财富', industry: '801790', industry_name: '非银金融', float_mcap: 2.9e11, f1: 2.92e8, f5: 9.6e8, f20: 2.8e9, i1: 10.07, i5: 33.10, i20: 96.60, v5: '5/5', v20: '20/20', flags: [] },
  { code: '000001', name: '平安银行', industry: '801780', industry_name: '银行', float_mcap: 2.3e11, f1: 2.21e8, f5: 8.8e8, f20: 2.4e9, i1: 9.61, i5: 38.30, i20: 104.30, v5: '5/5', v20: '20/20', flags: [] },
  { code: '601318', name: '中国平安', industry: '801790', industry_name: '非银金融', float_mcap: 9.8e11, f1: 2.85e8, f5: 1.3e9, f20: 3.9e9, i1: 2.91, i5: 13.30, i20: 39.80, v5: '5/5', v20: '20/20', flags: [] },
  { code: '600519', name: '贵州茅台', industry: '801120', industry_name: '食品饮料', float_mcap: 1.8e12, f1: 2.12e8, f5: 8.6e8, f20: 3.2e9, i1: 1.18, i5: 4.78, i20: 17.80, v5: '5/5', v20: '20/20', flags: [] },
  { code: '002594', name: '比亚迪', industry: '801880', industry_name: '汽车', float_mcap: 8.6e11, f1: 2.41e8, f5: 1.1e9, f20: 3.3e9, i1: 2.80, i5: 12.80, i20: 38.40, v5: '5/5', v20: '20/20', flags: [] },
  { code: '601899', name: '紫金矿业', industry: '801050', industry_name: '有色金属', float_mcap: 4.1e11, f1: 1.82e8, f5: 7.5e8, f20: 2.6e9, i1: 4.44, i5: 18.30, i20: 63.40, v5: '5/5', v20: '20/20', flags: [] },
  { code: '601012', name: '隆基绿能', industry: '801730', industry_name: '电力设备', float_mcap: 1.4e11, f1: 1.71e8, f5: 6.2e8, f20: 1.9e9, i1: 12.20, i5: 44.30, i20: 135.70, v5: '5/5', v20: '20/20', flags: ['large_flow'] },
  { code: '688041', name: '海光信息', industry: '801080', industry_name: '电子', float_mcap: 2.2e11, f1: 1.62e8, f5: 5.9e8, f20: 1.7e9, i1: 7.36, i5: 26.80, i20: 77.30, v5: '5/5', v20: '20/20', flags: [] },
  { code: '600030', name: '中信证券', industry: '801790', industry_name: '非银金融', float_mcap: 3.2e11, f1: 2.18e8, f5: 7.1e8, f20: 2.2e9, i1: 6.81, i5: 22.20, i20: 68.80, v5: '5/5', v20: '20/20', flags: [] },
  { code: '002475', name: '立讯精密', industry: '801080', industry_name: '电子', float_mcap: 2.9e11, f1: 1.53e8, f5: 6.6e8, f20: 2.1e9, i1: 5.28, i5: 22.80, i20: 72.40, v5: '5/5', v20: '20/20', flags: [] },
  { code: '000333', name: '美的集团', industry: '801110', industry_name: '家用电器', float_mcap: 5.2e11, f1: 1.42e8, f5: 5.4e8, f20: 1.6e9, i1: 2.73, i5: 10.40, i20: 30.80, v5: '5/5', v20: '20/20', flags: [] },
  { code: '600036', name: '招商银行', industry: '801780', industry_name: '银行', float_mcap: 1.0e12, f1: 1.88e8, f5: 7.9e8, f20: 2.5e9, i1: 1.88, i5: 7.90, i20: 25.00, v5: '5/5', v20: '20/20', flags: [] },
  { code: '600900', name: '长江电力', industry: '801160', industry_name: '公用事业', float_mcap: 6.9e11, f1: 1.21e8, f5: 4.6e8, f20: 1.5e9, i1: 1.75, i5: 6.67, i20: 21.70, v5: '5/5', v20: '20/20', flags: [] },
  { code: '688256', name: '寒武纪', industry: '801080', industry_name: '电子', float_mcap: 2.9e11, f1: 1.98e8, f5: 8.2e8, f20: 2.7e9, i1: 6.83, i5: 28.30, i20: 93.10, v5: '5/5', v20: '20/20', flags: ['weight_stale'], qws: 'quarterly', coverage: 0.91 },
  { code: '603259', name: '药明康德', industry: '801150', industry_name: '医药生物', float_mcap: 1.8e11, f1: 1.31e8, f5: 4.8e8, f20: 1.4e9, i1: 7.28, i5: 26.70, i20: 77.80, v5: '5/5', v20: '20/20', flags: [] },
  { code: '000858', name: '五粮液', industry: '801120', industry_name: '食品饮料', float_mcap: 5.2e11, f1: 1.12e8, f5: 4.1e8, f20: 1.2e9, i1: 2.15, i5: 7.88, i20: 23.10, v5: '5/5', v20: '20/20', flags: [] },
  { code: '002371', name: '北方华创', industry: '801080', industry_name: '电子', float_mcap: 2.0e11, f1: 1.02e8, f5: 3.9e8, f20: 1.1e9, i1: 5.10, i5: 19.50, i20: 55.00, v5: '5/5', v20: '20/20', flags: [] },
  { code: '000725', name: '京东方A', industry: '801080', industry_name: '电子', float_mcap: 1.6e11, f1: 1.08e8, f5: 3.5e8, f20: 1.0e9, i1: 6.75, i5: 21.90, i20: 62.50, v5: '5/5', v20: '20/20', flags: [] },
  { code: '600276', name: '恒瑞医药', industry: '801150', industry_name: '医药生物', float_mcap: 3.2e11, f1: 9.2e7, f5: 3.3e8, f20: 9.8e8, i1: 2.88, i5: 10.30, i20: 30.60, v5: '5/5', v20: '20/20', flags: [] },
  { code: '000651', name: '格力电器', industry: '801110', industry_name: '家用电器', float_mcap: 2.4e11, f1: 8.1e7, f5: 2.9e8, f20: 8.8e8, i1: 3.38, i5: 12.10, i20: 36.70, v5: '5/5', v20: '20/20', flags: [] },
  { code: '000100', name: 'TCL科技', industry: '801080', industry_name: '电子', float_mcap: 8.5e10, f1: 9.3e7, f5: 2.6e8, f20: 7.6e8, i1: 10.90, i5: 30.60, i20: 89.40, v5: '5/5', v20: '20/20', flags: [] },
  { code: '600809', name: '山西汾酒', industry: '801120', industry_name: '食品饮料', float_mcap: 2.6e11, f1: 6.2e7, f5: 2.2e8, f20: 6.6e8, i1: 2.38, i5: 8.46, i20: 25.40, v5: '5/5', v20: '20/20', flags: [] },
  { code: '603501', name: '韦尔股份', industry: '801080', industry_name: '电子', float_mcap: 1.2e11, f1: 7.8e7, f5: 2.4e8, f20: 7.2e8, i1: 6.50, i5: 20.00, i20: 60.00, v5: '5/5', v20: '20/20', flags: [] },
  { code: '002714', name: '牧原股份', industry: '801010', industry_name: '农林牧渔', float_mcap: 2.0e11, f1: 7.1e7, f5: 2.1e8, f20: 6.2e8, i1: 3.55, i5: 10.50, i20: 31.00, v5: '5/5', v20: '20/20', flags: [] },
  { code: '300498', name: '温氏股份', industry: '801010', industry_name: '农林牧渔', float_mcap: 1.2e11, f1: 4.2e7, f5: 1.3e8, f20: 3.9e8, i1: 3.50, i5: 10.80, i20: 32.50, v5: '5/5', v20: '20/20', flags: [] },
  { code: '601888', name: '中国中免', industry: '801200', industry_name: '商贸零售', float_mcap: 1.7e11, f1: -1.1e7, f5: 2.3e7, f20: 9.8e7, i1: -0.06, i5: 0.14, i20: 0.58, v5: '5/5', v20: '20/20', flags: [] },
  { code: '002271', name: '东方雨虹', industry: '801710', industry_name: '建筑材料', float_mcap: 3.1e10, f1: 3.1e7, f5: 9.5e7, f20: 2.7e8, i1: 10.00, i5: 30.60, i20: 87.10, v5: '4/5', v20: '20/20', flags: [] },
  { code: '000002', name: '万科A', industry: '801180', industry_name: '房地产', float_mcap: 9.0e10, f1: -3.2e7, f5: 1.1e7, f20: 4.2e7, i1: -3.56, i5: 1.22, i20: 4.67, v5: '3/5', v20: '20/20', flags: ['share_gap'] }
];

// ETF 覆盖清单（取自 model/etf_universe.csv，18 只；share_total 份、nav_prev 元为构造值）
const ETF_INFO = {
  '510050': { name: '上证50ETF', exchange: 'SSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 2.95, share_total: 6.5e10, listing_date: '2005-02-23', status: 'active' },
  '510300': { name: '沪深300ETF', exchange: 'SSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 4.05, share_total: 9.8e10, listing_date: '2012-05-28', status: 'active' },
  '510500': { name: '中证500ETF', exchange: 'SSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 6.2, share_total: 3.2e10, listing_date: '2013-02-06', status: 'active' },
  '512100': { name: '中证1000ETF', exchange: 'SSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 2.6, share_total: 1.8e10, listing_date: '2016-09-29', status: 'active' },
  '563360': { name: 'A500ETF', exchange: 'SSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 1.05, share_total: 1.1e10, listing_date: '2024-10-15', status: 'active' },
  '588000': { name: '科创50ETF', exchange: 'SSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 1.12, share_total: 8.6e10, listing_date: '2020-11-16', status: 'active' },
  '512170': { name: '医疗ETF', exchange: 'SSE', category: '行业', weight_source: 'quarterly', weight_date: '2026-09-30', coverage: 0.97, nav_prev: 0.42, share_total: 3.5e10, listing_date: '2019-06-17', status: 'active' },
  '512480': { name: '半导体ETF', exchange: 'SSE', category: '行业', weight_source: 'quarterly', weight_date: '2026-09-30', coverage: 0.96, nav_prev: 1.05, share_total: 2.9e10, listing_date: '2019-06-12', status: 'active' },
  '512660': { name: '军工ETF', exchange: 'SSE', category: '行业', weight_source: 'quarterly', weight_date: '2026-09-30', coverage: 0.97, nav_prev: 1.18, share_total: 1.2e10, listing_date: '2016-08-08', status: 'active' },
  '512690': { name: '酒ETF', exchange: 'SSE', category: '行业', weight_source: 'quarterly', weight_date: '2026-09-30', coverage: 0.95, nav_prev: 0.85, share_total: 1.6e10, listing_date: '2019-05-06', status: 'active' },
  '512880': { name: '证券ETF', exchange: 'SSE', category: '行业', weight_source: 'quarterly', weight_date: '2026-09-30', coverage: 0.98, nav_prev: 1.28, share_total: 4.2e10, listing_date: '2016-08-08', status: 'active' },
  '512760': { name: '芯片ETF', exchange: 'SSE', category: '主题', weight_source: 'quarterly', weight_date: '2026-09-30', coverage: 0.96, nav_prev: 1.62, share_total: 1.9e10, listing_date: '2019-06-18', status: 'active' },
  '515790': { name: '光伏ETF', exchange: 'SSE', category: '主题', weight_source: 'quarterly', weight_date: '2026-09-30', coverage: 0.94, nav_prev: 0.98, share_total: 1.5e10, listing_date: '2020-12-18', status: 'active' },
  '159901': { name: '深证100ETF', exchange: 'SZSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 3.05, share_total: 1.4e10, listing_date: '2006-04-24', status: 'accumulating' },
  '159915': { name: '创业板ETF', exchange: 'SZSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 2.45, share_total: 5.8e10, listing_date: '2011-12-09', status: 'accumulating' },
  '159922': { name: '中证500ETF', exchange: 'SZSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 6.35, share_total: 1.1e10, listing_date: '2013-03-15', status: 'accumulating' },
  '159928': { name: '消费ETF', exchange: 'SZSE', category: '行业', weight_source: 'quarterly', weight_date: '2026-09-30', coverage: 0.95, nav_prev: 1.15, share_total: 1.3e10, listing_date: '2013-09-16', status: 'accumulating' },
  '159949': { name: '创业板50ETF', exchange: 'SZSE', category: '宽基', weight_source: 'index', weight_date: '2026-10-29', coverage: 1, nav_prev: 0.95, share_total: 3.1e10, listing_date: '2016-07-22', status: 'accumulating' }
};

// 行业 → ETF 贡献池（个股详情 top_etfs 用，构造值）
const INDUSTRY_ETF_POOL = {
  '801080': ['512480', '512760', '588000', '510300'],
  '801150': ['512170', '510300', '563360'],
  '801730': ['515790', '510300', '563360'],
  '801120': ['512690', '510300', '563360', '510050'],
  '801780': ['510300', '563360', '510050', '510500'],
  '801790': ['512880', '510300', '563360'],
  '801110': ['510300', '563360', '510050'],
  '801880': ['510300', '563360', '510500'],
  '801050': ['510500', '510300', '512100'],
  '801160': ['510050', '510300', '563360'],
  '801010': ['510500', '510300'],
  '801710': ['510500', '510300'],
  '801200': ['510300', '563360', '510500'],
  '801180': ['510050', '510300', '510500']
};
const DEFAULT_ETF_POOL = ['510300', '563360', '510500', '512100', '510050'];

/* ---------- 工具 ---------- */

function pad(x) {
  return x < 10 ? '0' + x : '' + x;
}

function fmtDate(d) {
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

// 从 2026-10-29 向前取 n 个交易日（仅过滤周末，节假日忽略——mock 足够）
function tradingDates(n) {
  const out = [];
  const d = new Date(2026, 9, 29);
  while (out.length < n) {
    const w = d.getDay();
    if (w !== 0 && w !== 6) out.push(fmtDate(d));
    d.setDate(d.getDate() - 1);
  }
  return out.reverse();
}

function hashCode(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = (h * 31 + str.charCodeAt(i)) | 0;
  }
  return h;
}

function mulberry32(a) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function seeded(str) {
  return mulberry32(hashCode(str));
}

function round2(v) {
  return Math.round(v * 100) / 100;
}

module.exports = {
  LATEST_DATE,
  INDUSTRIES,
  STOCKS,
  ETF_INFO,
  INDUSTRY_ETF_POOL,
  DEFAULT_ETF_POOL,
  tradingDates,
  seeded,
  round2
};
