# 独立 ETF 历史数据仓库

ETF 查询目标从2004-12-30起，实际份额、净值、估算各自保留来源日期与覆盖范围，不再把2021当固定历史下限。指数行情另在 index_history.db，最早可到1990-12-19；指数早期回溯、正式发布日期、ETF成立/上市时间不是同一概念。

2026-11-10本轮核验：目录已扩至1837只；沪市2012—2020年2187个交易日任务全部success，份额最早有效日期2012-01-04。2004—2011年及深市更早份额抽样尚无可用源，未生成零记录。早期净值391只基金作业中380项已成功而跳过、7项新增成功、3项pending、1项失败，部分净值最早至2004-12-30。这是本轮状态，后续以数据库覆盖和发布meta为准；不能将按日作业success视为所有基金完整。

历史估算保留上市边界保护：交易所目录提供上市日期时，上市前及跨上市边界的份额变化归为 `fund_event`，估算留空，原始份额及净值不删除。`first_seen` 不代替上市日期。上市后仍只是按已验证输入计算的估算，不表示已核验建仓完成；未知上市日期不推断。

从 `server/` 运行，默认 SQLite 为 `server/data/market_history.db`，可用 `ETF_HISTORY_DB_PATH` 覆盖。它不修改原应用库。依赖现有 Python 环境（akshare、openpyxl、requests）。

```powershell
python -m history.backfill --dataset shares --start 2012-01-04 --end 2020-12-31
python -m history.backfill --dataset nav --start 2004-12-30 --end 2020-12-31 --max-jobs 100
python -m history.coverage --start 2004-12-30 --end YYYY-MM-DD
```

历史采集固定相同日期范围反复运行；已成功任务跳过，失败按日期/基金重试；净值每次最多尝试 100 个基金，直至不再有待处理基金。深市首轮为旧包装器的结果时，`--dataset shares-sz-verified` 用交易所原始 XLSX 复核行、基金集合及份额。沪市的份额单位换算自万份，深市来源直接为份。交易所响应为空或日期不匹配记录失败，不能按零处理。交易日日历从新浪公开历获取，不能用普通工作日代替。

日常任务先按北京时间核验开市和收盘，周末或休市日直接退出，不采集、不历史回填、不导出发布。index_history.db的calendar已缓存完整来源日历，本轮覆盖1990-12-19至2026-12-31；market_history.db日历可能只有已请求区间。当天超出缓存范围时只核验日历，不能按普通工作日猜测开市。交易日15:30至23:30每小时核对最新可得数据，未披露属于pending。页面分别显示实际份额、净值、资金和指数覆盖日期；查询目标日、采集时间与快照生成时间保留各自含义。内容指纹相同则复用已验证版本。

有效运行开始前创建桌面ETF更新日志/YYYY-MM-DD，全部采集、重试、覆盖、审查、导出、发布和汇总按etf-heartbeat-YYYYMMDD-HHMM-*保存，重复运行加唯一后缀。令牌和认证头不进入任何日志。

确认交易日已收盘后，使用来源交易日历中截至目标日的最近五个交易日作为窗口，以下WINDOW_START、YYYY-MM-DD替换成真实日期：

```powershell
python -m history.backfill --dataset all --start WINDOW_START --end YYYY-MM-DD --max-jobs 3000
```

`all` 先刷新深交所当前 ETF 目录、沪深两市份额，再限量采集近期仍有观测且净值最旧的基金；当 `all` 窗口不超过10个交易日时，沪市普通ETF、沪市交易型货币基金及深市ETF份额每天重抓并更新，以吸收晚间修订；单独历史份额回填及较长窗口继续跳过已成功日期。相同净值窗口已成功的任务不占配额。未指定 `--max-jobs` 时仅净值默认 100。完成历史份额后须再次运行同一固定范围的 NAV 采集，补入历史回填中新发现的基金。采集过程会打印结果；存在失败时进程以非零状态结束。历史日和净值作业记录分别为 `shares`（沪市普通ETF）、`shares-sh-money`（沪市交易型货币基金）、`shares-sz`、`nav-v2`；`jobs` 可按状态与日期核对。对于 NAV 上游明确不提供普通单位净值的类型，记录 `unsupported`；尚未披露的净值记录 `pending` 并可重试，未经证实的空响应应记录失败。

成功状态仅表明当前源返回的记录通过检查并入库，不证明全基金、全日期完整；须另用存续期和独立来源核对。`funds.first_seen/last_seen` 是本仓库观察日期，不是上市/退市日期；未见到基金不能推断已退市。深市目录快照的份额日期未经验证，不写入日频历史。此仓库不含历史组合权重，也不生成个股资金流；ETF 价格、净值、份额和持仓权重不是同一种数据。

已知抽样没有可用源的更早份额不要逐个普通工作日盲目重试；重试仅针对未完成且有明确可用来源的基金/日期。HTTP514、403或其他明确访问拒绝记录状态，不切换代理、IP、域名或降低安全设置规避。




NAV 日更：对于最新已知交易日的在列基金，即使当日份额尚未到达，若当日已核验单位净值缺失，作业仍标记 `pending` 以便晚间重试；当日净值先到时可记录 `success`，不以份额是否出现作为前提。明确 `unsupported` 的产品仍保留该状态。已知上市日在目标日之后或退市日在目标日及以前时，不因缺净值标记为在市待补；上市日未知时，当前在列的首见日若晚于目标日也不会外推为当日已上市。针对早于最新交易日的固定历史范围，不仅因当前在列就标记 `pending`；原有“目标日已有份额而净值缺失”的核验仍然保留。`pending` 仅表示目标日净值尚未被核验，不断言一定会有披露。

## 个股当前权重回溯快照

本节为原app.db及旧stock快照兼容流程；当前网页主个股视图已经改用下节market全目录披露持仓。旧流程成功与否不能替代全目录覆盖审核，也不能阻止独立核验通过的新三类快照更新。

完成日常历史更新以及原个股管线后，确认原管线 `computed_date` 为目标交易日，再在 `server/` 运行：

仅在确认是交易日且收盘后执行；周末和休市日跳过。历史库日历只覆盖已请求范围，若当天晚于其最大日期，先仅刷新交易日历确认，不能直接认定休市。

```powershell
python scripts/build_stock_history_snapshot.py --expected-date YYYY-MM-DD
```

脚本从原库和历史库只读构造独立副本，仅使用原库已启用的沪市基金及当期权重。按历史数据的净值身份、事件、异常和连续交易日规则过滤：某股当日有任一当前已启用持仓 ETF 数据不可用时，该股当日不作为完整估算。数据不补零；当前权重回溯不构成历史持仓或策略回测。历史交易强度缺少可靠市值时留空。已上市不足五年的基金以及未启用的基金会使部分个股周期不完整。

构建要求现有原模型历史逐日与质量门槛一致，并在导出前后核对数据源指纹。快照先在隔离目录生成，通过分片大小、每个周期金额、Worker 上传及代表接口检查后，才放入 `.local/snapshots` 并打印 `verified_snapshot=`；不会自行激活或发布。输入未变时重验既有快照全部摘要并输出 `unchanged_source=true`。只有完整验证成功才使用返回路径；管线未更新到目标日、历史源变化或质量对账失败时停止并保留旧版。

构建时在 `.local/stock-history-builds` 生成隔离数据库，当前实测约需 1.4 GB，另需临时磁盘导出空间和约 82 MB 快照空间。验证成功且快照及指纹记录写入后，仅清理当次构建的三个数据库及其 WAL/SHM；失败构建保留以便审查。既有旧构建不会自动删除。配套 Worker 必须支持紧凑序列并经构建；旧版快照仍可读取。

## 指数、宽基与全目录披露持仓快照

`index_history.db` 保存指数日线、源交易日历、逐只核验的跟踪指数映射及场外基金披露期份额。只在数据采集完成后导出；导出器使用 SQLite 一致性读事务。先完成并验证历史快照，再把该快照的绝对目录传入下面的 `--history-snapshot`，使 ETF 因素和宽基/个股数据引用同一个不可变版本。

使用项目Python日更指数与持仓，使用已安装Scrapling Python补新基金指数映射。持仓默认复用7天内已核验结果，不日内强制全量刷新：

```powershell
python scripts/collect_index_history.py --dataset indices --end YYYY-MM-DD --report 当天桌面指数覆盖报告路径
python scripts/collect_market_holdings.py --asof YYYY-MM-DD --workers 2 --interval 0.5
C:\Users\11200\.local\share\scrapling\.venv\Scripts\python.exe scripts/collect_index_history.py --dataset mappings --new-only --report 当天桌面映射报告路径
```

场外候选新增/季度披露时先用项目Python运行 `scripts/collect_index_history.py --dataset off-catalog`，再用Scrapling Python运行 `scripts/collect_off_exchange.py --report 报告路径`。默认断点复用已取得报告，新季度确需刷新旧成员时添加 `--refresh-reports`；不要每日重新抓全历史，不使用首批/代表样本数量上限。来源及份额类边界见 `INDEX_SOURCES.md`。

```powershell
python scripts/export_history_snapshot.py ../web-trial/.local/history-snapshots
node ../web-trial/tests/history-snapshot-check.mjs 已导出历史目录
python scripts/export_broad_snapshot.py ../web-trial/.local/broad-snapshots --history-snapshot 已验证历史目录
python scripts/export_market_snapshot.py ../web-trial/.local/market-snapshots --history-snapshot 已验证历史目录
node ../web-trial/tests/broad-snapshot-check.mjs 已导出宽基目录
node ../web-trial/tests/market-snapshot-check.mjs 已导出全市场目录
```

宽基按当前核验的跟踪指数分组，所有组按基金代码去重；未知存续期或缺失因素时只展示已有净额小计，不声称完整合计。指数与 ETF 有各自的起始日期。场外报告保留原始申赎份额和合并代码，按披露期分页；不与场内日频资金合并，也不把 A/C 类当成可直接相加的独立净流。

当前支持16个常用宽基/市场基准，按实际已核验映射选择所属ETF；可用指数与成员数看broad meta。场外完整报告独立分片并分页，不能静默只导出最近几季。目标指数范围仍不代表市场上所有指数类型均已覆盖。

历史/market因素每基金最多8000日期，broad最多12000交易日；每个新快照最多500文件、每文件不超过2MiB，历史导出按约1.8MB分片。超限停止并调整协议，不截断记录。日频横轴按核验交易日压缩，缺失的开市日仍留空；场外报告期末是期间标签，即使落在休市日也保留。

全市场个股视图使用 `stock_holdings.db` 中已经校验的已披露 A 股持仓。快照将持仓和 ETF 历史因素分开保存，按单只股票请求有界读取，不构造庞大的 ETF×股票×日期明细库。未披露持仓、未核验基金和源中未找到 A 股持仓的基金仍在全目录中列出状态。没有发现持仓不代表实际没有持仓；目前的资金金额是已披露持仓贡献小计，完整总额留空。历史仅按当前披露权重回溯，不是历史时点持仓。

通过验证后，使用 `set_local_view.py --stock 原已验证版本 --history 同一历史版本 --broad 宽基版本 --market 全市场版本` 原子切换。本机服务仍仅监听 `127.0.0.1`。远程各自使用 `publish_broad_snapshot.py`、`publish_market_snapshot.py` 和同一隐藏 stdin 凭据方式；403 封锁时保留本机，不反复重试。原 `app.db` 与旧个股快照保留，不被新模型覆盖。日常自动化仍先遵守交易日及收盘门禁。

## 盘后本机归档与离线包

上游完成采集、覆盖审查并切换本机快照后，从项目根目录运行盘后收尾命令。15:30 首轮和晚间补披露均使用同一入口。

```powershell
.\mini-programs\双端更新.cmd after-close
```

[双端更新脚本](../../mini-programs/双端更新.cmd) 会进入 `mini-programs` 并调用 [release.py](../../mini-programs/scripts/release.py)。`after-close` 读取 [local-view.json](../../web-trial/.local/local-view.json) 的 `market_target`，复用 [scheduled_gate](../scripts/collect_stock_prices.py) 核对北京时间和 `index_history.db` 的开市日历。周末、休市、日历未核验、未到 15:30 或市场快照尚未切到今天，都会返回 `status: skipped` 和原因，本次收尾到此结束。门禁在创建归档日志之前执行，此时应查看调用方保存的输出。

通过门禁后，脚本按本机已选历史、宽基和市场快照导出离线数据，核对三库的版本引用、目标日及数据块摘要，再准备微信与小红书两份 ZIP。新交付通过回归、离线数据对账、宿主编译与 ZIP 校验后才切换本机候选。晚间同一目标日有新披露时可生成另一个交付 ID。已核验的相同归档可直接复用。全过程仅归档到本机，平台发布进度另见[更新手册](../../mini-programs/UPDATES.md)。

| 要核对的问题 | 读哪个日期或状态 |
| --- | --- |
| 本机服务读到了哪个目标日？ | 查看 `local-view.json` 的 `history_target`、`broad_target`、`market_target` 及对应 `*_version`。旧模型的 `stock_date` 单独判读。 |
| 所查标的实际有值到哪天？ | 宽基看所选期间的最近有效资金日、期末可估算成员数。历史份额和净值分别看各自有值日。离线快照清单的 `summary.quotations` 还列出每只预置股票的来源查询截止日 `source_checked_through`、实际价格末日 `price_available_end` 和待更新标记 `price_pending`。 |
| 本轮选中了哪个离线包？ | 查看桌面 `ETF更新日志/YYYY-MM-DD/etf-heartbeat-*-mini-*-summary.json` 的 `status`、`id` 和 `dataDate`，再对照 [latest-snapshot.json](../../mini-programs/release/latest-snapshot.json) 指向的 `release/versions/<id>/manifest.json`。清单的 `dataDate` 为包内数据截止日，`preparedAt` 为制包时间。指针的 `selectedAt` 为入口切换时间。 |
| 本轮沿用旧包还是失败？ | `selected` 表示切换候选，`unchanged` 表示当前归档仍适用，`held_after_restore` 表示保留人工回退选择，`skipped` 表示门禁跳过。归档阶段失败查看同一批次的 `failure.json` 和对应步骤日志，再核对最新入口。 |

**2026-11-11 11:35（北京时间）的实例。** 当天手动准备记录为 `unchanged`，`reusedVerifiedArchives: true`，复用了交付 ID `1.1.0-snapshot-20261110-ee58399650fa7583`。当时三库目标日和离线包 `dataDate` 均为 **2026-11-10**。查询“全部已核验宽基”时，11 月 10 日应有的 266 只 ETF 均缺少当日估算值，最近有值日为 **11 月 9 日**。

双击[打开最新离线包](../../mini-programs/release/打开最新离线包.cmd)，脚本会按当前指针核对两个 ZIP 的 SHA-256，再打开所在目录。使用其中的 `ETF-WeChat.zip` 或 `ETF-Xiaohongshu.zip`。只想核对已选包的日期和摘要时，从项目根目录运行以下命令即可。

```powershell
.\mini-programs\release\打开最新离线包.cmd -VerifyOnly
```

## 当前上市股票的独立价格历史

操作与来源契约见 [STOCK_PRICE_SOURCES.md](STOCK_PRICE_SOURCES.md)。当前不可变清单只纳入 2026-11-10 当天官方成功核验的沪市 2,319 只、深市 2,906 只股票；北交所当前目录因访问拒绝尚未核验，保留 `complete=false`，不得用旧目录或历史持仓代码替代当前上市证据。新目标日更新清单须有当日官方成功证据；同一历史任务续传复用原清单和目标日。

从项目根目录，使用安装 Scrapling 的 Python 运行 `server/scripts/collect_eastmoney_stock_prices.py --universe-file 清单路径 --max-jobs 6000 --workers 2 --interval .6 --report 当天桌面唯一报告路径`。先加 `--dry-run` 做零请求规划；正式运行去掉该参数。中断后使用同一命令和新的报告名续传，成功且新鲜的记录直接复用，不启动重复进程。401/403/429 停批并退避两小时，5xx 停批并退避十五分钟；不删除断点绕过冷却。

东方财富按股票保存到 `web-trial/.local/price-cache/prices/eastmoney/v1/`，其失败记录和批次断点与腾讯分开。腾讯原始 `prices/v1/` 成功缓存不覆盖；网页按整股选择合格单一来源，不混接两源曲线。原始 OHLCV 中成交量、成交额单位保留未核验状态，不标为股、手或元；已核验的不复权收盘价仍为元/股。

部分早期来源日期与现有共用日历不一致。原始记录保留，当前 API 暂不绘制这些日期，并通过所选区间的 `source_calendar_excluded_count` 披露差异，状态保留 `partial`。不能把该差异推断为休市，也不能把“已查询完成”称为历史完全覆盖。运行日志、失败原因、覆盖和审查报告继续保存在桌面 `ETF更新日志/北京时间日期`，缓存和内部断点留在项目中。
