# 指数与场外基金公开数据

此模块使用独立 `server/data/index_history.db`，不写原个股库或 ETF 历史库。

## 来源和时间口径

- 指数日线：现有 AkShare 的新浪公开指数日线；中证 A50 和中证 2000 使用东方财富公开指数日线。中证1000改取东方财富更长的2005年起回溯序列，2932个重叠日期的收盘价逐项核对，最大差0.01指数点。逐行保留来源，索引表保留原始查询页面。库中 volume 为各源原始字段，单位未统一，不对外展示或纳入资金计算。
- 交易日历：AkShare 新浪历史交易日历；日线必须属于该源日历。原始库不按普通周一到周五猜测交易日，也不删真实历史周末记录。日频图由显示层按用户设置隐藏休市日期。
- 最早的上证综指观测为 1990-12-19，深证成指为 1991-04-03。该日期是本源可用起点，不是每个指数的成立日期。较新指数的早期回溯价格不代表当时已经发布该指数，更不代表当时已经存在 ETF。
- 场内 ETF 归属：读取原历史库的全部基金代码，逐页核对基金代码与“跟踪标的”字段，仅精确匹配目标指数及已核验别名；不会因为简称包含“300”便把策略指数归入沪深 300。其他指数、缺字段、身份不符、HTTP 失败各自记录。
- 场外候选：公开基金目录中名称涉及目标指数的指数基金/联接基金，排除已收录场内 ETF，再逐页核验“跟踪标的”。名称只用于发现候选，不作为最终归属证据，因此不能以候选搜索宣称已穷尽全部场外基金。
- 场外份额报告：基金规模页的结构化公开披露，读取原始 `QJSG`、`QJSH`、`QMZFE`，单位为份。页面表格显示亿份，不能把显示数直接当作原始份数。未将净值涨跌或份额变化换算成现金流。
- 每个 A/C 等份额类保留单独代码、份额类标记和源 `MERGECODE`，不把类内报告与母基金汇总重复相加。场外联接基金与底层 ETF 不能直接合计，季度份额也不能并入日频人民币净申赎。
- 场外报告期末可能是休市日，属于合法报告期间标签，不按日频图的交易日过滤掉。缺失季度不能用相邻报告日期推断期间起点，首期/非连续期间起点留空。
- 所有分组是当前已核验跟踪标的，历史报告不构成历史指数成分或历史持仓重建。

## 运行

普通交易日收盘后，使用原项目 Python（在 `server/`）：

```powershell
python scripts/collect_index_history.py --dataset indices --end YYYY-MM-DD --report 覆盖报告完整路径
```

ETF 目录有新增或需刷新对应关系时，使用已安装 Scrapling 的 Python：

```powershell
C:\Users\11200\.local\share\scrapling\.venv\Scripts\python.exe scripts/collect_index_history.py --dataset mappings --report 覆盖报告完整路径
```

该操作按已成功核验代码断点跳过，`--refresh-mappings` 才重新核验所有旧关系。不要每日重新请求全部基金资料页。

场外目录刷新使用原项目 Python，然后用 Scrapling Python 续采：

```powershell
python scripts/collect_index_history.py --dataset off-catalog
C:\Users\11200\.local\share\scrapling\.venv\Scripts\python.exe scripts/collect_off_exchange.py --report 场外覆盖报告完整路径
```

默认对所有匹配份额类采集源给出的完整报告历史。已成功报告及已核验其他指数自动跳过；`--refresh-reports` 用于新季度披露后刷新已覆盖的匹配基金，不能每日全量重复跑。未披露及失败保留状态，不填零；已核实访问阻塞不使用代理、换 IP 或安全绕过。

所有运行日志和报告写桌面 `ETF更新日志/YYYY-MM-DD`。后台更新仍遵守用户要求：周末、休市日不采集、不回填；新指数或场外资料补充不改变此排期。

## 表

`indexes`、`index_daily`、`calendar`、`fund_index` 提供指数/场内映射；`mapping_checks` 与 `jobs` 提供覆盖和失败记录。`off_exchange_members`、`off_exchange_reports` 保存场外成员和各期披露；`off_exchange_checks` 保存候选核验进度。Web 导出应保留分片和分页，不能把最新几季当作全历史报告，也不能把只覆盖目标指数的基金数量当作全市场 ETF 数量。
