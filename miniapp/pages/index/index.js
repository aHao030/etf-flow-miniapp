const api = require('../../services/api');
const PERIODS = [
  { key: '1d', label: '单日' },
  { key: '5d', label: '5日' },
  { key: '20d', label: '20日' }
];
Page({
  data: {
    periods: PERIODS, period: '1d', direction: 'in',
    industries: [{ industry_code: '', industry_name: '全部行业' }], industryIndex: 0,
    items: [], page: 0, pageSize: 20, hasMore: true, loading: false, loadingMore: false,
    loadMoreStatus: 'idle', tradeDate: '', szseAccumulating: false, coverage: null, isMock: false, error: '',
    query: '', searchItems: [], searchLoading: false, searchError: '', searchPage: 0, searchHasMore: false
  },
  onLoad() {
    getApp().ensureMeta().then(m => this.setData({
      tradeDate: m.latest_date, szseAccumulating: !!m.szse_accumulating, coverage: m.coverage || null, isMock: !!m.is_mock
    })).catch(() => {});
    this.loadIndustries();
    this.refresh();
  },
  onUnload() {
    this._rankSeq = (this._rankSeq || 0) + 1;
    this._searchSeq = (this._searchSeq || 0) + 1;
    clearTimeout(this._searchTimer);
  },
  onPullDownRefresh() { this.refresh().then(() => wx.stopPullDownRefresh()); },
  onReachBottom() {
    if (this.data.query.trim()) {
      if (this.data.searchHasMore && !this.data.searchLoading) this.search(false);
    } else if (this.data.hasMore && !this.data.loadingMore && !this.data.loading) {
      this.loadMore();
    }
  },
  loadIndustries() {
    api.getIndustries().then(d => this.setData({
      industries: [{ industry_code: '', industry_name: '全部行业' }].concat(d.items || [])
    })).catch(() => {});
  },
  fetchRank(reset) {
    const seq = reset ? (this._rankSeq = (this._rankSeq || 0) + 1) : this._rankSeq;
    const page = reset ? 1 : this.data.page + 1;
    return api.getRank({
      period: this.data.period, direction: this.data.direction,
      industry: this.data.industries[this.data.industryIndex].industry_code,
      page, page_size: this.data.pageSize
    }).then(d => {
      if (seq !== this._rankSeq) return;
      this.setData({
        items: reset ? (d.items || []) : this.data.items.concat(d.items || []),
        page, total: d.total, tradeDate: d.trade_date, hasMore: !!d.has_more,
        loading: false, loadingMore: false, loadMoreStatus: d.has_more ? 'idle' : 'nomore', error: ''
      });
    }).catch(err => {
      if (seq !== this._rankSeq) return;
      this.setData({ loading: false, loadingMore: false, loadMoreStatus: 'error',
        error: (err && err.message) || '加载失败，请重试' });
    });
  },
  refresh() {
    this.setData({ page: 0, items: [], hasMore: true, loading: true, loadingMore: false, error: '' });
    return this.fetchRank(true);
  },
  loadMore() {
    this.setData({ loadingMore: true, loadMoreStatus: 'loading' });
    return this.fetchRank(false);
  },
  onPeriodTap(e) {
    const key = e.currentTarget.dataset.key;
    if (key !== this.data.period) { this.setData({ period: key }); this.refresh(); }
  },
  onDirectionTap(e) {
    const key = e.currentTarget.dataset.key;
    if (key !== this.data.direction) { this.setData({ direction: key }); this.refresh(); }
  },
  onIndustryChange(e) {
    const idx = Number(e.detail.value);
    if (idx !== this.data.industryIndex) { this.setData({ industryIndex: idx }); this.refresh(); }
  },
  onItemTap(e) {
    wx.navigateTo({ url: '/package-detail/pages/stock-detail/stock-detail?code=' + encodeURIComponent(e.detail.code) });
  },
  onSearchItemTap(e) {
    wx.navigateTo({ url: '/package-detail/pages/stock-detail/stock-detail?code=' + encodeURIComponent(e.currentTarget.dataset.code) });
  },
  onSearchInput(e) {
    const query = e.detail.value.trim();
    clearTimeout(this._searchTimer);
    this._searchSeq = (this._searchSeq || 0) + 1;
    this.setData({ query, searchItems: [], searchPage: 0, searchHasMore: false,
      searchLoading: !!query, searchError: '' });
    if (query) this._searchTimer = setTimeout(() => this.search(true), 300);
  },
  search(reset) {
    const query = this.data.query.trim();
    if (!query) return Promise.resolve();
    const seq = reset ? (this._searchSeq = (this._searchSeq || 0) + 1) : this._searchSeq;
    const page = reset ? 1 : this.data.searchPage + 1;
    this.setData({ searchLoading: true, searchError: '' });
    return api.getStocks({ q: query, page, page_size: 20 }).then(d => {
      if (seq !== this._searchSeq) return;
      this.setData({ searchItems: reset ? (d.items || []) : this.data.searchItems.concat(d.items || []),
        searchPage: page, searchHasMore: !!d.has_more, searchLoading: false });
    }).catch(err => {
      if (seq === this._searchSeq) this.setData({
        searchLoading: false, searchError: (err && err.message) || '搜索失败，请重试'
      });
    });
  },
  onSearchRetry() { this.search(this.data.searchPage === 0); },
  onRetry() {
    if (this.data.items.length && this.data.loadMoreStatus === 'error') this.loadMore();
    else if (!this.data.items.length) this.refresh();
  }
});
