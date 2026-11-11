# tests/ — M4 测试用例与脚本（qa-compliance）

任务板 task-5 的测试产物。全部脚本可直接复跑，退出码 0=通过、1=失败、2=环境不可用。

## 运行前置

1. 启动后端：`server/` 下 `python main.py --no-scheduler`（127.0.0.1:8000）；
   用工作区 Python：`C:\Users\11200\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`。
2. 执行（工作区根目录）：
   ```bash
   python tests/api_contract_test.py        # API 契约边界（91 项 + 空库 50301 联动实测）
   python tests/l1_source_reconcile.py      # L1 源对账：库内 vs 上交所/天天基金（需外网）
   python tests/l2_recompute.py             # L2 复算对账：模型原始 CSV 独立重算 vs API
   python tests/l3_ui_consistency.py        # L3 界面一致性：跨页面/恒等式
   python tests/static_compliance_scan.py   # 合规静态扫描：红线词/披露条/命名/文案终稿
   ```

## 覆盖与口径

| 脚本 | 覆盖 | 关键口径 |
|---|---|---|
| api_contract_test.py | 8 接口错误码（40001/40002/40003/40401-40404/42901/50001）、50301+health 联动（临时空库实例，8010 端口）、watchlist 50302 冻结桩、分页边界、trade_date 历史回看、空值语义（5日<3/20日<12 → null）、meta.disclosure 文案、weight_source/coverage 归一化枚举、422 信封偏离探测、NaN 边界回归（920982） | 契约 v1.1；PASS/FAIL/GAP 三态，GAP=契约缺口（未实现或不可自然触发） |
| l1_source_reconcile.py | 13 只沪市 ETF × 20 交易日份额 vs 上交所官网；净值 vs 天天基金 lsjz；深市落库状态 | 独立实现（stdlib），不 import server 代码；份额 abs≤0.011、净值 abs≤1e-4 |
| l2_recompute.py | 榜单 TOP100 全量复算 + 头/中/尾抽样 12 只：flow/intensity_bp/top_etfs TOP5/5-20 日窗口（最小有效天数 3/12） | Flow=Σ(contribution_yuan)；intensity=flow/(float_mv_yi×1e8)×1e4；容差 0.1%+0.011 |
| l3_ui_consistency.py | 榜单↔详情↔ETF 双向一致、Σ贡献=flow_1d（≤1 元）、est_flow=Σ贡献、share_change/nav_prev 与 quality_marks 一致、series 末点衔接、行业榜⊆全市场榜 | 金额单位「元」、强度 bp |
| static_compliance_scan.py | 红线词（买入/卖出/推荐/建议/抄底/加仓/上车/目标价/评级/收益/荐股/预测）、披露条四页常驻、「估算净流入」命名、config 文案 vs m1-模型说明 §7 终稿逐字一致、env 核查 | 「不构成」否定式豁免；ec-canvas 开发者日志豁免 |

## 结果档案（2026-10-30，latest_date=2026-10-30）

- api_contract：91 PASS / 3 FAIL（D1：422 裸 detail 未走契约信封）/ 2 GAP（D2：42901 未实现；D3：50001 无注入入口）
- L1：3 PASS（份额 260/260、净值 260/260）
- L2：68 PASS / 0 FAIL（max_err=0.0000%）
- L3：82 PASS / 0 FAIL
- 静态扫描：16 PASS / 0 FAIL / 2 INFO
- 明细与缺陷清单：`docs/m4-测试报告.md`；提审文案与检查清单：`docs/m5-提审材料包.md`

## 维护约定

- 契约或口径变更时先更新 `docs/m2-api-契约.md` / `docs/m1-模型说明-v1.md`，再同步本目录断言；
- `api_contract_test.py` 的空库实例用例会在 `%TEMP%` 复制 server/model 代码，跑完即删，不触碰工作区 server/ 文件；
- 上游接口不可达时 L1 以 SKIP 记录（环境性），不算数据缺陷；确认无法访问后再上报。
