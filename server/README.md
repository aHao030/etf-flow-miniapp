# server/ — M2 后端服务（backend-dev，task-3）

技术路线（用户已决策）：**路线 B 自建**——FastAPI + SQLite（MVP）+ 进程内缓存 + APScheduler，本地电脑部署（体验版期）。
API 契约：`../docs/m2-api-契约.md` v1.0（已冻结，实现唯一依据，必返 trade_date、金额单位「元」）。

## 目录

| 文件 | 说明 |
|---|---|
| `config.py` | 路径/上游接口/时序/口径披露文案（终稿同 docs/m1-模型说明-v1.md §7） |
| `db.py` | SQLite 存储层（唯一写 SQL 处；换 MySQL 只改本层）。WAL + 每操作独立连接 |
| `cache.py` | 进程内 TTL 缓存（接口与 Redis 语义对齐；换 Redis 只改本类） |
| `fetchers.py` | 上游采集（SSE/SZSE/lsjz/jjcc/腾讯），带重试退避；**主链路不含东财 push2** |
| `industry.py` | 申万一级行业静态映射构建/加载（akshare，见下） |
| `compute.py` | 计算服务：**复用 model/v1_flow.py + compute_flow.py**（仅重定向输入输出目录，口径零复制）+ 入库 + 榜单预计算 + latest_date 推进 |
| `pipeline.py` | 盘后管线编排（幂等，trade_date 可重跑）+ APScheduler 任务函数 + CLI |
| `app.py` | FastAPI 8 接口（meta/health/rank/stock/etf/industries/etfs + watchlist 冻结桩） |
| `main.py` | 入口：调度器 + uvicorn |
| `scripts/smoke_test.py` | 契约逐接口自测 |
| `data/` | app.db（SQLite）、inputs/（模型输入，与 data/samples 同构）、industry_sw_l1.csv |
| `work/` | outputs/（模型输出重定向）、logs/ |

## 运行

```bash
python main.py --no-scheduler   # 仅 HTTP（自测），127.0.0.1:8000
python main.py                  # 调度器 + HTTP（生产行为）
python pipeline.py run --date YYYY-MM-DD   # 手动跑当日/指定日管线（幂等）
python pipeline.py finalize     # 补完成：份额回填→全量建榜→latest_date→K线→缓存失效
python pipeline.py szse-pull    # 手动深市快照落库
python pipeline.py industry-build  # 重建行业映射（一般无需重跑）
python scripts/smoke_test.py    # 自测（需服务已启动），42 项契约断言
python scripts/db_inspect.py    # 数据库状态速览（调试）
```

定时任务（交易日，Asia/Shanghai）：19:30 主管线 / 22:30 兜底重试（SSE 晚间 19:00–20:00 披露）/ 8:10 T+1 补算；深市份额 20:30 落库 / 8:00 核对补拉（契约 §4-1）。

## 关键设计说明

1. **口径零复制**：计算 100% 复用 `model/v1_flow.py`（`compute.py run_model()` 仅把
   `V.DATA_DIR → server/data/inputs`、`V.OUT_DIR → server/work/outputs`，再调用
   `compute_flow.main()` 原路径）。模型输入 CSV 由 ETL 按 samples 同构生成。
2. **幂等**：全部步骤以 trade_date 为键 INSERT OR REPLACE / 追加去重；`computed_date` 已到目标日则整体跳过。任务失败保留旧数据（latest_date 不动），接口继续服务昨日数据。
3. **latest_date 原子切换**：全链路成功后一次推进 meta 指针并整批失效进程内缓存（契约 §4）。
4. **历史流通市值近似法（A7/验证报告 §7-5 移交）**：个股 60 日序列的强度
   `= flow ÷ [当日流通市值 × 收盘价(t)/收盘价(当日)]`；当前日比值=1，与模型输出一致。
5. **质量标记**：个股 quality 由其当日贡献 ETF 的 `quality_marks` 并集 + 权重过期（>6 个月 → `weight_stale`）合成；data_status（ok/sse_only/accumulating/no_data）依据权重覆盖基金的 status 判定。
6. **行业映射**：申万一级，来源 `akshare`：`sw_index_first_info`（申万宏源研究官网 31 个一级行业）+ `index_component_sw`（申万指数成分，乐咕乐股聚合），静态 CSV 带 version；覆盖池外/未分类 → 「未分类」。口径与来源已标注，供「关于」页引用。
7. **深市 ETF**：快照起步（status=snapshot_only），份额每日 20:30+8:00 落库积累；日频测算待 universe 激活（quant-analyst 决策），届时 ETL 已备好份额历史。
8. **自选/提醒**：M2 冻结（裁决 ④），watchlist 路由返回 50302 提示前端用本地缓存；契约 §2.8 保留。
9. **缓存/存储适配层**：MVP 进程内缓存 + SQLite；Redis/MySQL 替换点见 db.py/cache.py 头部注释，契约 §4 的 Redis key 为行为约定。
10. **K线双源**：腾讯 fqkline 为主源，被 IP 软封（501）时自动切新浪 getKLineData 备源；增量拉取（已有当日收盘价跳过）+ 连续 40 次失败熔断，次日任务自动续补。序列强度在收盘价缺失时回退模型口径（当日快照分母）。
11. **深市份额指针**：`etl_szse_shares` 落库成功即同步推进 meta 的 `szse_share_date`（D4 修复），health.pipeline 与 meta 指针同源，所有调用路径（主管线/20:30/8:00/CLI）一致。

## 已知豁免（QA 裁决 D2/D3，开发期不实现，公开运营前补齐）

| 项 | 现状 | 公开上线前需做 |
|---|---|---|
| 42901 请求限流 | 未实现（本地单机 + 体验版内测，无滥用面） | 按 openid/IP 限速（如 slowapi），契约 §3 条目保留 |
| 50001 故障注入/监控告警 | 未实现（异常仅日志 + task_log 记录） | 接入监控告警通道；契约 §3 条目保留 |

## 环境

Python 3.12（工作区运行时）：pandas/numpy/lxml/openpyxl/akshare 已带；
`pip install -r requirements.txt`（fastapi/uvicorn/apscheduler/requests）。

## 本机发布快照

Windows 本机发布使用 `server/scripts/publish_web_snapshot.py`；初次运行 `--init-secret` 生成更新密钥并仅输出一次，用于配置站点的 `UPDATE_SECRET`。密钥以当前 Windows 用户的 DPAPI 保存于 `web-trial/.local/update-secret.dpapi`，该目录已列入 Git 忽略；换 Windows 用户或机器不能解密，需重新配置密钥。

正常运行传入快照目录，脚本提示 `Ready for access JSON on stdin` 后，从标准输入读取单行 JSON（键 `service_token`，由站点访问工具取得）；该令牌仅在内存中使用。使用无终端回显的管道提供输入，勿保存令牌。脚本在上传前验证快照文件摘要、交易日和本机最新数据；逐个上传并核验后才提交，最后核对发布版本。已有同版本会核验后退出。失败时检查日志中的通用错误，再重新获取访问令牌重试。

本机文件锁只阻止本机协作进程并发发布，并不能阻止其他设备或其他发布客户端并发操作；站点服务仍需负责并发一致性和凭据权限控制。定时更新需要本机在线、能够获取短期访问令牌并启动采集与发布任务。
