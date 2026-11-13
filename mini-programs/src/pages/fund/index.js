const { get, sourceInfo } = require('../../lib/data');
const { back } = require('../../lib/platform');
const { money, number } = require('../../lib/format');
const { chartModel } = require('../../lib/chart');

const PAGE_SIZE = 20;
const metrics = {
  flow: { key: 'estimated_flow', label: '当日估算净申赎', unit: '元', kind: 'bar', currency: true },
  nav: { key: 'unit_nav', label: '单位净值', unit: '元', kind: 'line', currency: false },
  shares: { key: 'shares', label: '基金份额', unit: '份', kind: 'line', currency: false }
};
const qualityLabels = {
  raw_observation: '原始观测', estimated: '已估算',
  missing_previous_shares: '前日份额缺失', missing_current_shares: '当日份额缺失',
  missing_previous_nav: '前日净值缺失', missing_current_nav: '当日净值缺失',
  possible_split: '疑似份额折算', events_unverified: '基金事件未核验',
  fund_event: '基金事件待核实', money_market: '货币基金仅展示原始记录'
};

function validDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(value + 'T00:00:00Z');
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function decoded(value) {
  if (typeof value !== 'string') return '';
  try { return decodeURIComponent(value); } catch (_) { return value; }
}

function validRange(start, end) {
  return validDate(start) && validDate(end) && start <= end;
}

function clampDate(date, min, max) {
  return date < min ? min : date > max ? max : date;
}

function errorText(err) {
  return err && err.message || '暂时无法读取数据，请重试。';
}

function verifyIdentity(result, code, version, start, end) {
  if (!result || !result.fund || result.fund.fund_code !== code ||
      result.version !== version || result.start !== start || result.end !== end) {
    throw new Error('返回的基金、日期或数据版本与此查询不一致。');
  }
}

function validateRecords(rows, start, end) {
  if (!Array.isArray(rows) || rows.length > 8000) throw new Error('基金记录格式异常。');
  let previous = '';
  for (const row of rows) {
    if (!row || !validDate(row.trade_date) || row.trade_date < start ||
        row.trade_date > end || row.trade_date <= previous ||
        ![row.shares, row.unit_nav, row.estimated_flow].every(v => v === null || Number.isFinite(v)) ||
        row.shares !== null && row.shares < 0 || row.unit_nav !== null && row.unit_nav <= 0 ||
        row.estimated_flow !== null && row.quality !== 'estimated') {
      throw new Error('基金记录未通过日期、数值或估算状态检查。');
    }
    previous = row.trade_date;
  }
}

function sessionDates(result, start, end) {
  if (!result || result.start !== start || result.end !== end ||
      !Array.isArray(result.dates) || result.dates.length > 12000 ||
      !result.dates.every((date, i, list) => validDate(date) && date >= start &&
        date <= end && (i === 0 || date > list[i - 1]))) {
    throw new Error('交易日历未覆盖所选范围。');
  }
  return result.dates;
}
async function calendarFor(meta,start,end) {
  const calendar=get('broad/calendar',{start,end});
  if(sourceInfo().mode==='snapshot')return sessionDates(await calendar,start,end);
  const [rows,broad]=await Promise.all([calendar,get('broad/meta')]);
  if(!broad || !broad.version || !rows || rows.version!==broad.version || broad.history_version!==meta.version || broad.target_end!==meta.target_end){
    throw Error('交易日历与基金历史版本尚未对齐。');
  }
  return sessionDates(rows,start,end);
}

function emptyRange() {
  return {
    ready: false, loading: false, error: '', retryable: false, needsNewVersion:false, start: '', end: '',sourceDate:'',sourceLabel:'本期估算净申赎截至',
    chart: null, chartDay:null, chartNotice: '', chartRetry: '', summaryValue: '', summaryLabel: '', coverage: '',
    detailsOpen: false, detailLoading: false, detailError: '', detailRows: [],
    total: 0, detailPage: 1, pageCount: 1
  };
}

Page({
  data: {
    code: '', name: '', minDate: '', maxDate: '', draftStart: '', draftEnd: '',
    datePending: false, rangeNote: '', metric: 'flow', summaryTone: '',
    ...emptyRange()
  },

  onLoad(options) {
    const args = options || {};
    this._request = (this._request || 0) + 1;
    this._detailRequest = 0;
    this._snapshot = null;
    this._meta = null;
    this._draftRevision=0;
    this._sourceRange = args.start !== undefined || args.end !== undefined
      ? { start: args.start || '', end: args.end || '' } : null;
    this._sourceVersion = decoded(args.history_version);
    this._unit = ['wan', 'yi'].includes(args.unit) ? args.unit : 'auto';
    const code = args.code || '';
    this.setData({
      ...emptyRange(), code, name: decoded(args.name) || 'ETF', minDate: '', maxDate: '',
      backLabel:args.from==='stock'?'返回个股':args.from==='broad'?'返回指数':'返回',dateFiltersOpen:false,
      draftStart: '', draftEnd: '', rangeNote: '', datePending: false, metric: 'flow'
    });
    if (!/^\d{6}$/.test(code)) {
      this.setData({ error: '基金代码无效，请返回重新选择基金。' });
      return;
    }
    return this.loadLimits();
  },

  onHide() {this._wasHidden=true;},
  onShow() {
    if(!this._wasHidden)return;
    this._wasHidden=false;
    if(sourceInfo().mode!=='snapshot' && /^\d{6}$/.test(this.data.code))return this.loadLimits();
  },
  onUnload() {
    ++this._request;
    ++this._detailRequest;
    this._snapshot = null;
  },

  goBack() { back(); },
  toggleDates() {this.setData({dateFiltersOpen:!this.data.dateFiltersOpen});},
  inspectDay(event){if(this.data.chart && this._readDay)this.setData({chartDay:this._readDay(event.detail.date)});},

  async loadLimits(requestedRange) {
    const request = ++this._request;
    ++this._detailRequest;
    const revision=this._draftRevision, hadDraft=this.data.datePending;
    this._snapshot=null;this._meta=null;
    this.setData({ ...emptyRange(), loading: true });
    try {
      const meta = await get('history/meta');
      if (request !== this._request) return;
      if (!meta || !meta.version || !validRange(meta.target_start, meta.target_end)) {
        throw new Error('基金历史可查询范围暂不可用。');
      }
      if (this._sourceVersion && meta.version !== this._sourceVersion) {
        this.setData({ loading:false, retryable:true, needsNewVersion:true,
          error:'上页引用的基金历史版本已变化，可按原日期查询新版基金历史。' });
        return;
      }
      this._meta = meta;
      const minDate = meta.target_start, maxDate = meta.target_end;
      const source = requestedRange || this._sourceRange || { start: minDate, end: maxDate };
      const valid = validRange(source.start, source.end);
      const start = valid ? clampDate(source.start, minDate, maxDate) : minDate;
      const end = valid ? clampDate(source.end, minDate, maxDate) : maxDate;
      const keepDraft=hadDraft || revision!==this._draftRevision;
      this.setData({ minDate, maxDate, rangeNote:'', datePending:keepDraft && (this.data.draftStart!==start||this.data.draftEnd!==end),
        ...(keepDraft?{}:{draftStart:start,draftEnd:end}) });
      if (!valid || source.end < minDate || source.start > maxDate) {
        const reason = valid ? '所选 ' + source.start + ' 至 ' + source.end + ' 与基金历史没有重叠。' : '传入的日期无效。';
        this.setData({ loading: false, error: reason + '可查询 ' + minDate + ' 至 ' + maxDate + '，请调整日期后应用。' });
        return;
      }
      if (start !== source.start || end !== source.end) {
        this.setData({ rangeNote: '上页所选 ' + source.start + ' 至 ' + source.end + '。此处显示与基金历史重叠的日期。' });
      }
      return this.loadRange(start, end);
    } catch (err) {
      if (request === this._request) this.setData({ loading: false, retryable: true, error: errorText(err) });
    }
  },

  changeDate(event) {
    const field = event.currentTarget.dataset.field;
    if (field !== 'draftStart' && field !== 'draftEnd') return;
    ++this._draftRevision;
    const start = field === 'draftStart' ? event.detail.value : this.data.draftStart;
    const end = field === 'draftEnd' ? event.detail.value : this.data.draftEnd;
    this.setData({ [field]: event.detail.value, datePending: start !== this.data.start || end !== this.data.end });
  },

  applyDates() {
    const { draftStart: start, draftEnd: end, minDate, maxDate } = this.data;
    this._sourceRange = {start,end};
    if(!this._meta)return this.loadLimits();
    this.setData({ rangeNote: '', datePending: false });
    if (!validRange(start, end) || start < minDate || end > maxDate) {
      ++this._request;
      ++this._detailRequest;
      this._snapshot = null;
      this.setData({ ...emptyRange(), error: '请选择 ' + minDate + ' 至 ' + maxDate + ' 内的有效起止日期。' });
      return;
    }
    return this.loadRange(start, end);
  },

  retry() {
    if(this.data.needsNewVersion)this._sourceVersion='';
    return this.loadLimits();
  },

  async loadRange(start, end) {
    const request = ++this._request;
    ++this._detailRequest;
    this._snapshot = null;
    this.setData({ ...emptyRange(), loading: true, start, end });
    const code = this.data.code, meta=this._meta, version = meta.version;
    const params = { start, end };
    try {
      const [history, calendar] = await Promise.all([
        get('history/etf/' + code + '/chart', params),
        calendarFor(meta,start,end).then(dates => ({ dates }))
          .catch(err => ({ error: errorText(err) }))
      ]);
      if (request !== this._request) return;
      verifyIdentity(history, code, version, start, end);
      validateRecords(history.series, start, end);
      if (history.total !== history.series.length) throw new Error('基金记录数量与快照不符。');
      this._snapshot = {
        version, start, end, count: history.total, calendar,
        byDate: new Map(history.series.map(row => [row.trade_date, row]))
      };
      this._sourceVersion='';
      this.setData({
        name: history.fund.fund_name || this.data.name,
        ready: true, loading: false, total: history.total,
        pageCount: Math.max(1, Math.ceil(history.total / PAGE_SIZE))
      });
      this.showMetric();
    } catch (err) {
      if (request === this._request) {
        const message = errorText(err);
        const context = this._meta.offline && !/离线|数据包/.test(message)
          ? '当前离线包无法提供这只基金在所选区间的记录。' : '';
        this.setData({ loading: false, error: context + message, retryable: true });
      }
    }
  },

  selectMetric(event) {
    const metric = event.currentTarget.dataset.metric;
    if (!Object.prototype.hasOwnProperty.call(metrics, metric) || metric === this.data.metric) return;
    this.setData({ metric });
    this.showMetric();
  },

  showMetric() {
    const snapshot = this._snapshot;
    if (!snapshot) return;
    if (snapshot.calendar.error) {
      this.setData({ chart: null, chartNotice: snapshot.calendar.error + ' 可展开下方原始记录。', chartRetry: '重读交易日历' });
      return;
    }
    const metric = metrics[this.data.metric];
    const rows = snapshot.calendar.dates.map(date => {
      const row = snapshot.byDate.get(date);
      return { date, value: row ? row[metric.key] : null };
    });
    const observed = rows.filter(row => Number.isFinite(row.value));
    const latest = observed[observed.length - 1];
    this.setData({sourceDate:latest?.date||'',sourceLabel:this.data.metric==='flow'?'本期估算净申赎截至':'本期'+metric.label+'截至'});
    const isFlow = metric.currency;
    const coverage = '所选 ' + rows.length + ' 个开市日，有值 ' + observed.length + ' 日' +
      (latest ? '。最近有值日 ' + latest.date : '') + '。';
    if (!latest) {
      this.setData({
        chart: null, summaryLabel: '', summaryValue: '', summaryTone: '', coverage,
        chartRetry: rows.length && this._meta.offline && !snapshot.count ? '重读基金记录' : '',
        chartNotice: !rows.length ? '所选期间无开市日。' :
          this._meta.offline && !snapshot.count ? '当前离线包在所选区间没有这只基金的历史观测。' :
          '所选期间暂无可核验的' + metric.label + '。'
      });
      return;
    }
    const value = isFlow ? observed.reduce((sum, row) => sum + row.value, 0) : latest.value;
    this.setData({
      chart: chartModel(rows, { ...metric, scope:[this.data.code,this.data.metric,snapshot.start,snapshot.end].join('/'), moneyUnit: this._unit },reader=>{this._readDay=reader;}),
      chartDay:this.data.chartDay && this._readDay ? this._readDay(this.data.chartDay.date) : null,
      chartNotice: '', chartRetry: '', coverage,
      summaryLabel: isFlow ? '期间估算净申赎小计' : '最近有值日' + metric.label,
      summaryValue: isFlow ? money(value, this._unit) : number(value, this.data.metric === 'nav' ? 8 : 2) + ' ' + metric.unit,
      summaryTone: isFlow && value !== 0 ? (value > 0 ? 'pos' : 'neg') : ''
    });
  },

  toggleDetails() {
    const open = !this.data.detailsOpen;
    ++this._detailRequest;
    this.setData({ detailsOpen: open, detailLoading: false, detailError: '' });
    if (open && this._snapshot && this.data.total) return this.loadDetails(this.data.detailPage);
  },

  turnPage(event) {
    if (this.data.detailLoading) return;
    const page = this.data.detailPage + Number(event.currentTarget.dataset.step);
    if (Number.isInteger(page) && page >= 1 && page <= this.data.pageCount) return this.loadDetails(page);
  },

  retryDetails() { return this.loadDetails(this.data.detailPage); },

  async loadDetails(page) {
    const snapshot = this._snapshot;
    if (!snapshot || !this.data.detailsOpen) return;
    const request = ++this._detailRequest;
    this.setData({ detailPage: page, detailLoading: true, detailError: '', detailRows: [] });
    try {
      const params = { start: snapshot.start, end: snapshot.end, page, page_size: PAGE_SIZE };
      const result = await get('history/etf/' + this.data.code, params);
      if (request !== this._detailRequest || snapshot !== this._snapshot) return;
      verifyIdentity(result, this.data.code, snapshot.version, snapshot.start, snapshot.end);
      validateRecords(result.items, snapshot.start, snapshot.end);
      const expectedCount = Math.min(PAGE_SIZE, snapshot.count - (page - 1) * PAGE_SIZE);
      if (result.page !== page || result.page_size !== PAGE_SIZE || result.total !== snapshot.count || result.items.length !== expectedCount) {
        throw new Error('明细页与图表快照不一致，请重新查询。');
      }
      const detailRows = result.items.map(row => {
        const original = snapshot.byDate.get(row.trade_date);
        if (!original || ['unit_nav', 'shares', 'estimated_flow', 'quality'].some(key => original[key] !== row[key]) ||
            row.acc_nav !== null && !Number.isFinite(row.acc_nav)) {
          throw new Error('明细记录与图表快照不一致，请重新查询。');
        }
        return {
          date: row.trade_date, shares: number(row.shares, 2), nav: number(row.unit_nav, 8),
          accNav: number(row.acc_nav, 8), flow: money(row.estimated_flow, this._unit),
          tone: row.estimated_flow > 0 ? 'pos' : row.estimated_flow < 0 ? 'neg' : '',
          quality: qualityLabels[row.quality] || '状态待核验'
        };
      });
      this.setData({ detailLoading: false, detailRows });
    } catch (err) {
      if (request === this._detailRequest && snapshot === this._snapshot) {
        this.setData({ detailLoading: false, detailError: errorText(err) });
      }
    }
  }
});
