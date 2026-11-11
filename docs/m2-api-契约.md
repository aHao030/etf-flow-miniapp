# M2 API 契约 v1.2（Lead 已评审）

- 作者：backend-dev｜日期：2026-10-30｜状态：v1.0 已冻结并评审通过；v1.1、v1.2 为 Lead 评审确认的补丁（见文末变更记录），作为 M2 实现与 M3 前端对齐的唯一依据
- 依据：《m1-模型说明-v1.md》口径、《m1-验证报告.md》§7 遗留移交、《m2-后端方案预研.md》、task-3/4 要求
- 定位：契约与具体技术路线无关；云开发路线用云函数路由转发同一套路径与 JSON 结构

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 协议 | REST + JSON（UTF-8），路径前缀 `/api/v1` |
| 金额单位 | **一律「元」**（JSON number，保留 2 位小数）；前端展示自行转万元 |
| 强度单位 | bp（number，保留 2 位）；份额「份」；市值「元」 |
| 日期格式 | `YYYY-MM-DD`（交易日历） |
| **trade_date** | 数据截至日期：本响应内所有数值所对应的最新交易日（每个接口必返） |
| **latest_date** | 榜单最新日期指针：服务端维护的「当前可用数据日期」，每日任务成功后原子更新；前端不传 trade_date 时所有接口默认取 latest_date |
| 空值语义 | 有效天数不足（5日<3 / 20日<12）或数据缺失的指标返回 `null`（非 0），前端显示「—」 |
| 分页 | 入参 `page`（从 1 起）、`page_size`（默认 20，上限 100）；响应含 `items[]`、`total`、`page`、`page_size`、`has_more` |
| 响应信封 | 成功 `{"code":0,"message":"ok","data":{...}}`；失败 `{"code":<业务码>,"message":"...","data":null}` |
| 口径披露 | 文案随 `/meta` 下发（短/长），前端强制常驻，不硬编码 |

## 2. 接口清单

### 2.1 `GET /api/v1/meta` — 元信息（前端启动必调）

| 参数 | 无 |
|---|---|

`data`：`latest_date`（string）、`prev_trade_date`（string）、`app_version`、`industry_dict_version`（行业字典版本，用于缓存校验）、`units:{money:"元",intensity:"bp"}`、`windows:["1d","5d","20d"]`、`szse_accumulating`（bool，深市份额历史积累中，true 时前端展示「深市 ETF 历史积累中」）、`disclosure:{short,long,disclaimer}`（口径披露文案，终稿与 qa-compliance 对齐；disclaimer 为独立免责声明句，文本=docs/m5-提审材料包.md §1③ 定稿）。

### 2.2 `GET /api/v1/health` — 健康检查与数据就绪状态

| 参数 | 无 |
|---|---|

`data`：
- `status`：`"ok"` ｜ `"degraded"`（部分数据缺失但可服务）；
- `latest_date`、`prev_trade_date`；
- `pipeline`：`{sse_share_date, szse_share_date, holdings_date, computed_date, cached_date}` —— 上交所份额/深交所份额/持仓权重/计算/缓存各自落库到的最新日期；
- `szse_accumulating`（bool）；
- `message`（前端可直接展示的提示文案，如「今日数据计算中，预计 21:00 前更新」）。

用途：前端冷启动与下拉刷新时判断是否展示「数据未更新」提示；任务失败日保留旧数据不阻断服务。

### 2.3 `GET /api/v1/rank` — 榜单

| 入参 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `period` | enum | 否 | `1d`（默认）｜`5d`｜`20d` |
| `industry` | string | 否 | 行业代码（来自 2.6 字典）；缺省=全市场 |
| `direction` | enum | 否 | `in`（流入榜，默认）｜`out`（流出榜，预留） |
| `trade_date` | string | 否 | 缺省=latest_date，支持历史回看 |
| `page` / `page_size` | int | 否 | 分页（榜单 TOP 100 以内） |

`data`：`trade_date`、`period`、`industry`、`industry_name`、`direction`、`total`、`page`、`page_size`、`has_more`、`items[]`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `rank` | int | 名次（从 1 起） |
| `stock_code` / `stock_name` | string | 如 `688981` / `中芯国际` |
| `industry` / `industry_name` | string | 申万一级（M2 落地，三方对齐项） |
| `flow` | number | 估算净流入（元）；direction=out 时为负值 |
| `intensity_bp` | number | 流入强度（bp）；分母缺失返回 null |
| `valid_days` | string | `"5/5"` 形式；不足下限的股票不入榜 |
| `flags` | string[] | 事件标记（见 2.4 枚举），空数组=无 |

排序：`direction=in` 按 flow 降序；`out` 按 flow 升序（最负在前）。

### 2.4 `GET /api/v1/stock/{code}` — 个股详情

| 入参 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | path | 是 | 6 位股票代码 |
| `trade_date` | query | 否 | 缺省=latest_date；60 日序列以该日为截至 |

`data`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `trade_date` | string | 数据截至日期 |
| `stock_code` / `stock_name` / `industry` / `industry_name` | string | 基本信息 |
| `float_mcap` | number | 流通市值快照（元，强度分母，A7） |
| `flow_1d` / `flow_5d` / `flow_20d` | number/null | 估算净流入（元）；有效天数不足为 null |
| `intensity_1d_bp` / `intensity_5d_bp` / `intensity_20d_bp` | number/null | 流入强度（bp） |
| `valid_days` | object | `{d1:"5/5", d5:"5/5", d20:"12/20"}`，与模型有效天数口径一致 |
| `series` | array[60] | 60 日序列，`{trade_date, flow, intensity_bp}`（单日口径，不足 60 日按实际条数） |
| `top_etfs` | array[≤5] | ETF 贡献拆解 TOP5，按 |flow| 降序：`{etf_code, etf_name, flow, share_change, nav, weight, weight_source, weight_date}` |
| `quality` | object | **质量标记（与模型 quality_marks 一一对应）**：`{weight_source, coverage, flags[], note}`，见下 |
| `data_status` | enum | `"ok"` 完整 ｜ `"sse_only"` 仅有沪市贡献（深市积累中）｜ `"accumulating"` 深市贡献暂缺（快照起步，无日频数据）｜ `"no_data"` 不在覆盖池 |
| `note` | string/null | 附加说明（如「上市未满 60 交易日，暂不纳入测算」） |

`quality` 字段说明（对应模型说明 §4 异常处理）：
- `weight_source`：`"index"`（中证指数官网）｜`"quarterly"`（季报持仓）｜`"none"`（权重缺失）；
- `coverage`：number，权重覆盖率 c(e,t)，0–1；无数据为 null；
- `flags` 枚举：`split_adjusted`（份额折算/拆分，当日 Flow 置 0）、`large_flow`（大额申赎 |Δ份额|/份额>30%）、`listing_period`（建仓期）、`delisting`（清盘）、`weight_missing`（权重缺失）、`nav_missing`（净值缺失）、`share_gap`（份额数据缺口）、`weight_stale`（权重过期>6个月）、`cash_substitute`（现金替代）、`suspended`（长期停牌）；
- `note`：string，人类可读的标记解释（可空）。

### 2.5 `GET /api/v1/etf/{code}` — ETF 详情

| 入参 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | path | 是 | 6 位 ETF 代码 |
| `trade_date` | query | 否 | 缺省=latest_date |

`data`：`trade_date`、`etf_code`、`etf_name`、`exchange`（`"SSE"`｜`"SZSE"`）、`category`（`宽基`｜`行业`｜`主题`）、`share_total`（份）、`share_change`（Δ份额，份；折算日置 0）、`nav_prev`（NAV(t−1)，元）、`est_flow`（本 ETF 当日估算净流入=Δ份额×NAV(t−1)，元）、`weight_source`、`weight_date`（权重生效日）、`coverage`（number/null）、`share_series`（array[≤60]：`{trade_date, share_total, share_change}`，60 日份额与 Δ份额序列）、`scale_series`（array[≤60]：`{trade_date, scale}`，scale=份额×NAV，元，60 日规模走势）、`top_stocks`（array[≤5]：`{stock_code, stock_name, flow, weight}`，本 ETF 对成分股贡献 TOP5）、`note`（如建仓期/清盘/深市积累中）、`flags[]`。

序列口径（v1.1 补丁）：`share_series` 与 `scale_series` 以 `trade_date` 为截至、升序返回，不足 60 日按实际条数；`share_change` 为相邻两日份额差（首日无前值返回 null）；`scale` 用同日 NAV，缺失时取最近前值；份额或 NAV 均缺失则该点 scale 为 null。

### 2.6 `GET /api/v1/industries` — 行业字典

`data`：`version`、`updated_date`、`items[]`：`{industry_code, industry_name, stock_count}`（申万一级，按行业名排序；stock_count=该行业在覆盖池内股票数）。供榜单筛选器与详情展示，前端按 version 缓存。

### 2.7 `GET /api/v1/etfs` — ETF 覆盖清单

| 入参 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `category` | enum | 否 | `宽基`｜`行业`｜`主题`；缺省=全部 |
| `page` / `page_size` | int | 否 | 分页 |

`data`：`trade_date`、`total`、`page`、`page_size`、`has_more`、`items[]`：`{etf_code, etf_name, category, exchange, status, listing_date, weight_source, weight_date}`；`status`：`active`（测算中）｜`accumulating`（深市快照起步，历史积累中）｜`new`（建仓期）｜`excluded`（债券/货币/商品/QDII/联接等，A8 过滤）。前端 ETF 页据此展示。

### 2.8 自选 / 提醒（预留设计，M2 实现范围待用户决策）

- `GET/POST/DELETE /api/v1/watchlist`：自选增删查，`{type:"stock"|"etf", code}`；需 openid（wx.login 换）。
- `POST /api/v1/subscribe/authorize`：前端 `wx.requestSubscribeMessage` 后回传 `{template_id, granted}`，服务端记授权余量。
- `POST /api/v1/internal/notify/run`：内部接口（服务端鉴权，仅定时任务调用），对自选股当日 flow 超阈值者调 `subscribeMessage.send`，记 `push_log`；errcode 43101 清授权。
- **约束**：个人主体下按一次性订阅设计（授权一次推一次，前端引导重复授权）；每用户每日 ≤1 条汇总推送；推送窗口 20:00–21:00。
- 若用户决策 M2 不做 openid 版，此节冻结，前端先用本地缓存自选。

## 3. 错误码

| HTTP | code | 含义 |
|---|---|---|
| 200 | 0 | 成功 |
| 400 | 40001 | 参数缺失/格式非法 |
| 400 | 40002 | 枚举取值非法（period/direction/category） |
| 400 | 40003 | 分页越界（page<1 或 page_size>100） |
| 404 | 40401 | stock_code 不存在或不在覆盖池 |
| 404 | 40402 | etf_code 不在覆盖清单 |
| 404 | 40403 | industry 不存在 |
| 404 | 40404 | trade_date 无数据（非交易日或未落库） |
| 429 | 42901 | 请求过于频繁 |
| 503 | 50301 | 服务降级：数据未就绪（如 ETL 失败、latest_date 未推进），前端按 health 提示 |
| 500 | 50001 | 服务器内部错误（记录日志并告警） |

## 4. 缓存与时序约定（实现依据）

- **latest_date 切换**：每日任务全链路成功后，原子写 `latest_date` 指针 + 全量榜单/详情缓存；失败保留旧值，接口照常服务旧数据（503 仅发生在首次冷启动无任何数据时）。
- **Redis key**（实现参考，契约级仅约定行为）：`rank:{period}:{industry}:{date}`、`stock:{code}:{date}`、`etf:{code}:{date}`、`latest_date` 指针；TTL 72h；Redis 不可用直查 MySQL 预计算表。
- **M2 管道三项（验证报告 §7 移交，影响 health 的 pipeline 字段）**：
  1. 深市份额每日落库：盘后 20:30 拉深交所 T-1 快照入 `szse_share_date`，次日 8:00 补拉一次核对；
  2. 季报披露窗口（1/4/7/10 月末）每日重拉持仓至覆盖率达标（≥95% 或回退上一完整季），推进 `holdings_date`；
  3. 历史流通市值近似法：`流通市值(t) ≈ 当日流通市值 × 收盘价(t)/收盘价(当日)`，回算强度序列分母（A7 移交）。
- **请求头合规**：上游采集保留 Referer（sse.com.cn / fund.szse.cn / fundf10.eastmoney.com）；东财 push2 仅低频对账用，主链不依赖。

## 5. 待评审/待确认项

1. 榜单 TOP N 与分页上限（现拟 TOP100 / page_size≤100）是否满足前端；
2. `industry` 用行业代码入参、申万一级落地与归属数据来源（三方对齐 Q4）；
3. `series` 60 日是否满足图表需求（task-4 已按 60 日设计，确认即可）；
4. 自选/提醒节是否 M2 实现（预研 Q6，待用户决策）；
5. `direction=out` 流出榜 M2 是否实现（字段已预留，MVP 可只做 in）；
6. 错误码 50301 与 health 的联动是否符合前端预期。

## 6. 变更记录

- **网页个股监测扩展（2026-11-03）**：`GET /api/v1/stock/{code}?period=` 增加 `3m/6m/1y/2y/3y/5y`，保留 `1d/5d/20d`；榜单仍使用原三个周期。自然月/年窗口为锚日开区间至截止日闭区间，交易日数来自正式日历。`movement` 增加 `window_start/window_end`、`start_exclusive`、`available_start/available_end`、`missing_days`、`observed_net_flow`，分别表达请求范围、边界语义、实际记录范围、缺口及已有部分累计。长周期未完整时 `net_flow/inflow/outflow=null`，不能把部分累计冠以完整周期。日历不足时预期和缺失天数为 null。`series` 按所选范围返回已有日记录，取消固定 60 行；不补零。`meta.stock_periods` 与 `meta.stock_windows` 描述支持周期及其真实日历窗口。历史仍为 `current_weights` 近似，不是历史时点持仓模型。

- **v1.2（2026-10-30，Lead 评审确认，S1 裁决）**：§2.1 `disclosure` 新增 `disclaimer` 字段（独立免责声明句，文本=docs/m5-提审材料包.md §1③ 定稿，供「我的」免责声明卡片展示）。
- **v1.1（2026-10-30，Lead 评审确认）**：§2.5 ETF 详情新增 `share_series`（≤60 项 `{trade_date, share_total, share_change}`）与 `scale_series`（≤60 项 `{trade_date, scale}`，scale=份额×NAV 元）两个序列字段，口径与 stock 的 series 一致（trade_date 截至、不足 60 日按实际条数）。另确认：① 自选列表行数据由前端逐个调详情接口（N+1 可接受，不设批量接口）；② 流出榜 MVP 不实现（direction=out 保持预留）。
- **v1.0（2026-10-30）**：初版，Lead 评审通过后冻结。
