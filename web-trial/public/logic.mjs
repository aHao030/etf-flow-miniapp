const MONEY_UNITS = Object.freeze({wan:Object.freeze({unit:'wan',label:'万元',divisor:1e4}),yi:Object.freeze({unit:'yi',label:'亿元',divisor:1e8})});
const MONEY_UNIT_STORAGE = 'etf-flow-money-unit-v1';
const moneyListeners = new Set();
let moneyUnit = 'auto';
try { const saved=globalThis.localStorage?.getItem(MONEY_UNIT_STORAGE);if(['auto','wan','yi'].includes(saved))moneyUnit=saved; } catch {}
export function getMoneyUnit(){return moneyUnit;}
export function setMoneyUnit(unit){
  if(!['auto','wan','yi'].includes(unit))throw new TypeError('Unknown money unit');
  if(moneyUnit===unit)return unit;
  moneyUnit=unit;
  try{globalThis.localStorage?.setItem(MONEY_UNIT_STORAGE,unit);}catch{}
  for(const listener of moneyListeners)listener(unit);
  return unit;
}
export function onMoneyUnitChange(listener){moneyListeners.add(listener);return ()=>moneyListeners.delete(listener);}
export function resolveMoneyUnit(referenceAbs=0,mode=moneyUnit){
  if(!['auto','wan','yi'].includes(mode))throw new TypeError('Unknown money unit');
  return MONEY_UNITS[mode==='auto'?(Math.abs(Number(referenceAbs))>=1e8?'yi':'wan'):mode];
}
export function money(value, unit) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  const n = Number(value), sign = n > 0 ? '+' : '', scale=resolveMoneyUnit(n,unit);
  const digits=Math.abs(n/scale.divisor)<.01?(scale.unit==='yi'?10:6):2;
  return sign + new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:digits}).format(n / scale.divisor) + ' ' + scale.label;
}
export function signClass(value) {
  return value == null || !Number.isFinite(Number(value)) ? '' : Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : '';
}
export function latestOnly() {
  let sequence = 0;
  return { next: () => ++sequence, current: id => id === sequence };
}
function sourceDateOK(value, end) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(value + 'T00:00:00Z');
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0,10) === value && value <= end;
}
// These dates belong to observed records. The query target and batch time never
// supply a missing source date, including when an older snapshot is loaded.
export function sourceDataDates(meta, expectedVersion) {
  const result = {shares:{date:null,count:0},nav:{date:null,count:0},flow:{date:null,count:0}};
  if (!expectedVersion || meta?.version !== expectedVersion || !sourceDateOK(meta.target_end, '9999-12-31')) return result;
  if (meta.data_dates && typeof meta.data_dates === 'object') {
    for (const field of Object.keys(result)) {
      const item = meta.data_dates[field];
      if (sourceDateOK(item?.date, meta.target_end) && Number.isSafeInteger(item.count) && item.count > 0) {
        result[field] = {date:item.date,count:item.count};
      }
    }
    return result;
  }
  const observed = [...Object.values(meta.source_days || {}), ...Object.values(meta.market_days || {})]
    .map(item => item?.end).filter(day => sourceDateOK(day, meta.target_end)).sort();
  if (observed.length) result.shares = {date:observed.at(-1),count:null};
  return result;
}
export function sourceDateLabel(item) {
  if (!item?.date) return '暂无';
  return item.date + (Number.isSafeInteger(item.count) && item.count > 0 ? '（' + item.count.toLocaleString('zh-CN') + '只有记录）' : '');
}
export function formatWeight(value) {
  return value == null || !Number.isFinite(Number(value)) ? '—' : (Number(value) * 100).toFixed(2) + '%';
}
export function weightLabel(source) {
  if (!source || source === 'none' || source === 'missing') return '缺失';
  if (source.startsWith('index')) return '指数权重';
  if (source.startsWith('report') || source.startsWith('quarterly')) return '定期披露持仓';
  return source;
}
// Build a bounded visual series on the requested calendar axis; missing data is never zero-filled.
export function chartSeries(series, start, end, limit = 90) {
  const parsed = value => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return NaN;
    const ms = Date.parse(value + 'T00:00:00Z');
    return Number.isFinite(ms) && new Date(ms).toISOString().slice(0,10) === value ? ms : NaN;
  };
  const rows = (Array.isArray(series) ? series : []).filter(row => row && Number.isFinite(parsed(row.trade_date)))
    .slice().sort((a, b) => a.trade_date.localeCompare(b.trade_date));
  const from = Number.isFinite(parsed(start)) ? parsed(start) : (rows.length ? parsed(rows[0].trade_date) : NaN);
  const to = Number.isFinite(parsed(end)) ? parsed(end) : (rows.length ? parsed(rows[rows.length - 1].trade_date) : NaN);
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) return { points: [], aggregated: false };
  const inRange = rows.filter(row => { const ms = parsed(row.trade_date); return ms >= from && ms <= to; });
  const duration = Math.max(to - from, 1);
  const day = 86400000;
  const barWidth = Math.max(2, Math.min(10, 600 / Math.max(inRange.length, 1) - 1));
  const visible = inRange.filter(row => row.flow != null && Number.isFinite(Number(row.flow)));
  const collides = visible.some((row, index) => index > 0 &&
    (parsed(row.trade_date) - parsed(visible[index - 1].trade_date)) / duration * 600 < barWidth + 1);
  const aggregate = inRange.length > limit || to - from > 90 * day || collides;
  if (!aggregate) return { aggregated: false, points: inRange.map(row => ({
    x: (parsed(row.trade_date) - from) / duration, value: row.flow == null || !Number.isFinite(Number(row.flow)) ? null : Number(row.flow),
    start: row.trade_date, end: row.trade_date, count: row.flow == null || !Number.isFinite(Number(row.flow)) ? 0 : 1
  })) };
  const bucketCount = Math.max(1, Math.floor(limit));
  const bins = Array.from({length:bucketCount}, (_, index) => ({ x: (index + 0.5) / bucketCount, value: null, count: 0, start: null, end: null }));
  inRange.forEach(row => {
    const index = Math.min(bucketCount - 1, Math.floor(((parsed(row.trade_date) - from) / (duration + 1)) * bucketCount));
    const bin = bins[index];
    if (row.flow == null || !Number.isFinite(Number(row.flow))) return;
    bin.value = (bin.value === null ? 0 : bin.value) + Number(row.flow); bin.count++;
    bin.start ||= row.trade_date; bin.end = row.trade_date;
  });
  return { aggregated: true, points: bins };
}
// Filter without inventing observations on missing or non-trading days.
export function selectSeries(series, start, end, exclusive = false) {
  return (Array.isArray(series) ? series : []).filter(row => row && typeof row.trade_date === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(row.trade_date) &&
    (!start || (exclusive ? row.trade_date > start : row.trade_date >= start)) && (!end || row.trade_date <= end))
    .slice().sort((a, b) => a.trade_date.localeCompare(b.trade_date));
}
export function revealSection(id) {
  const target=document.getElementById(id);if(!target)return;
  target.setAttribute('tabindex','-1');
  target.focus?.({preventScroll:true});
  target.scrollIntoView?.({block:'start',behavior:'auto'});
}
