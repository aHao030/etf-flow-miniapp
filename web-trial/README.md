# ETF 资金观察网页

## 从源码预览 Astryx 界面

使用 Node.js 22.15 或更高版本，在本目录运行下列命令。

```powershell
npm ci
npm run build
npm run preview
```

打开 [本机源码预览](http://127.0.0.1:4175/)。新检出的仓库可以操作页面和查看数据尚未发布的状态。完整资金记录由下文的已验证数据库和快照提供。已安装本机完整服务的用户继续使用桌面入口。

`ui/dashboard.mjs` 使用 Astryx Neutral 的 AppShell、TopNav、Layout、Section、Heading、Button 和 Stack 构建页面。React 在构建时生成 HTML。浏览器从同一来源加载样式和行情交互模块，中文使用设备系统字体。程序和界面依赖由 `package-lock.json` 锁定。

本项目默认打开宽基指数观察，第二个入口为个股观察。ETF 历史详情从指数所属 ETF 下钻进入，不再保留独立目录入口。黑白界面中红色表示资金净流入、绿色表示净流出，保留正负号、具体日期和金额读数。

## 三类数据与用途

| 视图 | 数据与接口 | 口径 |
| --- | --- | --- |
| ETF 历史 | market_history.db；/api/v1/history/meta、/etfs、/etf/{code}、/etf/{code}/chart | 沪深 ETF 目录、真实源日期的份额和单位净值、通过条件检查后的估算净申赎。目录保留历史出现产品，不推断未知退市日期。 |
| 宽基指数 | index_history.db；/api/v1/broad/meta、/indexes、/calendar、/index/{code或all}、/off/{code或all} | 先选指数，再选其已核验跟踪标的的 ETF；展示成员、分组合计及全组合计，叠加指数走势。场外指数基金/ETF联接基金按披露期分页展示申购和赎回份额。 |
| 全目录披露持仓 | stock_holdings.db 与同版本 ETF 历史因素；/api/v1/market/meta、/stocks、/stock/{code}、/etfs、/etf/{code}、/rank | 遍历整个已收录 ETF 目录核验披露持仓；按真实 A 股代码、来源基金、报告期及原始非归一化权重计算已披露贡献小计。未披露部分和未知关联关系保持未知。 |

当前网页个股视图使用 market 接口，不再使用固定少量 ETF 作为全市场覆盖。原 app.db、/api/v1 下的兼容个股接口和旧 stock 快照保留，不能用它们的覆盖数代替新版全目录口径。ETF 目录中包括债券、货币、跨境等类型；没有取得其 A 股持仓不代表采集失败，也不代表其实际持仓为零，须看每只基金状态。

宽基范围以 /api/v1/broad/meta 返回的可选指数和映射检查记录为准，目前支持 16 个常用宽基/市场基准。成员通过基金资料页“跟踪标的”逐一确认，不根据名字中的“300”“500”直接归组。全组合计按基金代码去重，但仅覆盖已核验映射，不能称为全市场所有指数基金，也不能识别“国家队”交易。

宽基时间筛选同时作用于指数日序列、期间合计、每只 ETF 的期间小计与逐日明细。`members.period_*` 来自与宽基相同的历史快照版本；`end_date_flow` 仅取所选范围最后交易日，保留的旧 `estimated_flow/flow_date` 仍是快照目标日，不能用作期间金额。上市边界未知时 `period_expected_days/period_complete_flow` 保持空值；缺失分片不退回最新金额。逐日表倒序、每页50条，展示资金小计、完整合计、有效与应有成员数、未知存续数、指数收盘点数。后端串行逐片处理历史并仅缓存最多12项/2MiB的小计结果，避免切换周期反复读取全部历史。

## 单位、合计和历史边界

- 资金默认自动单位：绝对值达到1亿元用“亿元”，其余用“万元”；一张图根据选中范围统一单位，坐标与读数一致。顶部可固定万元/亿元，偏好保存在本浏览器。切换只重绘已有数据，不触发行情采集。份额、单位净值和股价的单位不随资金单位改变。
- 个股图叠加黑色不复权收盘价，右轴为元/股，与资金共享真实交易日坐标；每日表可同时查看两项。股价独立请求 `/api/v1/prices/stock/{code}?start=YYYY-MM-DD&end=YYYY-MM-DD`，价格缺失不影响资金图。只有核验目录内股票可请求，日期截止当前已发布市场目标日。来源、实际覆盖起止和缺口在界面说明，不复权跳变不能当复权收益率。
- 股价采用固定腾讯日K端点、按5自然年分窗，服务端验证代码、复权类型、OHLC、成交量、日期顺序、上市日期、正式日历和大小上限；停牌/零成交量/休市/上市前记录不冒充有效收盘。按需缓存供全股票目录使用，源拒绝访问或限流后退避，不更换身份绕过。旧 app.db 的混合复权行情不用于此图。

- ETF 份额为“份”，单位净值为“元”，估算净申赎为“元”：相邻交易日份额差 × 前一交易日单位净值。前日及当日净值身份、事件、上市边界和异常变动均须通过检查。货币 ETF 的收益结转不能直接当申赎，每百份/万份收益不能当单位净值。
- 分组的已有净额是有效记录小计。缺数据、未知存续期、上市前或事件期间均不能补零；不能证明完整时，完整合计为空。全组合计叠加的上证综指只作为市场参考，不是该组合净值。
- 个股资金是已披露持仓贡献小计，不是股票成交资金或全部 ETF 实际净流入。当前披露权重回溯历史可能含前视信息，不是历史时点持仓重建，不能作为真实历史策略回测。股票上市边界由交易所批量目录的明确上市日期核验；未取得上市日的股票须显示边界未核验，不能把未知边界推断为自早期起存续。
- 场外报告的申购/赎回为“份”，不是人民币金额；净申购份数为两者之差。A/C 等份额类代码、源合并代码、报告期间分别保留。场外联接与底层 ETF 存在重复经济敞口，季度份额和场内日频资金不直接合计。
- 日频图使用核验后的交易日日历，周末和休市日不占横轴位置；缺失的交易日仍然留空。原始库不因显示过滤而删除来源记录。场外季报期末可能落在休市日，这是报告期间标签，仍保留。
- 指数历史最早观测可到 1990-12-19，不等于每个指数均自当年成立；新指数的早期回溯序列必须与正式发布日期区别。ETF 查询目标起点为 2004-12-30，实际可用份额、净值和资金估算分别列起止日期；目标范围不表示全部历史已取得。
- 2026-11-10 的来源审查中，ETF 份额最早可核验至 2012-01-04、部分净值可至 2004-12-30；指数、场外报告与 ETF 各自有不同起点。后续以发布快照 meta 和逐基金覆盖信息为准，不能以晚近值回填早期缺口。
- 本轮沪市2012—2020年2,187个交易日任务已全部通过；2004—2011年及深市更早份额抽样尚无可用源，未伪造数据。早期净值391只基金作业中380项沿用已成功结果、7项新增成功、3项pending、1项失败。任务成功不等于每只基金全部日期无缺口。
- 个股支持 1d、5d、20d、3m、6m、1y、2y、3y、5y 和全部可用历史。长周期按自然月/年锚点和交易日历计算，不把一年固定为250天。完整周期缺失时仅显示已有记录小计。

history 图接口返回完整选中范围，不随逐日表分页截断。当前历史/market 因素每基金最多 8,000 个日期，broad 最多 12,000 个交易日；超限应停止并调整协议，不能静默截断。新三类快照各自最多500文件，单文件不超过2 MiB；历史导出按约1.8 MB分片。场外完整报告使用独立分片和服务端分页，不限于最近几个季度。

## 本机与托管网页

本机入口为桌面“打开ETF资金观察.cmd”，网址 http://127.0.0.1:4173/。服务只监听回环地址，读取 web-trial/.local/local-view.json 中经过摘要校验的版本。源码重新构建后需重启本机专属服务；换数据快照不必重启。

个股股价独立保存在 `.local/price-cache/prices/v1/{股票代码}/{5年窗起始年}.json`。日更在已验证 market 快照切换后，用 Scrapling Python 在 server 目录运行 `scripts/collect_stock_prices.py --refresh-cached --report "当天桌面目录/股价刷新.json"`；只维护已查询缓存与待重试股票。脚本内置正式日历、15:30及当天 market 目标日检查，休市和盘前零请求结束。首次明确预热可用 `--codes 300750 600519`，默认截至已发布数据日。来源与测试见 [股价说明](../server/history/STOCK_PRICE_SOURCES.md)。

托管网址：https://etf-stock-flow-observer-1031.sweetowlet132.chatgpt.site/ 。它仍仅本人可见。本机与远程发布独立；Cloudflare HTML 403 封锁属于远程访问阻塞，代码部署成功不能当作数据上传成功。遇相同封锁保留待发布快照，不反复重试，不换代理/IP/域名规避，不重置密钥或放宽权限。

## 交易日收盘门禁

自动化在北京时间交易日15:30至22:30每小时核对最新可得披露，23:15进行夜间主补抓，先判断真实开市日。新增有效数据或校验状态变化后同步网页与双端离线候选，内容未变复用已有版本。23:15参考上交所基金市场聚合总规模的23:00更新时间并留15分钟缓冲；逐基金份额和净值仍以实际披露记录核验。官方依据和适用范围见 `server/history/INDEX_SOURCES.md` 的披露时点核验。页面实际数据日期来自已验证的源覆盖，与检查时间和查询目标日分别保留。当前来源没有可核验的逐基金公布时分秒，只展示数据所属日期。

1. 周六、周日或交易日历确认的休市日直接结束，不采集、不历史回填、不运行模型、不构建导出、不发布。
2. 优先使用已缓存且覆盖当天的交易日历；index_history.db 的 calendar 保存来源完整日历，market_history.db 的 calendar 可能仅覆盖曾请求的区间。当天超过日历范围时只能先核验日历，不可因“今天不在表中”断言休市，也不可只按周一至周五判断开市。
3. 交易日15:00前不采集盘后数据。15:30源未披露记为pending，晚间再补，不冒充今天或按故障清空旧版。
4. 人工明确要求补历史时按当次指令执行；例行休市自动化不做无效补抓。

## 更新流程

以下命令从项目 server 目录运行，YYYY-MM-DD 为本次目标交易日，WINDOW_START 为来源交易日历中截至目标日的最近五个交易日首日。路径含空格时用引号。

项目 Python：C:\Users\11200\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe

Scrapling Python：C:\Users\11200\.local\share\scrapling\.venv\Scripts\python.exe

先按上述门禁判断开市。开始有效运行前创建桌面 ETF更新日志/YYYY-MM-DD，采集、重试、覆盖、导出、审查、线上核验和汇总均保存其中；自动文件名沿用 etf-heartbeat-YYYYMMDD-HHMM-*，重复运行加秒或唯一后缀。

### 采集与断点

使用项目 Python：

~~~powershell
python -m history.backfill --dataset all --start WINDOW_START --end YYYY-MM-DD --max-jobs 3000
python -m history.coverage --start 2004-12-30 --end YYYY-MM-DD
python scripts/collect_index_history.py --dataset indices --end YYYY-MM-DD --report "当天桌面目录/指数覆盖.json"
python scripts/collect_market_holdings.py --asof YYYY-MM-DD --workers 2 --interval 0.5
~~~

持仓采集默认复用7天内已核验结果，保留原始披露权重，不强制归一化为100%；无需每次使用 --refresh。首次扩大历史范围需固定日期区间断点补齐，已成功的长区间份额不重复采集；较短日更窗口重新取最近日期以吸收晚间修订。覆盖报告的成功状态不证明全基金全日期完整。

新增股票或定期核验上市边界时运行 `python scripts/collect_stock_listings.py --report "当天桌面目录/股票上市日期.json"`，从上交所主板、科创板及深交所A股目录共3个批量请求取得源上市日期，只写 `stock_holdings.db.stock_listings`。2026-11-10已核验5,225个股票日期；北交所接口返回403，未绕过，目录未收录或无法访问的股票仍为空。无需逐股请求或每天重复抓取全目录。

历史回填新发现 ETF 后，用 Scrapling Python 只补新目录关系，不重刷已知来源失败：

~~~powershell
C:\Users\11200\.local\share\scrapling\.venv\Scripts\python.exe scripts/collect_index_history.py --dataset mappings --new-only --report "当天桌面目录/新增指数映射.json"
~~~

场外候选和报告不需要日内重复抓全量。新增基金或季度披露更新时使用：

~~~powershell
python scripts/collect_index_history.py --dataset off-catalog
C:\Users\11200\.local\share\scrapling\.venv\Scripts\python.exe scripts/collect_off_exchange.py --report "当天桌面目录/场外覆盖.json"
~~~

默认跳过已取得的报告；新季度确需刷新旧成员时添加 --refresh-reports。不要设置代表样本或每指数数量上限。来源身份不符、未披露和访问拒绝分别记录，不靠换网络绕过。具体来源与份额类边界见 ../server/history/INDEX_SOURCES.md。

### 导出、核验与原子切换

先导出历史快照，记下返回的不可变绝对目录 H：

~~~powershell
python scripts/export_history_snapshot.py ../web-trial/.local/history-snapshots
node ../web-trial/tests/history-snapshot-check.mjs H
python scripts/export_broad_snapshot.py ../web-trial/.local/broad-snapshots --history-snapshot H
python scripts/export_market_snapshot.py ../web-trial/.local/market-snapshots --history-snapshot H
node ../web-trial/tests/broad-snapshot-check.mjs B
node ../web-trial/tests/market-snapshot-check.mjs M
~~~

B/M 分别为导出器返回的宽基、全目录快照绝对路径。必须引用同一 H，不能把不同版本的 ETF 因素与分组合计混用。通过检查后，用各目录名中的版本号切换：

~~~powershell
python scripts/set_local_view.py --stock 保留的旧个股已验证版本 --history H版本 --broad B版本 --market M版本
~~~

原兼容个股流程仍可运行 pipeline.py run --date YYYY-MM-DD；只有原模型 computed_date 达到目标且核验通过才运行 scripts/build_stock_history_snapshot.py --expected-date YYYY-MM-DD。它构建隔离的扩展历史并返回 verified_snapshot，不修改原 app.db；失败保留旧 stock 版本，不阻止已独立核验的 history/broad/market 更新，也不能再用短历史导出覆盖旧版扩展历史。

若本机服务正在运行，读取 /local/status 及 /api/v1/history/meta、/api/v1/broad/meta、/api/v1/market/meta，确认版本、真实日期、覆盖和共同 history_version；未启动时例行自动化不打开浏览器或新建服务。

### 远程发布

各类数据分别使用 scripts/publish_history_snapshot.py H、scripts/publish_broad_snapshot.py B、scripts/publish_market_snapshot.py M；旧 stock 版本使用 scripts/publish_web_snapshot.py。使用 Sites get_site 取得既有服务凭据，通过隐藏 stdin JSON 的 service_token 字段传给发布脚本，不能写进参数、文件、日志或自动化提示。

快照先逐片检查摘要、结构和跨片引用，最后原子提交对应 latest。发布后读取对应线上 meta 确认版本；失败保留线上旧版。本机 DPAPI 更新密钥和站点 UPDATE_SECRET 沿用现值，不重新初始化。403访问封锁时停止该轮远程发布并保留本机已验证版本。

## 构建与验证

从 web-trial 运行 node scripts/build.mjs 将同源前端资源嵌入 Worker；npm test 运行当前接口与交互检查。正式大快照还需运行前述三类 snapshot-check。单元测试不能代替实际数据覆盖审查；桌面/手机查看精确读数、交易日横轴、指数叠加、份额类和分页。

访问保持 Sites 仅本人权限；更新另需既有更新密钥。没有公开站点、暴露本地服务到外网、第三方 CDN 或上传数据库原件的步骤。未进行公网渗透测试或长期负载测试。
