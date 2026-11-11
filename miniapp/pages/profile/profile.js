// pages/profile —— 我的（m3-草案 §2 / §4）
// 口径说明、免责声明、数据来源、隐私说明、关于。
// 文案唯一来源 /meta.disclosure（终稿见《m1-模型说明-v1.md》§7）：
//   - 口径长文案（完整版卡片）→ disclosure.long
//   - 免责声明卡片 → disclosure.disclaimer（M4 报告 S1：后端提供时优先，缺省回退 long）
const watchlist = require('../../utils/watchlist');

Page({
  data: {
    appVersion: '0.1.0',
    latestDate: '',
    disclosureLong: '',
    disclaimer: ''
  },

  onShow() {
    const app = getApp();
    app
      .ensureMeta()
      .then((meta) => {
        const d = (meta && meta.disclosure) || {};
        this.setData({
          appVersion: meta.app_version || this.data.appVersion,
          latestDate: meta.latest_date,
          disclosureLong: d.long || '',
          disclaimer: d.disclaimer || d.long || ''
        });
      })
      .catch(() => {});
  },

  onClearWatchlist() {
    wx.showModal({
      title: '清除自选',
      content: '将清空本机保存的全部自选（含提醒开关），确定？',
      confirmColor: '#d43c33',
      success: (res) => {
        if (res.confirm) {
          watchlist.clearAll();
          wx.showToast({ title: '已清除', icon: 'success' });
        }
      }
    });
  }
});
