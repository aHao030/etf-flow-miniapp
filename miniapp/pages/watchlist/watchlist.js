// pages/watchlist —— 自选/提醒（m3-草案 §2）
// 契约 §2.8 已冻结：服务端自选 M2 不实现 → 本地 Storage（utils/watchlist.js）。
// 提醒：开关 UI 已就位；订阅消息授权（wx.requestSubscribeMessage）待 M2 技术路线确定后接入。
const api = require('../../services/api');
const watchlist = require('../../utils/watchlist');

Page({
  data: {
    stockItems: [],
    etfItems: [],
    empty: true,
    loading: true
  },

  onShow() {
    this.reload();
  },

  reload() {
    const all = watchlist.getAll();
    if (!all.length) {
      this.setData({ stockItems: [], etfItems: [], empty: true, loading: false });
      return;
    }

    const stockRows = [];
    const etfRows = [];
    const jobs = all.map((it) => {
      if (it.type === 'stock') {
        return api
          .getStock(it.code)
          .then((d) => {
            stockRows.push({
              type: 'stock',
              code: d.stock_code,
              name: d.stock_name,
              sub: d.industry_name,
              flow: d.flow_1d,
              intensity_bp: d.intensity_1d_bp,
              remind: !!it.remind
            });
          })
          .catch(() => {
            stockRows.push({
              type: 'stock',
              code: it.code,
              name: it.name,
              sub: '详情获取失败',
              flow: null,
              intensity_bp: null,
              remind: !!it.remind
            });
          });
      }
      return api
        .getEtf(it.code)
        .then((d) => {
          etfRows.push({
            type: 'etf',
            code: d.etf_code,
            name: d.etf_name,
            sub: d.category + ' · ' + d.exchange,
            flow: d.est_flow,
            intensity_bp: undefined,
            remind: !!it.remind
          });
        })
        .catch(() => {
          etfRows.push({
            type: 'etf',
            code: it.code,
            name: it.name,
            sub: '详情获取失败',
            flow: null,
            intensity_bp: undefined,
            remind: !!it.remind
          });
        });
    });

    Promise.all(jobs).then(() => {
      this.setData({
        stockItems: stockRows,
        etfItems: etfRows,
        empty: false,
        loading: false
      });
    });
  },

  onRowTap(e) {
    const { type, code } = e.detail;
    if (type === 'stock') {
      wx.navigateTo({ url: '/package-detail/pages/stock-detail/stock-detail?code=' + code });
    } else {
      wx.navigateTo({ url: '/package-detail/pages/etf-detail/etf-detail?code=' + code });
    }
  },

  onRemindChange(e) {
    const { type, code } = e.currentTarget.dataset;
    const on = watchlist.toggleRemind(type, code);
    // 订阅消息需 M2 服务端与模板（契约 §2.8 冻结），骨架阶段仅记录本地开关
    wx.showToast({
      title: on ? '已开启（服务端能力开发中）' : '已关闭',
      icon: 'none'
    });
    this.updateRemindState(type, code, on);
  },

  updateRemindState(type, code, on) {
    const key = type === 'stock' ? 'stockItems' : 'etfItems';
    const rows = this.data[key].map((it) => {
      if (it.code === code) it.remind = on;
      return it;
    });
    this.setData({ [key]: rows });
  },

  onLongPress(e) {
    const { type, code, name } = e.currentTarget.dataset;
    wx.showActionSheet({
      itemList: ['移除自选'],
      success: (res) => {
        if (res.tapIndex === 0) {
          watchlist.remove(type, code);
          wx.showToast({ title: '已移除 ' + name, icon: 'none' });
          this.reload();
        }
      }
    });
  },

  onGoRank() {
    wx.switchTab({ url: '/pages/index/index' });
  }
});
