# -*- coding: utf-8 -*-
"""M4 阶段一 · L2 复算对账（qa-compliance）。

独立重算（不调用模型代码，只读模型原始输出 CSV）：
  Flow(i,t)   = Σ_e contribution_yuan(e,i,t)          （flow_by_etf.csv 逐行求和）
  intensity_bp= flow / (float_mv_yi * 1e8) * 1e4        （当日流通市值快照分母，模型口径）
  5/20 日窗口  = 窗口内逐日 Flow 求和；有效天数 5日<3 / 20日<12 -> null（非 0）

比对对象：
  /api/v1/rank（1d 榜单 TOP100 全部条目 + 抽样条目）与 /api/v1/stock/{code}（flow_1d/5d/20d、
  intensity_bp、top_etfs 拆解、valid_days）。
  另做诊断层：API 输出 vs flow_daily.csv / flow_derived.csv（入库链路一致性）。

抽样：榜单头部 4 / 中游 4 / 尾部 4（共 >=10 只，去重后不足则补中游相邻名次）。
容差：相对 0.1% + 绝对 0.011（API 金额/bp 保留 2 位小数，0.011 覆盖舍入与求和顺序差）。

用法：
  python tests/l2_recompute.py [base_url] [--outputs DIR] [--date YYYY-MM-DD]
前置：服务运行（server/: python main.py --no-scheduler）；--outputs 默认 server/work/outputs。
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


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail[:280]}")


def tol(api: float) -> float:
    """相对 0.1% + 绝对 0.011（2 位小数舍入容差）。"""
    return abs(api) * 0.001 + 0.011


def close(rec: float, api: float) -> bool:
    return abs(rec - api) <= tol(api)


def get_json(path: str, **params):
    r = requests.get(BASE + path, params=params or None, timeout=TIMEOUT)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else None)


def pct(rec: float, api: float) -> str:
    if api == 0:
        return f"abs_diff={abs(rec - api):.4g}"
    return f"rel={abs(rec - api) / abs(api) * 100:.4f}%"


def main() -> None:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", nargs="?", default=BASE)
    ap.add_argument("--outputs", default=None)
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    BASE = args.base_url.rstrip("/")

    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(here)
    out_dir = args.outputs or os.path.join(proj, "server", "work", "outputs")

    print(f"== L2 复算对账 @ {BASE}（outputs: {out_dir}）==")

    # 1) 元信息与全量榜单
    sc, meta = get_json("/api/v1/meta")
    if sc != 200 or not meta or meta.get("code") != 0:
        print(f"[FATAL] /api/v1/meta 不可用（http={sc}）{str(meta)[:200]}")
        sys.exit(2)
    td = args.date or meta["data"]["latest_date"]
    print(f"  对账日期 trade_date={td}（latest={meta['data']['latest_date']}）")

    sc, rank_j = get_json("/api/v1/rank", period="1d", page_size=100, trade_date=td)
    if sc != 200 or rank_j.get("code") != 0:
        print(f"[FATAL] /api/v1/rank 不可用（http={sc}）{str(rank_j)[:200]}")
        sys.exit(2)
    items = rank_j["data"]["items"]
    print(f"  榜单 total={rank_j['data']['total']} items={len(items)}")

    # 2) 读取模型原始输出（独立于 API）
    fb = pd.read_csv(os.path.join(out_dir, "flow_by_etf.csv"), dtype=str, keep_default_na=False)
    de = pd.read_csv(os.path.join(out_dir, "flow_derived.csv"), dtype=str, keep_default_na=False)
    fd = pd.read_csv(os.path.join(out_dir, "flow_daily.csv"), dtype=str, keep_default_na=False)

    def f(v):
        if v in ("", "nan", "None"):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    fb["contribution_yuan"] = fb["contribution_yuan"].map(f)
    d_day = fb[fb["date"] == td]
    rec_flow = d_day.groupby("stock_code")["contribution_yuan"].sum().to_dict()
    n_etf_map = d_day.groupby("stock_code").size().to_dict()
    de_day = de[de["date"] == td].set_index("stock_code")
    fd_day = fd[fd["date"] == td].set_index("stock_code")

    # 3) 全量榜单复算（100 条）
    print("\n  [L2-1] 榜单 flow 全量复算（TOP100，容差 0.1%+0.011）")
    max_rel, worst = 0.0, None
    n_ok = 0
    for it in items:
        code = it["stock_code"]
        if code not in rec_flow:
            check(f"榜单 {code} 在 flow_by_etf 有贡献行", False, "复算源缺失")
            continue
        rec = rec_flow[code]
        api = float(it["flow"])
        if close(rec, api):
            n_ok += 1
        else:
            check(f"榜单 {code} flow={api} vs 复算 {rec}", False, pct(rec, api))
        r = abs(rec - api) / max(abs(api), 1.0)
        if r > max_rel:
            max_rel, worst = r, (code, api, rec)
    check(f"榜单 TOP{len(items)} 全量复算通过（{n_ok}/{len(items)}，max_err={max_rel * 100:.4f}%）",
          n_ok == len(items), f"worst={worst}")

    # 4) 榜单 vs 模型输出（入库链路诊断）
    diag_bad = 0
    for it in items:
        code = it["stock_code"]
        row = fd_day.loc[code] if code in fd_day.index else None
        if row is None or row.get("flow_yuan") in ("", "nan", "None"):
            diag_bad += 1
            continue
        if not close(float(row["flow_yuan"]), float(it["flow"])):
            diag_bad += 1
    check("L2-1b 榜单 flow 与 flow_daily.csv 入库一致", diag_bad == 0, f"diff={diag_bad}")

    # 5) 抽样：头部 4 / 中游 4 / 尾部 4
    n = len(items)
    idx = sorted(set([0, 1, 2, 3, n // 2 - 2, n // 2 - 1, n // 2, n // 2 + 1,
                      n - 4, n - 3, n - 2, n - 1]))
    sample = [items[i] for i in idx if 0 <= i < n]
    codes = list(dict.fromkeys(it["stock_code"] for it in sample))  # 去重保序
    print(f"\n  [L2-2] 抽样 {len(codes)} 只（榜单第 {sorted(i + 1 for i in idx)} 名）个股详情复算")

    for code in codes:
        sc, sj = get_json(f"/api/v1/stock/{code}", trade_date=td)
        if sc != 200 or sj.get("code") != 0:
            check(f"{code} 详情可用", False, f"http={sc}")
            continue
        d = sj["data"]
        rec = rec_flow.get(code)
        # flow_1d
        if d.get("flow_1d") is None:
            check(f"{code} flow_1d 为 null 且复算无数据", rec is None, str(d.get("flow_1d")))
        else:
            check(f"{code} flow_1d={d['flow_1d']} vs 复算 {rec}", rec is not None
                  and close(rec, d["flow_1d"]), pct(rec, d["flow_1d"]))
        # intensity_1d（当日快照分母）
        mcap = de_day.loc[code, "float_mv_yi"] if code in de_day.index else None
        mcap = f(mcap)
        if d.get("intensity_1d_bp") is None:
            check(f"{code} intensity_1d 为 null 且分母缺失", mcap is None, "分母存在但 API 为 null")
        else:
            rec_bp = (d["flow_1d"] / (mcap * 1e8) * 1e4) if mcap else None
            check(f"{code} intensity_1d_bp={d['intensity_1d_bp']} vs 复算",
                  rec_bp is not None and close(rec_bp, d["intensity_1d_bp"]),
                  pct(rec_bp, d["intensity_1d_bp"]))
        # top_etfs 拆解：与 flow_by_etf TOP5 一致
        rows = d_day[d_day["stock_code"] == code].copy()
        rows["abs_c"] = rows["contribution_yuan"].abs()
        rows = rows.sort_values("abs_c", ascending=False).head(5)
        api_top = [(t["etf_code"], t["flow"]) for t in d.get("top_etfs", [])]
        rec_top = [(r["fund_code"], r["contribution_yuan"]) for _, r in rows.iterrows()]
        ok = (len(api_top) == len(rec_top)
              and [t[0] for t in api_top] == [t[0] for t in rec_top]
              and all(close(r[1], a[1]) for r, a in zip(rec_top, api_top)))
        check(f"{code} top_etfs 拆解 TOP5 一致", ok,
              f"api={[t[0] for t in api_top]} rec={[t[0] for t in rec_top]}")
        n_etf = n_etf_map.get(code, 0)
        if n_etf <= 5 and n_etf > 0:
            s_top = sum(t[1] for t in api_top)
            check(f"{code} Σtop_etfs≈flow_1d（n_etf={n_etf}）", close(s_top, d["flow_1d"]),
                  pct(s_top, d["flow_1d"]))
        # valid_days d1
        check(f"{code} valid_days.d1=1/1", d.get("valid_days", {}).get("d1") == "1/1",
              str(d.get("valid_days")))

    # 6) 5/20 日窗口复算（对抽样股票，独立窗口重算 + 空值规则）
    print("\n  [L2-3] 5/20 日窗口复算（最小有效天数 3/12，null 非 0）")
    fb_all = fb[fb["date"] <= td]
    for code in codes[:5]:
        series = (fb_all[fb_all["stock_code"] == code]
                  .groupby("date")["contribution_yuan"].sum().sort_index())
        if series.empty:
            continue
        last5 = series.tail(5)
        last20 = series.tail(20)
        v5 = last5.sum() if last5.notna().sum() >= 3 else None
        v20 = last20.sum() if last20.notna().sum() >= 12 else None
        sc, sj = get_json(f"/api/v1/stock/{code}", trade_date=td)
        d = sj["data"]
        ok5 = (v5 is None and d.get("flow_5d") is None) or \
              (v5 is not None and d.get("flow_5d") is not None and close(v5, d["flow_5d"]))
        ok20 = (v20 is None and d.get("flow_20d") is None) or \
               (v20 is not None and d.get("flow_20d") is not None and close(v20, d["flow_20d"]))
        check(f"{code} 5日窗口（vd={int(last5.notna().sum())}）", ok5,
              f"rec={v5} api={d.get('flow_5d')} vd_api={d.get('valid_days', {}).get('d5')}")
        check(f"{code} 20日窗口（vd={int(last20.notna().sum())}）", ok20,
              f"rec={v20} api={d.get('flow_20d')} vd_api={d.get('valid_days', {}).get('d20')}")

    print(f"\n== 结果: {PASS} PASS / {FAIL} FAIL ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
