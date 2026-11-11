'use strict';
function number(value, digits = 2) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const parts = value.toFixed(digits).split('.');
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return parts.join('.');
}
function money(value, unit = 'auto') {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const useYi = unit === 'yi' || (unit === 'auto' && Math.abs(value) >= 1e8);
  const scaled = value / (useYi ? 1e8 : 1e4);
  const digits = scaled && Math.abs(scaled)<.01 ? Math.min(10, Math.max(2,1-Math.floor(Math.log10(Math.abs(scaled))))) : 2;
  return (value > 0 ? '+' : '') + number(scaled,digits) + (useYi ? ' 亿元' : ' 万元');
}
function periodStart(end, months, min) {
  if (!months) return min;
  const date=new Date(end+'T00:00:00Z'), day=date.getUTCDate();
  date.setUTCDate(1); date.setUTCMonth(date.getUTCMonth()-months);
  date.setUTCDate(Math.min(day,new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate()));
  const start=date.toISOString().slice(0,10);
  return start<min?min:start;
}
module.exports={number,money,periodStart};
