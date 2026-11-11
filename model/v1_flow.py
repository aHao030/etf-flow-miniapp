# -*- coding: utf-8 -*-
"""V1 测算模型核心库（task-2, quant-analyst）

主公式（与《项目计划书》§2.2 及《m1-模型设计草案》v0.2 一致）：
    Flow(i,t) = Σ_e [ Δ份额(e,t) × NAV(e,t-1) × w(e,i,t) ]   e ∈ 覆盖池且持有 i

权重优先级与回退（Lead M1-A 定案 + A1-A12 敲定）：
    宽基 ETF   → 中证指数官网成分权重（csindex，覆盖率 100%，实测可得）
    行业/主题  → 季报持仓（天天基金 F10 前 100 大上限）：最新可用季度，
                 覆盖率 <95% 时回退最近完整季度（≥95%），均不足 → 权重缺失
    （PCF 每日申赎清单 V1 无免费批量渠道，列 V2）

异常处理（11 类，见 docs/m1-模型设计草案.md §4）：
    份额突变>30% 标记大额申赎；规模不连续→疑似折算（贡献置 0）；
    建仓期/清盘/净值缺失/权重缺失/数据缺口/停牌/现金替代/双重上市去重。

派生指标：单日 / 5日 / 20日累计、流入强度（净流入÷流通市值，bp）、
    ETF 贡献拆解 TOP5、质量标记（权重来源+覆盖率+有效天数）。
    累计窗口最小有效天数 3/12（A12 已拍板）；强度分母=当日流通市值快照（A7）。

输入：data/samples/*.csv + cache/csindex_weights/*.csv；输出：outputs/。
仅依赖 pandas/numpy，无网络。
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "..", "data", "samples")
CACHE_DIR = os.path.join(ROOT, "cache")
OUT_DIR = os.path.join(ROOT, "outputs")
IDX_DIR = os.path.join(CACHE_DIR, "csindex_weights")

STOCK_CODE_RE = re.compile(r"^\d{6}$")

COV_USABLE = 60.0      # A5 落位：季度覆盖率低于该值视为不可用
COV_FULL = 95.0        # 视为"完整"；不足时优先回退最近完整季度
DELTA_PCT_LIMIT = 0.30  # A10：大额申赎标记阈值（实测样本 max 17.3% 不误触发）
SPLIT_SIZE_TOL = 0.01   # 疑似折算：份额跳变但规模（份额×净值）相对变动 <1%
LISTING_DAYS_MIN = 60   # A9：上市未满 60 个交易日不纳入测算
MIN_VALID_DAYS_5 = 3    # A12（已拍板）
MIN_VALID_DAYS_20 = 12


def load_universe() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(ROOT, "etf_universe.csv"), dtype=str)
    df["listed_date"] = pd.to_datetime(df["listed_date"], errors="coerce")
    return df


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    shares = pd.read_csv(os.path.join(DATA_DIR, "etf_shares_sse_history.csv"), dtype=str)
    shares["stat_date"] = pd.to_datetime(shares["stat_date"])
    shares["shares"] = pd.to_numeric(shares["shares"], errors="coerce")
    nav = pd.read_csv(os.path.join(DATA_DIR, "etf_nav_history.csv"), dtype=str)
    nav["nav_date"] = pd.to_datetime(nav["nav_date"])
    nav["unit_nav"] = pd.to_numeric(nav["unit_nav"], errors="coerce")
    hold = pd.read_csv(os.path.join(DATA_DIR, "etf_holdings_all_quarters.csv"), dtype=str)
    floatcap = pd.read_csv(os.path.join(DATA_DIR, "stock_floatcap_snapshot.csv"), dtype=str)
    floatcap["float_mv_yi"] = pd.to_numeric(floatcap["float_mv_yi"], errors="coerce")
    return shares, nav, hold, floatcap


def _clean_holdings(hold: pd.DataFrame) -> pd.DataFrame:
    """清洗季报持仓：仅保留 6 位 A 股代码（剔除北交所 Axxxxx 等）、数值化、去重。"""
    h = hold.copy()
    h["stock_code"] = h["stock_code"].astype(str).str.strip()
    h = h[h["stock_code"].str.match(STOCK_CODE_RE)].copy()
    h["pct_nav"] = pd.to_numeric(h["pct_nav"].astype(str).str.replace("%", ""), errors="coerce")
    h["mktcap_wan"] = pd.to_numeric(h["mktcap_wan"].astype(str).str.replace(",", ""), errors="coerce")
    h = h.sort_values("pct_nav", ascending=False).drop_duplicates(["fund_code", "quarter", "stock_code"])
    return h


def report_quarter_stats(h: pd.DataFrame) -> pd.DataFrame:
    return (h.groupby(["fund_code", "quarter"], as_index=False)
             .agg(n=("stock_code", "size"), pct_sum=("pct_nav", "sum")))


def build_weights(universe: pd.DataFrame, hold: pd.DataFrame,
                  idx_dir: str = IDX_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    """逐 ETF 构建权重表（含来源与覆盖率），并输出权重决策明细。

    返回 (weights, decisions)：
      weights:    fund_code/stock_code/stock_name/weight/weight_source/weight_quarter/coverage_pct
      decisions:  fund_code/policy/weight_source/weight_quarter/coverage_pct/n_stocks/usable_quarters
    """
    h = _clean_holdings(hold)
    stats = report_quarter_stats(h)
    rows, decs = [], []
    for _, u in universe.iterrows():
        code, policy = u["fund_code"], u["weight_policy"]
        idx_code = u.get("index_code") or ""
        fp = os.path.join(idx_dir, f"{idx_code}.csv")
        wmap, src, quarter, coverage = {}, None, None, None
        used_idx = False
        if policy == "index" and idx_code and os.path.exists(fp):
            idx = pd.read_csv(fp, dtype=str)
            idx["weight_pct"] = pd.to_numeric(idx["weight_pct"], errors="coerce")
            tot = idx["weight_pct"].sum()
            if tot and tot > 0:
                wmap = dict(zip(idx["stock_code"],
                                [(float(w) / tot, nm) for w, nm in
                                 zip(idx["weight_pct"], idx["stock_name"])]))
                src = f"index_csindex({idx_code})"
                quarter = str(idx["date"].iloc[0])
                coverage = 100.0
                used_idx = True
        if not wmap:
            # 回退：季报链（最新可用季度；<95% 时回退最近完整季度）
            fs = stats[stats["fund_code"] == code]
            usable = fs[fs["pct_sum"] >= COV_USABLE].sort_values("quarter", ascending=False)
            full = fs[fs["pct_sum"] >= COV_FULL].sort_values("quarter", ascending=False)
            pick = None
            if len(usable):
                if usable.iloc[0]["pct_sum"] < COV_FULL and len(full):
                    pick = full.iloc[0]
                else:
                    pick = usable.iloc[0]
            if pick is not None:
                q = pick["quarter"]
                sub = h[(h["fund_code"] == code) & (h["quarter"] == q)].copy()
                msum = sub["mktcap_wan"].sum()
                sub["weight"] = (sub["mktcap_wan"] / msum) if msum and msum > 0 \
                    else (sub["pct_nav"] / sub["pct_nav"].sum())
                wmap = dict(zip(sub["stock_code"], zip(sub["weight"], sub["stock_name"])))
                src = f"report({q})"
                quarter = q
                coverage = float(pick["pct_sum"])
        if not wmap:
            decs.append({"fund_code": code, "policy": policy, "weight_source": "missing",
                         "weight_quarter": None, "coverage_pct": None, "n_stocks": 0,
                         "usable_quarters": ",".join(
                             f"{q}:{c:.1f}" for q, c in zip(
                                 stats[stats['fund_code'] == code]['quarter'],
                                 stats[stats['fund_code'] == code]['pct_sum'])) or ""})
            continue
        for sc, (w, nm) in wmap.items():
            rows.append({"fund_code": code, "stock_code": sc, "stock_name": nm,
                         "weight": float(w), "weight_source": src,
                         "weight_quarter": quarter, "coverage_pct": coverage})
        decs.append({"fund_code": code, "policy": policy, "weight_source": src,
                     "weight_quarter": quarter, "coverage_pct": coverage,
                     "n_stocks": len(wmap),
                     "usable_quarters": ",".join(
                         f"{q}:{c:.1f}" for q, c in zip(
                             stats[stats['fund_code'] == code]['quarter'],
                             stats[stats['fund_code'] == code]['pct_sum'])) or "",
                     "used_index": used_idx})
    return pd.DataFrame(rows), pd.DataFrame(decs)


def _prev_nav(nv: pd.DataFrame, t: pd.Timestamp):
    """t 日前一交易日（最近 nav_date < t）的单位净值；无则 None。"""
    cand = nv[nv["nav_date"] < t]
    if cand.empty:
        return None
    return float(cand.sort_values("nav_date").iloc[-1]["unit_nav"])


def trading_dates(shares: pd.DataFrame, nav: pd.DataFrame) -> pd.DatetimeIndex:
    """Use an audited calendar if present, otherwise observed dates (incomplete)."""
    calendar_path = os.path.join(DATA_DIR, "trading_calendar.csv")
    if os.path.exists(calendar_path):
        calendar = pd.to_datetime(pd.read_csv(calendar_path)["trade_date"], errors="raise")
    else:
        calendar = pd.concat([shares["stat_date"], nav["nav_date"]]).dropna()
    return pd.DatetimeIndex(pd.to_datetime(calendar).dropna().unique()).sort_values()


def compute_flow(universe: pd.DataFrame, weights: pd.DataFrame,
                 shares: pd.DataFrame, nav: pd.DataFrame
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """计算逐 ETF×日 的个股贡献与基金×日质量标记。"""
    calendar = trading_dates(shares, nav)
    active = universe[universe["status"] == "active"]
    contrib, marks = [], []
    for _, u in active.iterrows():
        code = u["fund_code"]
        sh = shares[shares["fund_code"] == code].sort_values("stat_date").copy()
        if sh.empty:
            continue
        sh["delta"] = sh["shares"].diff()
        sh["previous_date"] = sh["stat_date"].shift()
        sh["previous_shares"] = sh["shares"].shift()
        nv = nav[nav["fund_code"] == code].sort_values("nav_date")
        wsub = weights[weights["fund_code"] == code]
        src = wsub["weight_source"].iloc[0] if len(wsub) else "missing"
        cov = float(wsub["coverage_pct"].iloc[0]) if len(wsub) else np.nan
        listed = u["listed_date"]
        for _, r in sh.iterrows():
            t = r["stat_date"]
            delta = r["delta"]
            shares_t = r["shares"]
            if pd.isna(delta) or pd.isna(shares_t):
                continue
            flags = []
            previous_date = r["previous_date"]
            prev_shares = r["previous_shares"]
            if any(previous_date < day < t for day in calendar):
                marks.append({"date": t.strftime("%Y-%m-%d"), "fund_code": code,
                              "delta_shares": None, "delta_pct": None,
                              "nav_used": None, "coverage_pct": cov,
                              "weight_source": src, "flags": "data_gap"})
                continue
            previous_nav = nv[nv["nav_date"] == previous_date]["unit_nav"].dropna()
            nav_prev = float(previous_nav.iloc[-1]) if len(previous_nav) else None
            if listed is not None and pd.notna(listed) and \
                    (t - listed).days < LISTING_DAYS_MIN:
                continue  # 建仓期不纳入
            if len(wsub) == 0 or cov < COV_USABLE:
                flags.append("weight_missing")
            if nav_prev is None:
                flags.append("nav_missing")
            d_pct = abs(delta) / prev_shares if prev_shares else np.nan
            if pd.notna(d_pct) and d_pct > DELTA_PCT_LIMIT:
                flags.append("large_sub_red")
                # 疑似折算：规模（份额×净值）相对变动 <1% → 贡献置 0
                nav_t_series = nv[nv["nav_date"] == t]["unit_nav"]
                if len(nav_t_series) and pd.notna(nav_prev):
                    size_t = shares_t * float(nav_t_series.iloc[0])
                    size_tm1 = (prev_shares or 0.0) * nav_prev
                    if size_tm1 > 0 and abs(size_t - size_tm1) / size_tm1 < SPLIT_SIZE_TOL:
                        flags.append("possible_split")
                        marks.append({"date": t.strftime("%Y-%m-%d"), "fund_code": code,
                                      "delta_shares": delta, "delta_pct": d_pct,
                                      "nav_used": nav_prev, "coverage_pct": cov,
                                      "weight_source": src,
                                      "flags": "|".join(flags)})
                        continue
            marks.append({"date": t.strftime("%Y-%m-%d"), "fund_code": code,
                          "delta_shares": delta, "delta_pct": d_pct,
                          "nav_used": nav_prev, "coverage_pct": cov,
                          "weight_source": src, "flags": "|".join(flags) or ""})
            if nav_prev is None or len(wsub) == 0:
                prev_shares = shares_t
                continue
            money = delta * nav_prev
            for _, wrow in wsub.iterrows():
                contrib.append({
                    "date": t.strftime("%Y-%m-%d"), "fund_code": code,
                    "stock_code": wrow["stock_code"], "stock_name": wrow["stock_name"],
                    "weight": float(wrow["weight"]), "delta_shares": delta,
                    "nav_used": nav_prev, "contribution_yuan": money * float(wrow["weight"]),
                    "weight_source": wrow["weight_source"],
                    "coverage_pct": wrow["coverage_pct"]})
            prev_shares = shares_t
    return pd.DataFrame(contrib), pd.DataFrame(marks)


def aggregate(contrib: pd.DataFrame, floatcap: pd.DataFrame, calendar=None
              ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """个股日度汇总 → 单日/5日/20日/强度/TOP5。"""
    if contrib.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    daily = (contrib.groupby(["date", "stock_code"], as_index=False)
             .agg(flow_yuan=("contribution_yuan", "sum"),
                  n_etf=("fund_code", "nunique"),
                  stock_name=("stock_name", "first")))
    daily["date"] = pd.to_datetime(daily["date"])
    # TOP5 拆解
    top_rows = []
    for (d, s), grp in contrib.groupby(["date", "stock_code"]):
        g = grp.sort_values("contribution_yuan", key=abs, ascending=False).head(5)
        top_rows.append({
            "date": d, "stock_code": s,
            "top5": "; ".join(f"{r['fund_code']}:{r['contribution_yuan']:.0f}" for _, r in g.iterrows())})
    top5 = pd.DataFrame(top_rows)
    # 累计与强度
    wide = daily.pivot(index="date", columns="stock_code", values="flow_yuan").sort_index()
    if calendar is not None:
        # Union prevents dropping data if a supplied calendar is incomplete.
        days = pd.DatetimeIndex(calendar)
        days = days[(days >= wide.index.min()) & (days <= wide.index.max())]
        wide = wide.reindex(wide.index.union(days).sort_values())
    out = []
    for sc in wide.columns:
        s = wide[sc]
        for i, (d, v) in enumerate(s.items()):
            if pd.isna(v):
                continue
            w5 = s.iloc[max(0, i - 4): i + 1]
            w20 = s.iloc[max(0, i - 19): i + 1]
            v5 = w5.sum() if w5.notna().sum() >= MIN_VALID_DAYS_5 else np.nan
            v20 = w20.sum() if w20.notna().sum() >= MIN_VALID_DAYS_20 else np.nan
            out.append({"date": d.strftime("%Y-%m-%d"), "stock_code": sc,
                        "flow_1d_yuan": v, "flow_5d_yuan": v5, "valid_days_5": int(w5.notna().sum()),
                        "flow_20d_yuan": v20, "valid_days_20": int(w20.notna().sum())})
    derived = pd.DataFrame(out)
    fc = floatcap[["symbol", "float_mv_yi"]].rename(columns={"symbol": "stock_code"})
    derived = derived.merge(fc, on="stock_code", how="left")
    for col in ["flow_1d_yuan", "flow_5d_yuan", "flow_20d_yuan"]:
        derived[f"intensity_{col.replace('flow_', '').replace('_yuan', '')}_bp"] = (
            derived[col] / (derived["float_mv_yi"] * 1e8) * 1e4)
    return daily, derived, top5


def write_outputs(daily, derived, top5, contrib, marks, weights, decisions) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = {
        "flow_daily": os.path.join(OUT_DIR, "flow_daily.csv"),
        "flow_derived": os.path.join(OUT_DIR, "flow_derived.csv"),
        "top_contributors": os.path.join(OUT_DIR, "top_contributors.csv"),
        "flow_by_etf": os.path.join(OUT_DIR, "flow_by_etf.csv"),
        "quality_marks": os.path.join(OUT_DIR, "quality_marks.csv"),
        "weights_used": os.path.join(OUT_DIR, "weights_used.csv"),
        "weight_decisions": os.path.join(OUT_DIR, "weight_decisions.csv"),
    }
    daily.to_csv(paths["flow_daily"], index=False, encoding="utf-8-sig")
    derived.to_csv(paths["flow_derived"], index=False, encoding="utf-8-sig")
    top5.to_csv(paths["top_contributors"], index=False, encoding="utf-8-sig")
    contrib.to_csv(paths["flow_by_etf"], index=False, encoding="utf-8-sig")
    marks.to_csv(paths["quality_marks"], index=False, encoding="utf-8-sig")
    weights[["fund_code", "stock_code", "stock_name", "weight", "weight_source",
             "weight_quarter", "coverage_pct"]].to_csv(
        paths["weights_used"], index=False, encoding="utf-8-sig")
    decisions.to_csv(paths["weight_decisions"], index=False, encoding="utf-8-sig")
    return paths
