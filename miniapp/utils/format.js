// utils/format.js —— 展示格式化（JS 逻辑侧；WXML 侧使用 utils/fmt.wxs 同款逻辑）
// 契约约定：金额一律「元」、强度单位 bp（契约 §1），前端自行转 万/亿 展示。

/** 元 → 展示字符串（万/亿），null → 「—」；正数带 +，负数自带 - */
function money(v) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (isNaN(n)) return '—';
  const sign = n > 0 ? '+' : '';
  const abs = Math.abs(n);
  if (abs >= 1e8) return sign + (n / 1e8).toFixed(2) + '亿';
  if (abs >= 1e4) return sign + (n / 1e4).toFixed(2) + '万';
  return sign + n.toFixed(0) + '元';
}

/** 份额（份）→ 亿份 */
function share(v) {
  if (v === null || v === undefined) return '—';
  return (Number(v) / 1e8).toFixed(2) + '亿份';
}

/** 流通市值（元）→ 亿元 */
function mcap(v) {
  if (v === null || v === undefined) return '—';
  return (Number(v) / 1e8).toFixed(0) + '亿';
}

/** 流入强度 bp：保留 2 位，正数带 + */
function intensity(v) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  const sign = n > 0 ? '+' : '';
  return sign + n.toFixed(2) + 'bp';
}

/** 涨跌配色类名：up=流入红 / down=流出绿 / flat=中性 */
function cls(v) {
  if (v === null || v === undefined) return 'flat';
  const n = Number(v);
  if (n > 0) return 'up';
  if (n < 0) return 'down';
  return 'flat';
}

/** 契约 §2.4 quality.flags 枚举 → 中文标签 */
const FLAG_LABELS = {
  split_adjusted: '份额折算',
  large_flow: '大额申赎',
  listing_period: '建仓期',
  delisting: '清盘',
  weight_missing: '权重缺失',
  nav_missing: '净值缺失',
  share_gap: '份额缺口',
  weight_stale: '权重过期',
  cash_substitute: '现金替代',
  suspended: '长期停牌'
};

function flagLabel(flag) {
  return FLAG_LABELS[flag] || flag;
}

/** 契约 §2.4 data_status → 中文提示 */
function statusLabel(s) {
  return (
    {
      ok: '数据完整',
      sse_only: '仅沪市贡献（深市份额积累中）',
      accumulating: '深市贡献暂缺（快照起步）',
      no_data: '当前ETF池未覆盖'
    }[s] || s
  );
}

/** 契约 §2.4 quality.weight_source → 中文标签
 *  契约枚举：index | quarterly | none；后端当前实际返回扩展值
 *  （如 index_csindex(000300) / report(2026Q2)，联调已发现，待 backend-dev 归一化），
 *  前端按前缀兼容映射，保证两种形态都能正确展示。 */
function weightSourceLabel(s) {
  if (!s) return '未知';
  if (s === 'none') return '权重缺失';
  if (s.indexOf('index') === 0) return '指数权重';
  if (s.indexOf('quarterly') === 0 || s.indexOf('report') === 0) return '季报持仓';
  return s;
}

/** 权重来源徽标类名（指数权重=蓝 / 季报持仓=灰） */
function weightSourceBadge(s) {
  if (!s) return 'badge-quarter';
  if (s !== 'none' && s.indexOf('index') === 0) return 'badge-index';
  return 'badge-quarter';
}

/** 权重覆盖率 → 百分比数值：兼容后端 0–100 与契约 0–1 两种口径（联调发现后端返回百分比） */
function covPct(c) {
  if (c === null || c === undefined) return null;
  const n = Number(c);
  if (isNaN(n)) return null;
  return n > 1 ? n : n * 100;
}

module.exports = {
  money,
  share,
  mcap,
  intensity,
  cls,
  flagLabel,
  statusLabel,
  weightSourceLabel,
  weightSourceBadge,
  covPct
};
