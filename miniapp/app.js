// app.js —— 全局入口
// M3 第一阶段（UI 骨架 + mock）：所有数据来自本地 mock（config/env.js USE_MOCK=true）。
// M4 联调时切换 USE_MOCK=false 并填 BASE_URL，即走真实接口（services/api.js 已按契约实现信封解析）。
// 口径披露文案唯一来源：/api/v1/meta.disclosure（契约 §2.1），页面与组件一律不硬编码。
const api = require('./services/api');

App({
  globalData: {
    meta: null, // {latest_date, disclosure:{short,long}, szse_accumulating, ...}
    env: require('./config/env')
  },

  onLaunch() {
    // 契约 §2.1：前端启动必调 /meta
    api
      .getMeta()
      .then((meta) => {
        this.globalData.meta = meta;
      })
      .catch(() => {
        // mock 模式下不会失败；真接口失败时各页面按 health 提示兜底
      });
  },

  // 供页面/组件等待 meta 就绪（disclosure-bar 等依赖）
  ensureMeta() {
    if (this.globalData.meta) return Promise.resolve(this.globalData.meta);
    return api.getMeta().then((meta) => {
      this.globalData.meta = meta;
      return meta;
    });
  }
});
