// Shared immutable calendar read. Missing sessions remain gaps in the charts.
let cached = null, pending = null, generation = 0;
export function resetTradingCalendar() { generation++; cached = null; pending = null; }
export async function tradingDates(start, end) {
  if (!cached) {
    const expected = generation;
    if (!pending) pending = (async () => {
      const response = await fetch('/api/v1/broad/calendar', {headers:{Accept:'application/json'}});
      const body = await response.json();
      if (!response.ok || body.code !== 0) throw Error('交易日历暂不可用，请刷新数据');
      const data = body.data, dates = data?.dates || data?.trading_dates;
      if (!Array.isArray(dates) || !dates.length || dates.length > 12000 || !dates.every((d,i) => typeof d==='string' && /^\d{4}-\d{2}-\d{2}$/.test(d) && !Number.isNaN(Date.parse(d+'T00:00:00Z')) && new Date(d+'T00:00:00Z').toISOString().slice(0,10)===d && (!i || d > dates[i-1]))) throw Error('交易日历校验失败');
      if (generation !== expected) throw Error('交易日历已刷新，请重试');
      cached = {dates, start:data.start || dates[0], end:data.end || dates.at(-1)};
      return cached;
    })().finally(() => { if (generation === expected) pending = null; });
    await pending;
  }
  if ((start && start < cached.start) || (end && end > cached.end)) throw Error('交易日历未覆盖所选范围，请刷新数据');
  return cached.dates.filter(d => (!start || d >= start) && (!end || d <= end));
}
