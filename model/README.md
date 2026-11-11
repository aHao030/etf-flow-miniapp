# model/ — V1 测算模型与对账（quant-analyst，task-2）

主公式：`Flow(i,t) = Σ_e [ Δ份额(e,t) × NAV(e,t-1) × w(e,i,t) ]`

| 文件 | 说明 |
|---|---|
| `v1_flow.py` | 核心库：覆盖池、权重回退链（宽基=csindex 指数权重；行业/主题=季报前100，Q3 不足回退 Q2）、11 类异常处理、Flow 计算、派生指标（单日/5日/20日/强度/TOP5）、质量标记 |
| `compute_flow.py` | 测算驱动（无网络）：读 `data/samples/` + `cache/csindex_weights/` → `outputs/` |
| `reconcile.py` | 四层对账驱动（低频网络）：①内部一致性 ②gmbd vs 上交所份额（含拆分检测与还原）③金额量级 ④指数权重 vs 季报 ⑤窗口量级/守恒 |
| `tools/check_index_weights.py` | A6 补验：csindex 指数权重免费可得性探测 |
| `etf_universe.csv` | 覆盖池配置（代码/分类/指数代码/权重策略/上市日/状态） |
| `cache/csindex_weights/` | 6 个宽基指数全成分权重（2026-09-30） |
| `outputs/` | 测算产物（flow_daily/flow_derived/top_contributors/flow_by_etf/quality_marks/weights_used/weight_decisions + summary.txt）与 `reconciliation/` 对账产物 |

## 运行

```bash
# 环境：工作区自带 Python（含 pandas/numpy/lxml），无需 token
python compute_flow.py     # 测算：outputs/
python reconcile.py        # 对账：outputs/reconciliation/（网络，约 2 分钟）
python tools/check_index_weights.py  # A6 探测（可指定指数代码）
```

## 关键口径

- 金额单位：元；强度单位：bp；份额单位：份（上交所万份 ×1e4）。
- 累计窗口最小有效天数：5日=3、20日=12（A12 已拍板）。
- 量级对账门槛：±25%（A11 已拍板，实测最优 <0.02%）。
- 深市 ETF：快照起步（无历史份额），日频 Flow 待 M2 落库后启用。
- 详细口径见 `docs/m1-模型说明-v1.md`；对账结论见 `docs/m1-验证报告.md`。

## 覆盖扩展及交易日保护

`python model/universe_review.py 输入.csv 新建候选.csv` 依据代码、名称和可选类型生成排除或待审核状态；名称不能证明境内股票敞口，所有未排除项仍需人工审核，脚本不修改现有基金池。

`python model/import_verified_shares.py 输入.csv 新建份额.csv` 校验 `stat_date,market,fund_code,fund_name,shares,source,verified_by`，生成模型兼容字段并保留来源和审核人。输入必须逐条核实披露日期；脚本不自动合并、不改数据库、不激活基金。深市快照抓取日推断的有效日不构成已验证历史。

模型优先读取输入目录的 `trading_calendar.csv`（列名 `trade_date`，完整、经审核的交易日期）；缺失时使用全池份额及净值的观测日期作保守替代。观测日期无法发现全池同时缺失的交易日，不能保证连续性；正式使用须提供覆盖完整历史区间的交易日历。跨已知缺口的份额变化不计为单日流量，净值必须取份额前一观察日。节假日无需按工作日猜测。权重仍使用当前选择的权重回溯，不能视为历史时点权重。深市基金仍为 snapshot_only，未自动加入测算。
