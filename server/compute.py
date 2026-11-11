# -*- coding: utf-8 -*-
"""计算服务（M2）：复用 M1 模型产出，禁止另写口径实现。

口径 100% 复用 model/v1_flow.py 与 model/compute_flow.py：
- 通过 sys.path 引入模型模块，仅重定向其输入/输出目录
  （V.DATA_DIR → server/data/inputs，V.OUT_DIR → server/work/outputs），
  计算逻辑一行不改、不复制；
- 幂等：模型输出 CSV 以 INSERT OR REPLACE 入库。
之后在本模块完成：入库、榜单预计算（TOP100 × 行业 × 周期）、
数据状态判定（ok/sse_only/accumulating/no_data）、latest_date 指针推进。
"""
from __future__ import annotations

import os
import sys

import config
import db


def run_model() -> None:
    """调用模型驱动（compute_flow.main），输入/输出重定向到 server 目录。"""
    sys.dont_write_bytecode = True  # 避免向 model/ 写 __pycache__（写范围约束）
    sys.path.insert(0, config.MODEL_DIR)
    import v1_flow as V  # noqa: E402
    V.DATA_DIR = config.INPUTS_DIR
    V.OUT_DIR = config.OUTPUTS_DIR
    os.makedirs(config.OUTPUTS_DIR, exist_ok=True)
    import compute_flow as CF  # noqa: E402
    CF.main()  # 与 M1 完全相同的执行路径


def _read_csv(name: str):
    import pandas as pd
    path = os.path.join(config.OUTPUTS_DIR, name)
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _f(v, default=None):
    """CSV 空串 → None，否则 float。"""
    if v in ("", "nan", "None"):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=None):
    if v in ("", "nan", "None"):
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _fl(v) -> list[str]:
    """flags 字符串 → 列表（去空）。"""
    return [x for x in str(v).split("|") if x] if v else []


def ingest() -> str:
    """校验六份完整模型输出，在一个事务中替换镜像并返回最新日期。"""
    daily = _read_csv("flow_daily.csv")
    derived = _read_csv("flow_derived.csv")
    by_etf = _read_csv("flow_by_etf.csv")
    marks = _read_csv("quality_marks.csv")
    weights = _read_csv("weights_used.csv")
    decisions = _read_csv("weight_decisions.csv")

    snapshot = {}
    def collect(table, columns, rows):
        snapshot[table] = (columns, rows)

    collect("flow_daily",
                    ["trade_date", "stock_code", "stock_name", "flow_yuan", "n_etf"],
                    [(r["date"], r["stock_code"], r["stock_name"],
                      _f(r["flow_yuan"]), _i(r["n_etf"])) for _, r in daily.iterrows()])
    collect("flow_derived",
                    ["trade_date", "stock_code", "flow_1d_yuan", "flow_5d_yuan",
                     "valid_days_5", "flow_20d_yuan", "valid_days_20",
                     "float_mv_yi", "intensity_1d_bp", "intensity_5d_bp",
                     "intensity_20d_bp"],
                    [(r["date"], r["stock_code"],
                      _f(r["flow_1d_yuan"]), _f(r["flow_5d_yuan"]), _i(r["valid_days_5"]),
                      _f(r["flow_20d_yuan"]), _i(r["valid_days_20"]),
                      _f(r["float_mv_yi"]), _f(r["intensity_1d_bp"]),
                      _f(r["intensity_5d_bp"]), _f(r["intensity_20d_bp"]))
                     for _, r in derived.iterrows()])
    collect("flow_by_etf",
                    ["trade_date", "fund_code", "stock_code", "stock_name", "weight",
                     "delta_shares", "nav_used", "contribution_yuan", "weight_source",
                     "coverage_pct"],
                    [(r["date"], r["fund_code"], r["stock_code"], r["stock_name"],
                      _f(r["weight"]), _f(r["delta_shares"]), _f(r["nav_used"]),
                      _f(r["contribution_yuan"]), r["weight_source"],
                      _f(r["coverage_pct"])) for _, r in by_etf.iterrows()])
    collect("quality_marks",
                    ["trade_date", "fund_code", "delta_shares", "delta_pct",
                     "nav_used", "coverage_pct", "weight_source", "flags"],
                    [(r["date"], r["fund_code"], _f(r["delta_shares"]),
                      _f(r["delta_pct"]), _f(r["nav_used"]), _f(r["coverage_pct"]),
                      r["weight_source"], r["flags"]) for _, r in marks.iterrows()])
    collect("weights_used",
                    ["fund_code", "stock_code", "stock_name", "weight",
                     "weight_source", "weight_quarter", "coverage_pct"],
                    [(r["fund_code"], r["stock_code"], r["stock_name"],
                      _f(r["weight"]), r["weight_source"], r["weight_quarter"],
                      _f(r["coverage_pct"])) for _, r in weights.iterrows()])
    collect("weight_decisions",
                    ["fund_code", "policy", "weight_source", "weight_quarter",
                     "coverage_pct", "n_stocks", "usable_quarters", "used_index"],
                    [(r["fund_code"], r["policy"], r["weight_source"],
                      r["weight_quarter"], _f(r["coverage_pct"]), _i(r["n_stocks"]),
                      r["usable_quarters"], _i(r["used_index"]))
                     for _, r in decisions.iterrows()])
    db.replace_model_snapshot(snapshot)
    latest = daily["date"].max() if not daily.empty else None
    return latest


def update_stock_meta() -> None:
    """个股名称（模型口径）+ 申万行业映射落库。"""
    import industry as ind
    mapping = ind.load()
    stocks = db.query(
        "SELECT DISTINCT stock_code, stock_name FROM flow_daily")
    rows = []
    for s in stocks:
        m = mapping.get(s["stock_code"], {})
        rows.append((s["stock_code"], s["stock_name"],
                     m.get("industry_code") or "", m.get("industry_name") or "未分类"))
    db.replace_rows("stock_meta",
                    ["stock_code", "stock_name", "industry_code", "industry_name"],
                    rows)
    # 行业字典版本与覆盖池内股票数
    version = ind.version_of() or "unknown"
    counts = db.query(
        "SELECT industry_code, industry_name, COUNT(*) AS n FROM stock_meta "
        "WHERE industry_code != '' GROUP BY industry_code, industry_name")
    db.replace_rows("industry_dict",
                    ["industry_code", "industry_name", "stock_count",
                     "version", "updated_at"],
                    [(c["industry_code"], c["industry_name"], c["n"],
                      version, db._now()) for c in counts])


def _fmt_date(v) -> str:
    """上市日期 → YYYY-MM-DD（NaN/空/NaT → ''）。"""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    if hasattr(v, "strftime"):          # pd.Timestamp
        return v.strftime("%Y-%m-%d")
    s = str(v)
    if s in ("", "nan", "NaT", "None"):
        return ""
    return s[:10]


def update_etf_meta(universe) -> None:
    rows = [(r["fund_code"], r["fund_name"], "SH" if r["market"] == "SH" else "SZ",
             r["category"], r.get("index_code") or "", r["weight_policy"],
             _fmt_date(r["listed_date"]),
             r["status"], r.get("notes") or "")
            for _, r in universe.iterrows()]
    db.replace_rows("etf_meta",
                    ["fund_code", "fund_name", "market", "category", "index_code",
                     "weight_policy", "listed_date", "status", "notes"], rows)


def build_ranks(trade_dates: list[str] | None = None) -> None:
    """榜单预计算：每周期 × 全市场/各申万一级行业 × in（MVP 仅 in），TOP100。

    缺省对 flow_daily 全部历史日期构建（支持 trade_date 历史回看）；
    可传指定日期列表。幂等（INSERT OR REPLACE）。
    """
    if trade_dates is None:
        trade_dates = [r["trade_date"] for r in db.query(
            "SELECT DISTINCT trade_date FROM flow_daily ORDER BY trade_date")]
    for td in trade_dates:
        _build_ranks_one_day(td)


def _build_ranks_one_day(trade_date: str) -> None:
    periods = {"1d": ("flow_1d_yuan", "intensity_1d_bp", "NULL"),
               "5d": ("flow_5d_yuan", "intensity_5d_bp", "valid_days_5"),
               "20d": ("flow_20d_yuan", "intensity_20d_bp", "valid_days_20")}
    # 个股当日 flags（来自其贡献 ETF 的质量标记并集）
    flag_rows = db.query(
        "SELECT b.stock_code, GROUP_CONCAT(DISTINCT q.flags) AS flags "
        "FROM flow_by_etf b JOIN quality_marks q "
        "ON q.trade_date = b.trade_date AND q.fund_code = b.fund_code "
        "WHERE b.trade_date = ? AND q.flags != '' GROUP BY b.stock_code",
        (trade_date,))
    flags_map = {r["stock_code"]: sorted(set(_fl(r["flags"]))) for r in flag_rows}
    out: list[tuple] = []
    for period, (flow_col, inten_col, valid_col) in periods.items():
        valid_expr = valid_col if valid_col == "NULL" else f"d.{valid_col}"
        where = f"trade_date = ? AND {flow_col} IS NOT NULL"
        rows = db.query(
            f"SELECT d.stock_code, COALESCE(sm.stock_name,'') AS stock_name, "
            f"sm.industry_code, sm.industry_name, "
            f"d.{flow_col} AS flow, d.{inten_col} AS intensity, "
            f"{valid_expr} AS vd FROM flow_derived d "
            f"LEFT JOIN stock_meta sm ON sm.stock_code = d.stock_code "
            f"WHERE {where} ORDER BY d.{flow_col} DESC", (trade_date,))
        groups: dict[str, list] = {}
        for r in rows:
            # 每只股票同时进全市场组（''）与其行业组
            groups.setdefault("", []).append(r)
            groups.setdefault(r["industry_code"] or "", []).append(r)
        for ic, rs in groups.items():
            for rank, r in enumerate(rs[:config.RANK_TOP_N], start=1):
                if period == "1d":
                    vd_str = "1/1"
                else:
                    vd_str = f"{int(r['vd'] or 0)}/{5 if period == '5d' else 20}"
                out.append((trade_date, period, "in", ic, rank,
                            r["stock_code"], r["stock_name"], r["flow"],
                            r["intensity"],
                            vd_str, "|".join(flags_map.get(r["stock_code"], []))))
    db.replace_rows("rank_daily",
                    ["trade_date", "period", "direction", "industry_code", "rank",
                     "stock_code", "stock_name", "flow", "intensity_bp",
                     "valid_days", "flags"], out)


def stock_data_status(stock_code: str) -> str:
    """ok / sse_only / accumulating / no_data（依据权重覆盖基金的 status）。"""
    rows = db.query(
        "SELECT w.fund_code, e.status FROM weights_used w "
        "JOIN etf_meta e ON e.fund_code = w.fund_code WHERE w.stock_code = ?",
        (stock_code,))
    if not rows:
        return "no_data"
    active = any(r["status"] == "active" for r in rows)
    snap = any(r["status"] != "active" for r in rows)
    if active and snap:
        return "sse_only"
    if active:
        return "ok"
    return "accumulating"


def advance_meta(trade_date: str) -> None:
    """latest_date 原子推进 + 管线日期（契约 §4）。"""
    prev = db.query_one(
        "SELECT MAX(trade_date) AS d FROM flow_daily WHERE trade_date < ?",
        (trade_date,))
    db.meta_set("latest_date", trade_date)
    db.meta_set("prev_trade_date", prev["d"] if prev and prev["d"] else "")
    db.meta_set("computed_date", trade_date)
    db.meta_set("cached_date", trade_date)
    sse = db.query_one("SELECT MAX(trade_date) AS d FROM etf_share_daily WHERE market='SH'")
    szse = db.query_one("SELECT MAX(trade_date) AS d FROM etf_share_daily WHERE market='SZ'")
    if sse and sse["d"]:
        db.meta_set("sse_share_date", sse["d"])
    if szse and szse["d"]:
        db.meta_set("szse_share_date", szse["d"])


def latest_date() -> str | None:
    return db.meta_get("latest_date") or None

