# -*- coding: utf-8 -*-
"""M2 后端全局配置（backend-dev，task-3）。

路径 / 上游接口 / 时序 / 口径文案集中于此，业务代码不硬编码。
上游接口与请求头要求来自《data/README.md》（Referer 必须保留，避开东财 push2 高频）。
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))           # server/
PROJ = os.path.dirname(ROOT)                                 # etf-flow-miniapp/
MODEL_DIR = os.path.join(PROJ, "model")                      # 只读（复用 v1_flow）
DATA_SAMPLES_DIR = os.path.join(PROJ, "data", "samples")     # 只读（M1 样例，仅作首次引导）

DATA_DIR = os.path.join(ROOT, "data")
INPUTS_DIR = os.path.join(DATA_DIR, "inputs")                # 模型输入（与 samples 同构）
WORK_DIR = os.environ.get("ETF_FLOW_WORK_DIR", os.path.join(ROOT, "work"))
OUTPUTS_DIR = os.path.join(WORK_DIR, "outputs")              # 模型输出（v1_flow 重定向）
LOG_DIR = os.path.join(WORK_DIR, "logs")
DB_PATH = os.environ.get("ETF_FLOW_DB_PATH", os.path.join(DATA_DIR, "app.db"))
INDUSTRY_CSV = os.path.join(DATA_DIR, "industry_sw_l1.csv")
SZSE_LATEST_CSV = os.path.join(DATA_DIR, "szse_latest.csv")  # 深市快照原始留档（调试）

APP_VERSION = "0.2.0-stock-monitor"
PERIODS = ("1d", "5d", "20d")
STOCK_PERIODS = PERIODS + ("3m", "6m", "1y", "2y", "3y", "5y")
RANK_TOP_N = 100
PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100
MIN_VALID_DAYS = {"5d": 3, "20d": 12}   # A12（与模型一致，仅校验用）
CACHE_TTL_SECONDS = 3600

TZ = "Asia/Shanghai"
WINDOW_MONTHS = {1, 4, 7, 10}   # 季报披露窗口（契约 §4：窗口内每日重拉持仓至覆盖率达标）
LISTING_DAYS_MIN = 60           # A9：建仓期阈值（与模型一致）

# ---------------- 上游接口（来源：data/README.md） ----------------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
SSE_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_HEADERS = {"Referer": "https://www.sse.com.cn/", "User-Agent": UA}
SZSE_URL = ("https://fund.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx"
            "&CATALOGID=1000_lf&TABKEY=tab1&random=0.123")
SZSE_HEADERS = {"Referer": "https://fund.szse.cn/marketdata/fundslist/index.html",
                "User-Agent": UA}
LSJZ_URL = "https://api.fund.eastmoney.com/f10/lsjz"
JJCC_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SNAPSHOT_URL = "https://qt.gtimg.cn/q="

HTTP_TIMEOUT = 40
SLEEP_SSE = 0.25        # 上交所逐日请求间隔（POC 实测友好）
SLEEP_FUND = 0.3        # 天天基金 lsjz/jjcc 逐基金间隔（实测无 403，保留限速）
SLEEP_KLINE = 0.3       # K线间隔（腾讯/新浪双源；腾讯被软封时由新浪承接）
SNAPSHOT_BATCH = 60     # 腾讯快照单次批量符号数
KLINE_COUNT = 60        # 个股序列 60 日
NAV_PAGE_SIZE = 40      # lsjz 回看条数（≥25 日净值 + 富余）
FETCH_RETRIES = 3
FETCH_BACKOFF = 2.0

# 定时任务（APScheduler，Asia/Shanghai）
JOB_PIPELINE = "19:30"    # 主任务（交易日）
JOB_RETRY = "22:30"       # 晚间兜底重试（SSE 晚间 19:00-20:00 披露，未出则此时再试）
JOB_MORNING = "8:10"      # T+1 早间补算（数据以 T+1 早间为准）
JOB_SZSE_EVENING = "20:30"  # 深市份额快照落库
JOB_SZSE_MORNING = "8:00"   # 深市份额核对补拉（覆盖晚间值）

# 口径披露文案（终稿：docs/m1-模型说明-v1.md §7，与 qa-compliance 同口径）
DISCLOSURE_SHORT = ("本页「估算净流入」为估算值：按 ETF 每日份额变化 × 单位净值 × 持仓权重推算，"
                    "非交易所或基金公司披露的真实买卖资金。仅供参考，不构成投资建议。")
DISCLOSURE_LONG = (
    "1. 测算方法：以交易所每日披露的 ETF 份额变化（申赎净额）乘以基金单位净值折算为金额，"
    "再按该 ETF 最新披露的成分股权重分摊至个股，得到个股因 ETF 申赎产生的潜在资金流估算值。\n"
    "2. 权重来源：宽基 ETF 用中证指数官网成分权重；行业/主题 ETF 用定期报告持仓"
    "（年报/半年报全持仓、季报前 100 大，滞后 1–6 个月）。期间基金实际持仓可能变化，"
    "估算与真实资金流向可能存在偏差。\n"
    "3. 处理规则：份额折算/拆分、新上市建仓期、终止上市清算期的 ETF 不纳入或单独标记；"
    "大额申赎单独标记；数据缺失日不计入累计并标注有效天数。深市 ETF 份额历史积累中，"
    "日频估算暂仅覆盖沪市 ETF。\n"
    "4. 局限性：无法获知 ETF 申赎的实际执行方式（实物/现金），不含场外联接基金与盘中实时数据；"
    "本数据仅供研究观察，不构成投资建议，据此操作风险自负。\n"
    "数据来源：交易所披露、中证指数官网、基金定期报告、公开行情数据。")
# 独立免责声明句（终稿：docs/m5-提审材料包.md §1③，S1 裁决落位于 meta.disclosure.disclaimer）
DISCLAIMER = ("本小程序展示的「估算净流入」基于公开信息推算，为估算值而非真实成交数据，"
              "可能存在误差、遗漏或延迟；所有内容仅供信息参考与研究学习，"
              "不构成任何投资建议、推荐或收益承诺。市场有风险，投资需谨慎。"
              "数据来源于公开渠道（交易所披露、天天基金、东方财富、中证指数官网、"
              "基金定期报告等），本产品不保证其准确性、完整性与及时性。")
