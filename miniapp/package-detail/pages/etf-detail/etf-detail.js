// package-detail/pages/etf-detail —— ETF 页（m3-草案 §2）
// 契约 §2.5 /etf/{code}：份额/Δ份额/NAV/估算净流入/对成分股影响 top_stocks。
// ⚠ 契约 §2.5 未定义序列字段：页面已按期望字段 share_series/scale_series 渲染图表，
// 联调前需 backend-dev 补充契约（见 docs/m3-前端工程说明.md §7）。
const api = require('../../../services/api');
const watchlist = require('../../../utils/watchlist');
const fmt = require('../../../utils/format');

Page({
  data: {
    code: '',
    name: '',
    exchange: '',
    exchangeLabel: '',
    category: '',
    shareTotal: null,
    shareChange: null,
    navPrev: null,
    estFlow: null,
    weightSourceLabel: '',
    weightSourceBadge: 'badge-quarter',
    weightDate: '',
    coverageText: '',
    topStocks: [],
    shareSeries: [],
    scaleSeries: [],
    flagLabels: [],
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

  onPullDownRefresh() {
    this.fetch()
      .then(() => wx.stopPullDownRefresh())
      .catch(() => wx.stopPullDownRefresh());
  },

  fetch() {
    if (!this.data.code) {
      this.setData({ loading: false, error: '缺少 ETF 代码' });
      return Promise.resolve();
    }
    this.setData({ loading: true });
    return api
      .getEtf(this.data.code)
      .then((d) => {
        const topStocks = (d.top_stocks || []).map((t) => ({
          code: t.stock_code,
          name: t.stock_name,
          flow: t.flow,
          extra: '权重 ' + (t.weight * 100).toFixed(2) + '%'
        }));
        const covPct = fmt.covPct(d.coverage);
        this.setData({
          name: d.etf_name,
          exchange: d.exchange,
          exchangeLabel: d.exchange === 'SSE' ? '上交所' : '深交所',
          category: d.category,
          shareTotal: d.share_total,
          shareChange: d.share_change,
          navPrev: d.nav_prev,
          estFlow: d.est_flow,
          weightSourceLabel: fmt.weightSourceLabel(d.weight_source),
          weightSourceBadge: fmt.weightSourceBadge(d.weight_source),
          weightDate: d.weight_date,
          coverageText: covPct === null ? '' : covPct.toFixed(2) + '%',
          topStocks,
          shareSeries: d.share_series || [],
          scaleSeries: d.scale_series || [],
          flagLabels: (d.flags || []).map(fmt.flagLabel),
          note: d.note,
          tradeDate: d.trade_date,
          isWatched: watchlist.has('etf', d.etf_code),
          loading: false,
          error: ''
        });
        wx.setNavigationBarTitle({ title: d.etf_name + ' · 详情' });
      })
      .catch((err) => {
        this.setData({ loading: false, error: (err && err.message) || '加载失败' });
      });
  },

  onToggleWatch() {
    const { code, name, isWatched } = this.data;
    if (isWatched) {
      watchlist.remove('etf', code);
      this.setData({ isWatched: false });
      wx.showToast({ title: '已移出自选', icon: 'none' });
    } else {
      watchlist.add({ type: 'etf', code, name });
      this.setData({ isWatched: true });
      wx.showToast({ title: '已加入自选', icon: 'none' });
    }
  },

  onStockTap(e) {
    wx.navigateTo({
      url: '/package-detail/pages/stock-detail/stock-detail?code=' + e.detail.code
    });
  },

  onRetry() {
    this.fetch();
  }
});
