# data/ — M1 数据源验证产出（数据工程师）

> 本文档记录 M1 阶段实测过的每个数据源的接口、字段、频率、延迟、限制与结论。
> 实测日期：2026-10-30。POC 脚本：`poc_fetch_data.py`；样例 CSV：`samples/`；原始探针证据：`tools/`。

## 0. 结论速览（结论表）

| # | 数据 | 首选免费源（已实测） | 频率/延迟 | 结论 |
|---|------|---------------------|-----------|------|
| ① | ETF 每日份额变化（沪市） | **上交所官方** `query.sse.com.cn/commonQuery.do`（sqlId=`COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L`） | 每日，T 日晚间/T+1 早间可得 T 日数据；支持按 `STAT_DATE` 历史回溯 | ✅ **可用**（官方口径，与东财对账一致） |
| ① | ETF 每日份额变化（深市） | **深交所官方** `fund.szse.cn/api/report/ShowReport`（xlsx，CATALOGID=1000_lf） | 每日 T-1 快照，**无历史日期参数** | ⚠️ 可用但仅快照；深市份额历史需自建每日落库（M2 ETL 任务） |
| ①' | ETF 份额（备查/对账） | 东方财富 push2 `f84`（总份额）、天天基金 F10 gmbd（季度期末份额） | 快照/季度 | ✅ 可作对账源；push2 有 IP 限流风险 |
| ② | ETF 持仓与权重（季报） | **天天基金 F10** `FundArchivesDatas.aspx?type=jjcc`（akshare `fund_portfolio_hold_em` 同源） | 季报（如 2026Q3 于 2026-10-26 前后披露）；**每季最多前 100 大重仓** | ✅ 可用（行业主题 ETF 成分股 ≤100 只，覆盖完整；宽基 300/创业板指等前 100 不完整） |
| ②' | PCF 每日申赎清单 | 上交所披露页 `disclosure/fund/etflist/`、深交所基金公告栏目；聚合站（集思录等需登录/会员） | 每日，文件形式（xls/txt/PDF），**无公开稳定批量 JSON 接口** | ⚠️ 官方免费但需逐日逐基金抓取文件；**V1 不依赖，用季报近似**（结论详见 §PCF） |
| ③ | 个股日行情 | **腾讯财经** `web.ifzq.gtimg.cn/appstock/app/fqkline/get` | 每日盘后；无需 token | ✅ 可用（东财 push2 对脚本 IP 限流频繁，腾讯更稳） |
| ③ | 个股流通市值 | **腾讯财经** `qt.gtimg.cn/q=`（快照字段 45） | 实时快照，当日口径 | ✅ 当日可用；**历史流通市值序列免费渠道缺失**（见 §个股） |
| 附 | ETF 单位净值（V1 公式所需） | **天天基金** `api.fund.eastmoney.com/f10/lsjz`（需 Referer） | 每日盘后，历史全量 | ✅ 可用 |

**V1 链路数据齐备性结论**：`Δ份额(沪) ✅ + Δ份额(深, 需自建积累) ⚠️ + 净值 ✅ + 季报权重 ✅(行业主题完整/宽基前100) + 流通市值当日 ✅` → **V1 用「沪市官方份额 + 季报持仓 + 当日流通市值」可跑通全链路，零成本**；深市份额历史与宽基全成分权重是两处已知缺口（均有明确的低成本补齐方案，见调研笔记）。

---

## 1. ETF 每日份额变化

### 1.1 上交所官方接口（首选，✅ 可用）
- **接口**：`GET https://query.sse.com.cn/commonQuery.do`
  - 参数：`sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L`、`STAT_DATE=YYYY-MM-DD`、`isPagination=true`、`pageHelp.pageSize=10000` 等（见 `poc_fetch_data.py`）
  - **必须带请求头 `Referer: https://www.sse.com.cn/`**，否则返回 System Error
- **返回**：JSON `result[]`，覆盖全市场沪市 ETF（实测 2026-10-29 为 923 只），字段：
  | 字段 | 含义 | 单位 |
  |---|---|---|
  | `SEC_CODE` / `SEC_NAME` | 基金代码 / 简称（如 510300 / 300ETF） | — |
  | `ETF_TYPE` | 单市 / 跨市 | — |
  | `STAT_DATE` | 统计日期 | YYYY-MM-DD |
  | `TOT_VOL` | 基金份额 | **万份**（×10000 = 份） |
- **频率/延迟**：每日披露；实测 2026-10-30 17:40 时最新可查 `STAT_DATE=2026-10-29`（T 日数据在 T 日晚间 19:00–20:00 或 T+1 早间更新，与深交所 xlsx 页脚注"T 日晚间更新仅供参考，以 T+1 早间为准"口径一致）。
- **历史**：✅ 支持任意历史 `STAT_DATE` 回溯（非交易日返回空 result）。POC 已回拉 2026-09-21 → 2026-10-29 约 28 个交易日、13 只样本 ETF。
- **对账**：2026-10-29 `TOT_VOL=3747558.77 万份`（510300）＝ 东财 push2 `f84=37,475,587,584 份`，**逐份一致** ✅（详见 `docs/m1-数据源调研笔记.md`）。
- **限制**：单次返回全市场（~923 行），无按代码过滤参数（客户端过滤即可）；请求频率未触发限流（POC 30 次间隔 0.25s 正常）。

### 1.2 深交所官方接口（⚠️ 仅最新快照）
- **接口**：`GET https://fund.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1000_lf&TABKEY=tab1`（需 `Referer: https://fund.szse.cn/...`）
- **返回**：xlsx（openpyxl 解析，1083 行），字段：`基金代码/基金简称/基金类别/投资类别/上市日期/当前规模(份)/基金管理人/基金发起人/基金托管人/净值`。
- **频率/延迟**：每日 T-1 快照（页脚注：T 日晚间更新的 T 日规模仅供参考，以 T+1 早间更新为准）。
- **历史**：❌ `txtDate` 参数实测被忽略（subname 恒为最新日期）→ **深市份额历史需每日定时落库自建**（这正是 M2 ETL 定时任务要做的；上架前如急需历史，可临时用 Tushare `fund_share` 积分接口一次性回填）。
- 样例：159915 创业板ETF易方达 当前规模 36,384,454,936 份（2026-10-29 口径）。

### 1.3 备查/对账源
| 渠道 | 接口 | 字段 | 频率 | 限制 |
|---|---|---|---|---|
| 东方财富快照 | `push2.eastmoney.com/api/qt/stock/get?secid=1.510300&fields=f57,f58,f43,f84,f116,f117` | `f84` 总份额（份）、`f116` 总市值 | 实时快照 | **IP 限流敏感**（本机脚本实测被 Push2RateLimit 封禁；浏览器/换 IP 可用） |
| 天天基金 F10 规模变动 | `FundArchivesDatas.aspx?type=gmbd&code=510300` | 报告期/期间申购/期间赎回/期末总份额(亿份)/期末净资产(亿元) | 季度 | 无日频 |

---

## 2. ETF 持仓与权重（季报）

### 2.1 天天基金 F10 持仓明细（✅ 可用，V1 权重首选）
- **接口**：`GET https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={code}&topline=100&year={yyyy}&month=`
  - **必须带 `Referer: https://fundf10.eastmoney.com/jjcc_{code}.html` + `X-Requested-With: XMLHttpRequest`**（否则 404）；返回 GBK 编码的 `var apidata={content:"<html>…"}` JS 片段。
  - akshare `fund_portfolio_hold_em(symbol, date="2026")` 是同一接口的封装（已实测可用，但会混入全年各季度、且列顺序不稳定，建议自行按"季度"标签过滤最新季度）。
- **字段**（每季一张表）：`序号/股票代码/股票名称/占净值比例(%)/持股数(万股)/持仓市值(万元)/季度`。
- **频率/延迟**：季报披露（2026Q3 截止日 2026-09-30，2026-10-26 前后在天天基金可查）；标签含披露报告期，如 `2026年3季度股票投资明细`。
- **权重口径**：`占净值比例` 即权重（股票市值/基金资产净值），对指数 ETF 约等于指数权重；V1 公式 `权重(e,i,t)` 直接取该字段。
- **覆盖度（重要限制）**：
  - 服务端**每季最多返回前 100 大重仓**（topline 上限实测为 100）；
  - 行业主题 ETF（证券/半导体/芯片/光伏/军工/酒/医疗等）成分股通常 ≤100 只 → **全成分覆盖，权重合计 ~99%** ✅；
  - 宽基 ETF（沪深300 300 只、创业板指 100 只等）→ 前 100 只覆盖约 63%–87% 权重，**尾部成分缺失** ⚠️（补齐方案见调研笔记：中证指数官网成分权重表 / PCF / 指数编制公司披露，建议 V1 先接受前 100 近似并标注口径，V2 升级）。
  - 实测 2026Q3 数据在披露初期仅回填了部分行（如 510300 仅 33 行、159915 仅 76 行），**抓取时需按最新季度过滤并校验权重合计**，且应重试补齐。
- **披露日期**：接口不直接给公告日，报告期标签 + 基金定期报告公告（天天基金 F10 公告栏 `f10/JJGG` type=3，akshare `fund_announcement_report_em`）可查精确披露日期。

### 2.2 东财移动端持仓（备选，❌ 不推荐）
- `https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition?FCODE=510300&pageIndex=1&pageSize=10`：仅返回**前 10 大**（pageSize>10 实测被限流报错），字段含 GPDM/GPJC/JZBL/INDEXNAME。

### 2.3 PCF 每日申赎清单（结论：V1 不可依赖，V2 评估）
| 渠道 | 现状（2026-10-30 实测） | 结论 |
|---|---|---|
| 上交所官网 | 披露页 `https://www.sse.com.cn/disclosure/fund/etflist/` 存在，PCF 文件按基金/日期逐份下载（xls/txt）；页面 JS 渲染，未见公开文档化的批量 JSON 接口 | ⚠️ 官方免费但需文件级爬取（文件名规则未公开，需解析页面动态加载接口） |
| 深交所官网 | ETF 公告/申赎清单栏目（`fund.szse.cn` 基金公告），同样为公告文件形式 | ⚠️ 同上 |
| 天天基金/东财 | F10 无 PCF 结构化数据接口 | ❌ 无 |
| 集思录等聚合站 | ETF 数据页有 PCF 栏目，完整清单/接口需注册登录甚至会员 | ⚠️ 非免费开放 |
| Tushare | 申赎清单/成分权重类接口需高积分（5000+），需 token | ❌ 本轮不依赖；付费备选 |

**结论：PCF 每日申赎清单没有"免费、稳定、批量、结构化"的公开渠道。V1 按计划书 §3.2 用「季报持仓权重」近似；V2 若需日频精度，优先评估上交所 PCF 文件爬取（文件级、可自动化但脆弱）或 Tushare 积分。**

---

## 3. 个股日行情与流通市值

### 3.1 腾讯财经日 K（✅ 可用，个股行情首选）
- **接口**：`GET https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{count},qfq`
  - `symbol` 形如 `sh600519` / `sz300750`；`qfq` 前复权；返回 `data[symbol]["qfqday"]`。
- **字段**：每行 `[日期, 开盘, 收盘, 最高, 最低, 成交量(手)]`。
- **频率/延迟**：每日盘后更新；无 token、无 Referer 要求；实测 15 只个股 30 根 K 线全部成功。
- **备选**：东财 `push2his.eastmoney.com/api/qt/stock/kline/get`（字段更全：振幅/涨跌幅/换手率），但**本机脚本 IP 被限流**（akshare Push2RateLimitError），生产环境需限速+多 IP 轮换；新浪 `money.finance.sina.com.cn CN_MarketData.getKLineData` 亦可用（仅 OHLCV）。

### 3.2 流通市值（✅ 当日快照可用；❌ 免费历史序列缺失）
- **接口**：`GET https://qt.gtimg.cn/q=sh600519,sz300750,...`（GBK 编码，`~` 分隔）
  - 字段索引：`[2]` 代码、`[3]` 现价、`[44]` 流通市值(亿元)、`[45]` 总市值(亿元)。
    ⚠️ 顺序易搞反：腾讯 qt 快照 idx44 是**流通市值**、idx45 是**总市值**（已用中国平安 52.80 元 × 流通A股 108.3 亿 ≈ 5628.5 亿 = idx44、× 总股本 182.1 亿 ≈ 9560.8 亿 = idx45 交叉验证）。
- **结论与建议**：
  - V1 指标「流入强度 = 净流入 ÷ 流通市值」的**分母建议用当日/最新流通市值快照**（每日盘后取一次即可，与产品盘后 T+1 节奏一致）；
  - 若需历史流通市值序列：免费渠道实测均不可得（东财 kline 的 f116/f117 仅快照接口支持、历史 K 线不含市值；乐咕乐股旧接口已 404；akshare `stock_zh_a_hist` 无市值列）→ 可用近似法：`流通市值(t) ≈ 当日流通市值 × 收盘价(t)/收盘价(当日)`（忽略股本变动，误差小），或付费源（Tushare `daily_basic` 需积分）。

### 3.3 东财 push2 快照（备选，⚠️ 限流）
- `push2.eastmoney.com/api/qt/stock/get?secid={m}.{code}&fields=f57,f58,f43,f84,f85,f116,f117`：个股/ETF 通用，`f116/f117` 总市值/流通市值（元），ETF 的 `f84` 即总份额。**实测浏览器/web_fetch 可用，Python 脚本连续调用会被 IP 限流**，仅建议低频对账使用。

---

## 4. 附：ETF 单位净值（V1 公式所需）

- **接口**：`GET https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize={n}`（需 `Referer: https://fundf10.eastmoney.com/`）
- **字段**：`FSRQ` 日期、`DWJZ` 单位净值、`LJJZ` 累计净值、`JZZZL` 净值增长率、`SGZT/SHZT` 申赎状态。
- **频率/延迟**：每日盘后（与基金公司披露同步）；历史全量（510300 实测 3520 条）。
- V1 公式用 `单位净值(e, t-1)`：取前一交易日 DWJZ 即可。

---

## 5. 样例 CSV 一览（`samples/`，2026-10-30 生成）

| 文件 | 内容 | 行数说明 |
|---|---|---|
| `etf_shares_sse_history.csv` | 13 只沪市 ETF（12 只样本 + 自动发现的 A500 ETF 563360）× 23 个交易日的官方份额（万份 + 份） | 长表 |
| `etf_shares_szse_latest.csv` | 5 只深市 ETF 最新 T-1 份额快照（份） | 快照 |
| `etf_holdings_latest_quarter.csv` | 18 只 ETF 最新季度（2026Q3）前 100 大持仓（权重/持股数/市值） | 长表 |
| `etf_holdings_all_quarters.csv` | 同上，含 2026 Q1–Q3 全部季度（用于权重降级回退） | 长表 |
| `stock_quotes_daily.csv` | 15 只个股 × 30 根日 K（腾讯，前复权） | 长表 |
| `stock_floatcap_snapshot.csv` | 15 只个股当日总市值/流通市值（亿元） | 快照 |
| `etf_nav_history.csv` | 18 只 ETF 近 25 个交易日单位净值 | 长表 |

复现：`python poc_fetch_data.py`（依赖仅 openpyxl/lxml/标准库，无 token）。

---

## 6. 数据源风险与运维建议

1. **深市份额历史缺口**：M2 ETL 需设计每日盘后落库任务（深交所 xlsx 每日抓一次，T-1 口径入库），上架前回填可一次性用 Tushare `fund_share`。
2. **东财系接口限流**：push2 系列对高频脚本 IP 有分钟级封禁；天天基金 F10/lsjz 目前友好（POC 全程无 403），生产需限速 + 指数退避 + 多源冗余。
3. **季报持仓时效**：季报滞后 1–2 个月 + 披露初期行数不全 → 每次抓取必须校验「权重合计 ~95–100%」与「最新季度行数 ≥ 指数成分数」，不达标重试或降级用上一季度。
4. **上交所接口依赖 Referer**：生产环境务必保留请求头，否则接口直接报错。
5. **口径披露**：产品侧必须标注「份额变化为交易所披露口径，资金流为估算值」（对应计划书 §2.4）。
