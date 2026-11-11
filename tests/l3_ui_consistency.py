# -*- coding: utf-8 -*-
"""M4 阶段一 · L3 界面一致性（qa-compliance）。

跨页面/跨接口数值一致性核查（金额单位「元」、强度 bp）：
  1. 榜单 ↔ 个股详情：rank.flow == stock.flow_1d、rank.intensity_bp == stock.intensity_1d_bp；
  2. 个股详情 ↔ ETF 详情：stock.top_etfs 与 etf.top_stocks 双向贡献值一致；
  3. 个股总流入恒等式：Σ(flow_by_etf 全部贡献) == flow_1d，合计误差 ≤ 1 元；
  4. ETF 详情恒等式：est_flow == Σ(该 ETF 当日全部贡献)，≤ 1 元；
     share_change / nav_prev 与 quality_marks.csv 一致；
  5. 序列衔接：series 末点 == 当日 flow_1d / intensity_1d_bp；
  6. 行业榜单 ⊆ 全市场榜单（同股同值），行业名与字典一致。

用法：python tests/l3_ui_consistency.py [base_url] [--outputs DIR]
退出码：0 = 全部 PASS；1 = 存在 FAIL。
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
import requests

BASE = "http://127.0.0.1:8000"
TIMEOUT = 30
PASS = 0
FAIL = 0
YI = 1.0          # 合计恒等式误差上限（元）


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail[:280]}")


def get_json(path: str, **params):
    r = requests.get(BASE + path, params=params or None, timeout=TIMEOUT)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def eq(a, b, tol=0.011):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


def f(v):
    if v in ("", "nan", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> None:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", nargs="?", default=BASE)
    ap.add_argument("--outputs", default=None)
    args = ap.parse_args()
    BASE = args.base_url.rstrip("/")

    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(here)
    out_dir = args.outputs or os.path.join(proj, "server", "work", "outputs")

    sc, meta = get_json("/api/v1/meta")
    if sc != 200 or not meta or meta.get("code") != 0:
        print(f"[FATAL] meta 不可用 http={sc}")
        sys.exit(2)
    td = meta["data"]["latest_date"]
    print(f"== L3 界面一致性 @ {BASE}（trade_date={td}）==")

    fb = pd.read_csv(os.path.join(out_dir, "flow_by_etf.csv"), dtype=str, keep_default_na=False)
    de = pd.read_csv(os.path.join(out_dir, "flow_derived.csv"), dtype=str, keep_default_na=False)
    qm = pd.read_csv(os.path.join(out_dir, "quality_marks.csv"), dtype=str, keep_default_na=False)
    fb["contribution_yuan"] = fb["contribution_yuan"].map(f)
    fb_d = fb[fb["date"] == td]

    sc, rj = get_json("/api/v1/rank", period="1d", page_size=100)
    items = rj["data"]["items"]
    n = len(items)
    idx = sorted(set([0, 1, 2, 3, n // 2 - 2, n // 2 - 1, n // 2, n // 2 + 1,
                      n - 4, n - 3, n - 2, n - 1]))
    codes = list(dict.fromkeys(items[i]["stock_code"] for i in idx if 0 <= i < n))
    rank_map = {it["stock_code"]: it for it in items}
    print(f"  抽样 {len(codes)} 只：榜单第 {sorted(i + 1 for i in idx)} 名")

    for code in codes:
        sc, sj = get_json(f"/api/v1/stock/{code}", trade_date=td)
        if sc != 200 or sj.get("code") != 0:
            check(f"{code} 详情可用", False, f"http={sc}")
            continue
        d = sj["data"]
        it = rank_map[code]
        check(f"{code} rank.flow==stock.flow_1d", eq(it["flow"], d.get("flow_1d")),
              f"{it['flow']} vs {d.get('flow_1d')}")
        check(f"{code} rank.intensity_bp==stock.intensity_1d_bp",
              eq(it["intensity_bp"], d.get("intensity_1d_bp")),
              f"{it['intensity_bp']} vs {d.get('intensity_1d_bp')}")
        # 恒等式：Σ 全部贡献 == flow_1d（≤1 元）
        s_contrib = fb_d[fb_d["stock_code"] == code]["contribution_yuan"]
        if d.get("flow_1d") is not None and len(s_contrib):
            diff = abs(s_contrib.sum() - d["flow_1d"])
            check(f"{code} Σ贡献==flow_1d（|diff|={diff:.2g} ≤ 1 元）", diff <= YI, f"diff={diff}")
        # 序列末点衔接
        ser = d.get("series") or []
        if ser:
            check(f"{code} series 末点 flow==flow_1d", eq(ser[-1].get("flow"), d.get("flow_1d")))
            check(f"{code} series 末点 intensity==intensity_1d_bp",
                  eq(ser[-1].get("intensity_bp"), d.get("intensity_1d_bp")))
        # 个股 ↔ ETF 双向拆解
        for t in d.get("top_etfs", []):
            sc2, ej = get_json(f"/api/v1/etf/{t['etf_code']}", trade_date=td)
            if sc2 != 200 or ej.get("code") != 0:
                continue
            ts = {s["stock_code"]: s["flow"] for s in ej["data"].get("top_stocks", [])}
            if code in ts:
                check(f"{code}↔{t['etf_code']} 双向贡献一致", eq(t["flow"], ts[code]),
                      f"{t['flow']} vs {ts[code]}")

    # ETF 详情恒等式（抽 3 只：宽基/行业/主题）
    print("  [L3-ETF] ETF 详情恒等式")
    for fcode in ("510300", "512880", "515790"):
        sc, ej = get_json(f"/api/v1/etf/{fcode}", trade_date=td)
        if sc != 200 or ej.get("code") != 0:
            check(f"{fcode} ETF 详情可用", False, f"http={sc}")
            continue
        e = ej["data"]
        f_contrib = fb_d[fb_d["fund_code"] == fcode]["contribution_yuan"]
        if e.get("est_flow") is not None and len(f_contrib):
            diff = abs(f_contrib.sum() - e["est_flow"])
            check(f"{fcode} est_flow==Σ贡献（|diff|={diff:.2g} ≤ 1 元）", diff <= YI, f"diff={diff}")
        mrow = qm[(qm["date"] == td) & (qm["fund_code"] == fcode)]
        if len(mrow):
            delta = f(mrow.iloc[0]["delta_shares"])
            nav = f(mrow.iloc[0]["nav_used"])
            check(f"{fcode} share_change==quality_marks.delta_shares",
                  eq(e.get("share_change"), delta), f"{e.get('share_change')} vs {delta}")
            check(f"{fcode} nav_prev==quality_marks.nav_used",
                  eq(e.get("nav_prev"), nav), f"{e.get('nav_prev')} vs {nav}")

    # 行业榜单 ⊆ 全市场榜单（同股同值；行业榜个股可能不在全市场 TOP100，此时对详情接口核对）
    sc, ij = get_json("/api/v1/industries")
    inds = [i for i in ij["data"]["items"] if i["stock_count"] > 0]
    if inds:
        ic = inds[0]
        sc, rj2 = get_json("/api/v1/rank", period="1d", industry=ic["industry_code"], page_size=100)
        sub = rj2["data"]["items"]
        ok = all(i["industry"] == ic["industry_code"] and i["industry_name"] == ic["industry_name"]
                 for i in sub)
        for it2 in sub[:20]:  # 抽样核对前 20 条同股同值
            if it2["stock_code"] in rank_map:
                ok = ok and eq(rank_map[it2["stock_code"]]["flow"], it2["flow"])
            else:
                sc3, sj3 = get_json(f"/api/v1/stock/{it2['stock_code']}", trade_date=td)
                ok = ok and sc3 == 200 and eq(sj3["data"].get("flow_1d"), it2["flow"])
        check(f"行业榜[{ic['industry_name']}] ⊆ 全市场榜且同股同值（{len(sub)} 条）", ok)
        check("行业榜 industry_name 与字典一致",
              rj2["data"]["industry_name"] == ic["industry_name"])

    print(f"\n== 结果: {PASS} PASS / {FAIL} FAIL ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
