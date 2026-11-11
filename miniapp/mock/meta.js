// mock/meta.js —— mock /api/v1/meta 与 /api/v1/health（契约 §2.1 / §2.2）
// disclosure 文案与《m1-模型说明-v1.md》§7 终稿逐字一致（M4 报告 D5 已同步，2026-10-30）。
// 前端不硬编码：唯一来源为 /meta；mock 仅作 USE_MOCK 演示模式的兜底。
// ⚠ disclaimer 字段为 M4 报告 S1 的前端兼容预埋：建议后端 /meta.disclosure 增加该字段
//   （终稿见《m5-提审材料包》§1③）；后端未提供时前端回退展示 long。
const BASE = require('./_base');

const META = {
  latest_date: BASE.LATEST_DATE,
  is_mock: true,
  prev_trade_date: '2026-10-28',
  app_version: '0.1.0',
  industry_dict_version: 'mock-20261029',
  units: { money: '元', intensity: 'bp' },
  windows: ['1d', '5d', '20d'],
  szse_accumulating: true,
  coverage: { tracked_etfs: 18, active_etfs: 13, pending_etfs: 5 },
  history_note: '模拟数据，历史数据使用当前权重回溯。',
  disclosure: {
    short:
      '本页「估算净流入」为估算值：按 ETF 每日份额变化 × 单位净值 × 持仓权重推算，非交易所或基金公司披露的真实买卖资金。仅供参考，不构成投资建议。',
    long:
      '1. 测算方法：以交易所每日披露的 ETF 份额变化（申赎净额）乘以基金单位净值折算为金额，再按该 ETF 最新披露的成分股权重分摊至个股，得到个股因 ETF 申赎产生的潜在资金流估算值。\n' +
      '2. 权重来源：宽基 ETF 用中证指数官网成分权重；行业/主题 ETF 用定期报告持仓（年报/半年报全持仓、季报前 100 大，滞后 1–6 个月）。期间基金实际持仓可能变化，估算与真实资金流向可能存在偏差。\n' +
      '3. 处理规则：份额折算/拆分、新上市建仓期、终止上市清算期的 ETF 不纳入或单独标记；大额申赎单独标记；数据缺失日不计入累计并标注有效天数。深市 ETF 份额历史积累中，日频估算暂仅覆盖沪市 ETF。\n' +
      '4. 局限性：无法获知 ETF 申赎的实际执行方式（实物/现金），不含场外联接基金与盘中实时数据；本数据仅供研究观察，不构成投资建议，据此操作风险自负。\n' +
      '数据来源：交易所披露、中证指数官网、基金定期报告、公开行情数据。',
    disclaimer:
      '本小程序展示的「估算净流入」基于公开信息推算，为估算值而非真实成交数据，可能存在误差、遗漏或延迟；所有内容仅供信息参考与研究学习，不构成任何投资建议、推荐或收益承诺。市场有风险，投资需谨慎。数据来源于公开渠道（交易所披露、天天基金、东方财富、中证指数官网、基金定期报告等），本产品不保证其准确性、完整性与及时性。'
  }
};

function get() {
  return { code: 0, message: 'ok', data: META };
}

function health() {
  return {
    code: 0,
    message: 'ok',
    data: {
      status: 'ok',
      latest_date: META.latest_date,
      prev_trade_date: META.prev_trade_date,
      pipeline: {
        sse_share_date: META.latest_date,
        szse_share_date: META.prev_trade_date,
        holdings_date: '2026-09-30',
        computed_date: META.latest_date,
        cached_date: META.latest_date
      },
      szse_accumulating: true,
      message: '数据已更新至 ' + META.latest_date
    }
  };
}

module.exports = { get, health };
