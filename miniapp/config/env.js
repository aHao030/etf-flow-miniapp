// config/env.js —— 环境配置
// 联调阶段（M3→M4）：USE_MOCK=false，走真实后端 http://127.0.0.1:8000（server/README.md 启动）。
// 开发者工具需勾选「不校验合法域名…」（体验版期方案）；真机预览时改为局域网地址或正式域名。
// mock 保留为 fallback：USE_MOCK=true 即回到本地数据，便于无后端时演示 UI。
module.exports = {
  USE_MOCK: false,
  BASE_URL: 'http://127.0.0.1:8000',
  MOCK_DELAY_MS: 150 // 仅 USE_MOCK=true 时生效
};
