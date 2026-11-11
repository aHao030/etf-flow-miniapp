// components/disclosure-bar —— 常驻口径披露条（合规硬要求，计划书 §7 / 契约 §2.1）
// 文案唯一来源：/api/v1/meta.disclosure（短文案常驻 + ⓘ 展开长文案），不硬编码。
Component({
  data: {
    short: '',
    long: ''
  },
  lifetimes: {
    attached() {
      const app = getApp();
      app
        .ensureMeta()
        .then((meta) => {
          const d = (meta && meta.disclosure) || {};
          this.setData({ short: d.short || '', long: d.long || '' });
        })
        .catch(() => {});
    }
  },
  methods: {
    onInfo() {
      if (!this.data.long) return;
      wx.showModal({
        title: '数据口径说明',
        content: this.data.long,
        showCancel: false,
        confirmText: '知道了'
      });
    }
  }
});
