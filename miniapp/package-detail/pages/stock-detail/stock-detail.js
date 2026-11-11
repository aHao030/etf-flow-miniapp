// package-detail/pages/stock-detail —— 个股详情（m3-草案 §2）
// 契约 §2.4 /stock/{code}：60 日 series、ETF 来源拆解 top_etfs、流入强度、quality 质量标记。
// 合规：不展示行情价格/涨跌幅（已定决策）；全站字段统一「估算净流入」；图表下方常驻口径披露。
const api = require('../../../services/api');
const watchlist = require('../../../utils/watchlist');
const fmt = require('../../../utils/format');

const PERIODS = [
  { key: '1d', label: '单日' },
  { key: '5d', label: '5日' },
  { key: '20d', label: '20日' }
];

Page({
  data: {
    code: '',
    name: '',
    industryName: '',
    floatMcap: null,
    periods: PERIODS,
    period: '1d',
    flow: null,
    intensity: null,
    validDays: '',
    series: [],
    topEtfs: [],
    movement: null, coverage: null, historyNote: '',
    flagLabels: [],
    qualityNote: '',
    weightSourceLabel: '',
    weightSourceBadge: 'badge-quarter',
    coverageText: '',
    dataStatus: 'ok',
    statusText: '',
    note: '',
    tradeDate: '',
    isWatched: false,
    loading: true,
    error: ''
  },

  onLoad(options) {
    this.setData({ code: options.code || '' });
    this.fetch();
  },

  onUnload() { this._fetchSeq = (this._fetchSeq || 0) + 1; },

  onPullDownRefresh() {
    this.fetch()
      .then(() => wx.stopPullDownRefresh())
      .catch(() => wx.stopPullDownRefresh());
  },

  fetch() {
    if (!this.data.code) {
      this.setData({ loading: false, error: '缺少股票代码' });
      return Promise.resolve();
    }
    const seq = this._fetchSeq = (this._fetchSeq || 0) + 1;
    const p = this.data.period;
    this.setData({ loading: true, error: '' });
    return api
      .getStock(this.data.code, { period: p })
      .then((d) => {
        if (seq !== this._fetchSeq) return;
        const flow = p === '5d' ? d.flow_5d : p === '20d' ? d.flow_20d : d.flow_1d;
        const intensity =
          p === '5d' ? d.intensity_5d_bp : p === '20d' ? d.intensity_20d_bp : d.intensity_1d_bp;
        const validDays =
          p === '5d' ? d.valid_days.d5 : p === '20d' ? d.valid_days.d20 : d.valid_days.d1;

        const topEtfs = (d.etf_contributions || []).map((t) => ({
          code: t.etf_code,
          name: t.etf_name,
          sub:
            fmt.weightSourceLabel(t.weight_source) +
            ' · 权重 ' +
            (t.weight == null ? '—' : (t.weight * 100).toFixed(2)) +
            '%',
          flow: t.flow,
          extra: '权重日 ' + (t.weight_date || '—') + ' · 有效 ' + (t.valid_days == null ? '—' : t.valid_days) + ' 日 · ' + ({ complete: '完整', partial: '部分缺失', missing: '缺失', pending: '待纳入' }[t.status] || t.status || '')
        }));

        const covPct = fmt.covPct(d.quality && d.quality.coverage);

        this.setData({
          name: d.stock_name,
          industryName: d.industry_name,
          floatMcap: d.float_mcap,
          flow,
          intensity,
          validDays,
          series: d.series || [],
          topEtfs,
          movement: d.movement || null,
          coverage: d.coverage || null,
          historyNote: d.history_note || '',
          flagLabels: ((d.quality && d.quality.flags) || []).map(fmt.flagLabel),
          qualityNote: (d.quality && d.quality.note) || '',
          weightSourceLabel: fmt.weightSourceLabel(d.quality && d.quality.weight_source),
          weightSourceBadge: fmt.weightSourceBadge(d.quality && d.quality.weight_source),
          coverageText: covPct === null ? '' : covPct.toFixed(2) + '%',
          dataStatus: d.data_status,
          statusText: fmt.statusLabel(d.data_status),
          note: d.note,
          tradeDate: d.trade_date,
          isWatched: watchlist.has('stock', d.stock_code),
          loading: false,
          error: ''
        });
        wx.setNavigationBarTitle({ title: d.stock_name + ' · 详情' });
      })
      .catch((err) => {
        if (seq !== this._fetchSeq) return;
        this.setData({
          loading: false,
          error: (err && err.message) || '加载失败'
        });
      });
  },

  onPeriodTap(e) {
    const key = e.currentTarget.dataset.key;
    if (key === this.data.period) return;
    this.setData({ period: key });
    this.fetch();
  },

  onToggleWatch() {
    const { code, name, isWatched } = this.data;
    if (isWatched) {
      watchlist.remove('stock', code);
      this.setData({ isWatched: false });
      wx.showToast({ title: '已移出自选', icon: 'none' });
    } else {
      watchlist.add({ type: 'stock', code, name });
      this.setData({ isWatched: true });
      wx.showToast({ title: '已加入自选', icon: 'none' });
    }
  },

  onEtfTap(e) {
    wx.navigateTo({
      url: '/package-detail/pages/etf-detail/etf-detail?code=' + e.detail.code
    });
  },

  onRetry() {
    this.fetch();
  }
});
