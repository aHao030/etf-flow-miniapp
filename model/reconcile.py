# -*- coding: utf-8 -*-
"""M1 抽样对账脚本（task-2, quant-analyst）

四层对账（对应《m1-模型设计草案》§6 与 task-2 验收口径）：
  ① 份额内部一致性：SSE 官方 23 交易日 × 13 只，逐日完整性 + 突变统计（复核 A10）
  ② 份额跨源核对：天天基金 F10 gmbd（季度规模变动）vs 上交所官方份额
       - Q3 期末总份额(2026-09-30) 点核对
       - Q3 净申赎份额(申购-赎回) vs SSE 份额差(06-30→09-30)【±25% 门槛 A11】
       - 深市 5 只：gmbd 与 SZSE 快照日期相差约一个月，仅口径说明、不点核对（历史待 M2）
  ③ 金额量级：gmbd Q3 期末净资产 vs SSE 份额(09-30) × 单位净值(09-30)（lsjz 回补）
  ④ 权重对账：宽基 csindex 指数权重 vs 季报前 100（重叠覆盖率/MAE/相关系数）；
       行业主题覆盖率与回退决策取自 compute_flow 输出

网络调用：上交所 commonQuery.do（历史日期）、fundf10 gmbd、lsjz（低频 + 完整请求头）。
用法: python reconcile.py
输出: model/outputs/reconciliation/*.csv + reconciliation_summary.txt
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
from lxml import html as lxml_html

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v1_flow as V  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "outputs", "reconciliation")
DATA = os.path.join(ROOT, "..", "data", "samples")

SH_CODES = ["510050", "510300", "510500", "512100", "563360", "588000",
            "512170", "512480", "512660", "512690", "512880", "512760", "515790"]
SZ_CODES = ["159901", "159915", "159922", "159928", "159949"]
ALL_CODES = SH_CODES + SZ_CODES
GATE_PCT = 25.0  # A11（已拍板）：量级对账门槛 ±25%

os.makedirs(OUT, exist_ok=True)


def http_get(url, headers, decode="utf-8"):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode(decode, errors="replace")


def fetch_sse(stat_date):
    params = {
        "isPagination": "true", "pageHelp.pageSize": "10000", "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1", "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", "STAT_DATE": stat_date,
    }
    url = "https://query.sse.com.cn/commonQuery.do?" + urllib.parse.urlencode(params)
    rows = json.loads(http_get(url, {"Referer": "https://www.sse.com.cn/", "User-Agent": UA}))
    return {r["SEC_CODE"]: float(r["TOT_VOL"]) * 1e4 for r in (rows.get("result") or [])}


def _extract_content(text):
    """从 var xxx_apidata={ content:"..." } 中稳健提取 HTML 内容字符串。"""
    start = text.index('content:"') + len('content:"')
    i = start
    while i < len(text):
        if text[i] == '"' and (i == 0 or text[i - 1] != "\\"):
            break
        i += 1
    return text[start:i]


def fetch_gmbd(code):
    url = ("https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
           f"?type=gmbd&code={code}&rt={time.time():.6f}")
    headers = {"Referer": f"https://fundf10.eastmoney.com/gmbd_{code}.html",
               "User-Agent": UA, "X-Requested-With": "XMLHttpRequest"}
    text = http_get(url, headers)
    if 'content:"' not in text:
        return []
    html = _extract_content(text).replace('\\"', '"').replace("\\/", "/")
    doc = lxml_html.fromstring(html)
    out = []
    for table in doc.xpath("//table"):
        ths = [th.text_content().strip() for th in table.xpath(".//thead//th")]
        if not any("期间申购" in t for t in ths):
            continue
        idx = {"purchase": next((i for i, t in enumerate(ths) if "期间申购" in t), None),
               "redeem": next((i for i, t in enumerate(ths) if "期间赎回" in t), None),
               "end_shares": next((i for i, t in enumerate(ths) if "期末总份额" in t), None),
               "end_nav": next((i for i, t in enumerate(ths) if "期末净资产" in t), None)}
        for tr in table.xpath(".//tbody/tr"):
            tds = tr.xpath("./td")
            if len(tds) < 5:
                continue
            dm = re.match(r"(\d{4})-(\d{2})-(\d{2})", tds[0].text_content())
            if not dm:
                continue
            y, m = int(dm.group(1)), int(dm.group(2))
            if m not in (3, 6, 9, 12):
                continue  # 仅季度末行
            quarter = f"{y}Q{(m - 1) // 3 + 1}"

            def num(i):
                if i is None or i >= len(tds):
                    return np.nan
                v = re.sub(r"[^\d.\-]", "", tds[i].text_content())
                try:
                    return float(v)
                except ValueError:
                    return np.nan

            out.append({"quarter": quarter,
                        "purchase_yi": num(idx["purchase"]),
                        "redeem_yi": num(idx["redeem"]),
                        "end_shares_yi": num(idx["end_shares"]),
                        "end_nav_yi": num(idx["end_nav"])})
    return out


def fetch_lsjz(code, pages=8):
    """返回 {FSRQ: (单位净值, 累计净值)}；累计净值跨拆分连续，用于分解市场涨跌。"""
    rows = {}
    for p in range(1, pages + 1):
        url = (f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}"
               f"&pageIndex={p}&pageSize=49")
        headers = {"Referer": "https://fundf10.eastmoney.com/", "User-Agent": UA}
        try:
            j = json.loads(http_get(url, headers))
            for r in (j.get("Data") or {}).get("LSJZList") or []:
                try:
                    dwjz = float(r["DWJZ"])
                except (TypeError, ValueError):
                    continue
                try:
                    ljjz = float(r["LJJZ"])
                except (TypeError, ValueError):
                    ljjz = np.nan
                rows[r["FSRQ"]] = (dwjz, ljjz)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] lsjz {code} p{p}: {type(exc).__name__} {exc}")
        time.sleep(0.35)
    return rows


def find_split(navs, start_d, end_d):
    """季度内疑似份额拆分检测：单位净值单日跌 >23%，用累计净值连续性分解市场涨跌。

    拆分日: NAV(d) = NAV(d-1) × m / R（m=市场涨跌=LJJZ(d)/LJJZ(d-1)，R=拆分比例）。
    返回 (拆分日, 拆分比例 R, 市场涨跌 m)；无 → None。
    """
    dates = [d for d in sorted(navs) if start_d <= d <= end_d]
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        n0, n1 = navs[d0][0], navs[d1][0]
        if n1 <= 0 or n0 / n1 < 1.30:
            continue
        l0, l1 = navs[d0][1], navs[d1][1]
        m = (l1 / l0) if (l0 and l1 and l0 > 0 and np.isfinite(l0) and np.isfinite(l1)) else 1.0
        return d1, n0 * m / n1, m
    return None


def part1_internal(shares):
    print("== 1) internal consistency of SSE official series ==")
    rows = []
    for code in SH_CODES:
        s = shares[shares["fund_code"] == code].sort_values("stat_date")
        dup = s["stat_date"].duplicated().sum()
        s = s.copy()
        s["delta"] = s["shares"].diff()
        s["delta_pct"] = (s["delta"] / s["shares"].shift(1)).abs()
        rows.append({"fund_code": code, "n_days": len(s), "dup_dates": dup,
                     "max_abs_delta_pct": round(float(s["delta_pct"].max() * 100), 2),
                     "mean_abs_delta_pct": round(float(s["delta_pct"].mean() * 100), 2),
                     "n_over_30pct": int((s["delta_pct"] > 0.30).sum())})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "internal_shares_stats.csv"), index=False, encoding="utf-8-sig")
    print(df.to_string(index=False))
    return df


def part2_gmbd_vs_sse():
    print("== 2) cross-source shares: fundf10 gmbd vs SSE official ==")
    sse = {d: fetch_sse(d) for d in ["2026-03-31", "2026-06-30", "2026-09-30"]}
    time.sleep(0.3)
    gmbd = {}
    for code in ALL_CODES:
        try:
            gmbd[code] = fetch_gmbd(code)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] gmbd {code}: {type(exc).__name__} {exc}")
            gmbd[code] = []
        time.sleep(0.4)
    rows = []
    for code in ALL_CODES:
        avail = {r["quarter"] for r in gmbd.get(code, [])}
        pick = "2026Q3" if "2026Q3" in avail else ("2026Q2" if "2026Q2" in avail else None)
        if pick is None:
            rows.append({"fund_code": code, "market": "SH" if code in SH_CODES else "SZ",
                         "quarter_used": None, "note": "gmbd 2026Q2/Q3 missing"})
            continue
        q = next(r for r in gmbd[code] if r["quarter"] == pick)
        g_end = q["end_shares_yi"] * 1e8
        g_net = (q["purchase_yi"] - q["redeem_yi"]) * 1e8
        start_d = "2026-06-30" if pick == "2026Q3" else "2026-03-31"
        end_d = "2026-09-30" if pick == "2026Q3" else "2026-06-30"
        if code in SH_CODES:
            s_end, s_start = sse[end_d].get(code), sse[start_d].get(code)
            s_net = (s_end - s_start) if (s_end is not None and s_start is not None) else np.nan
            end_diff = (g_end - s_end) / s_end * 100 if s_end else np.nan
            net_diff = (g_net - s_net) / abs(s_net) * 100 if s_net else np.nan
            gate = "PASS" if abs(net_diff) <= GATE_PCT else "FAIL"
            row = {"fund_code": code, "market": "SH", "quarter_used": pick,
                   "gmbd_q_end_shares": g_end, "sse_end_shares": s_end,
                   "end_diff_pct": round(end_diff, 3),
                   "gmbd_q_net_shares": g_net, "sse_q_net_shares": s_net,
                   "net_diff_pct": round(net_diff, 3), "gate_25pct": gate,
                   "split_date": None, "split_ratio": np.nan,
                   "split_market_move": np.nan, "net_diff_adj_pct": np.nan}
            # 期末点对上但净额超门槛 → 疑似季度内份额折算，检测并还原
            if gate == "FAIL" and abs(end_diff) <= 1.0:
                navs = fetch_lsjz(code, pages=8)
                sp = find_split(navs, start_d, end_d)
                if sp is not None:
                    d1, ratio, m = sp
                    s_net_adj = s_end - s_start * ratio
                    net_adj = (g_net - s_net_adj) / abs(s_net_adj) * 100 if s_net_adj else np.nan
                    row.update({"split_date": d1, "split_ratio": round(ratio, 4),
                                "split_market_move": round(m, 4),
                                "net_diff_adj_pct": round(net_adj, 3),
                                "gate_25pct": "PASS(adj)" if abs(net_adj) <= GATE_PCT
                                else "FAIL(adj)"})
            rows.append(row)
        else:
            rows.append({"fund_code": code, "market": "SZ", "quarter_used": pick,
                         "gmbd_q_end_shares": g_end, "sse_end_shares": np.nan,
                         "end_diff_pct": np.nan, "gmbd_q_net_shares": g_net,
                         "sse_q_net_shares": np.nan, "net_diff_pct": np.nan,
                         "gate_25pct": "SKIP(no SZ history)",
                         "split_date": None, "split_ratio": np.nan,
                         "split_market_move": np.nan, "net_diff_adj_pct": np.nan})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "gmbd_vs_sse.csv"), index=False, encoding="utf-8-sig")
    print(df.to_string(index=False))
    return df


def part3_aum_check():
    print("== 3) money magnitude: gmbd quarter-end NAV vs shares x unit-NAV ==")
    gmbd = {}
    for code in SH_CODES:
        try:
            gmbd[code] = fetch_gmbd(code)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] gmbd {code}: {type(exc).__name__} {exc}")
            gmbd[code] = []
        time.sleep(0.4)
    sse = fetch_sse("2026-09-30")
    time.sleep(0.3)
    rows = []
    for code in SH_CODES:
        avail = {r["quarter"] for r in gmbd.get(code, [])}
        pick = "2026Q3" if "2026Q3" in avail else ("2026Q2" if "2026Q2" in avail else None)
        navs = fetch_lsjz(code, pages=8)
        if pick is None:
            rows.append({"fund_code": code, "quarter_used": None, "note": "gmbd missing"})
            continue
        q = next(r for r in gmbd[code] if r["quarter"] == pick)
        end_d = "2026-09-30" if pick == "2026Q3" else "2026-06-30"
        nav_end = navs.get(end_d, (None, None))[0] if end_d in navs else None
        shares = sse.get(code)
        if q is None or shares is None or nav_end is None:
            rows.append({"fund_code": code, "quarter_used": pick,
                         "note": f"missing nav/shares (nav_end={nav_end})"})
            continue
        aum_sse = shares * nav_end
        aum_g = q["end_nav_yi"] * 1e8
        diff = (aum_g - aum_sse) / aum_sse * 100 if aum_sse else np.nan
        rows.append({"fund_code": code, "quarter_used": pick,
                     "gmbd_end_nav_yuan": aum_g, "sse_shares_x_nav_yuan": aum_sse,
                     "nav_end": nav_end, "aum_diff_pct": round(diff, 3)})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "aum_check.csv"), index=False, encoding="utf-8-sig")
    print(df.to_string(index=False))
    return df


def part4_weight_check():
    print("== 4) weights: csindex index weights vs report top-100 ==")
    hold = pd.read_csv(os.path.join(DATA, "etf_holdings_all_quarters.csv"), dtype=str)
    h = V._clean_holdings(hold)
    broad_map = {"510050": "000016", "510300": "000300", "510500": "000905",
                 "512100": "000852", "563360": "000510", "588000": "000688"}
    rows = []
    for fund, idxc in broad_map.items():
        idx = pd.read_csv(os.path.join(V.IDX_DIR, f"{idxc}.csv"), dtype=str)
        idx["weight_pct"] = pd.to_numeric(idx["weight_pct"], errors="coerce")
        tot = idx["weight_pct"].sum()
        idx["w"] = idx["weight_pct"] / tot
        sub = h[h["fund_code"] == fund]
        for q in sorted(sub["quarter"].unique(), reverse=True):
            rep = sub[sub["quarter"] == q].copy()
            msum = rep["mktcap_wan"].sum()
            rep["w"] = rep["mktcap_wan"] / msum if msum else rep["pct_nav"] / rep["pct_nav"].sum()
            m = idx.merge(rep[["stock_code", "w"]], on="stock_code",
                          suffixes=("_idx", "_rep"))
            if len(m) == 0:
                continue
            rows.append({
                "fund_code": fund, "index_code": idxc, "report_quarter": q,
                "n_index": len(idx), "n_overlap": len(m),
                "idx_weight_covered_pct": round(float(m["w_idx"].sum() * 100), 2),
                "mae_w": round(float((m["w_idx"] - m["w_rep"]).abs().mean()), 6),
                "corr": round(float(m["w_idx"].corr(m["w_rep"])), 4),
                "max_abs_diff_w": round(float((m["w_idx"] - m["w_rep"]).abs().max()), 6)})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "weights_index_vs_report.csv"), index=False, encoding="utf-8-sig")
    print(df.to_string(index=False))
    return df


def part5_window_flow():
    print("== 5) window flow magnitude vs AUM (model outputs sanity) ==")
    contrib = pd.read_csv(os.path.join(V.OUT_DIR, "flow_by_etf.csv"),
                          dtype={"fund_code": str})
    nav = pd.read_csv(os.path.join(DATA, "etf_nav_history.csv"), dtype=str)
    shares = pd.read_csv(os.path.join(DATA, "etf_shares_sse_history.csv"), dtype=str)
    last = "2026-10-29"
    aum = {}
    for code in SH_CODES:
        sh = float(shares[(shares["fund_code"] == code) & (shares["stat_date"] == last)]
                   ["shares"].iloc[0])
        nv = float(nav[(nav["fund_code"] == code) & (nav["nav_date"] == last)]
                   ["unit_nav"].iloc[0])
        aum[code] = sh * nv
    per_fund = (contrib.groupby("fund_code")["contribution_yuan"].sum().reset_index())
    per_fund["aum_yuan"] = per_fund["fund_code"].map(aum)
    per_fund["flow_pct_of_aum"] = per_fund["contribution_yuan"] / per_fund["aum_yuan"] * 100
    per_fund = per_fund.sort_values("flow_pct_of_aum", ascending=False)
    per_fund.to_csv(os.path.join(OUT, "window_flow_vs_aum.csv"), index=False, encoding="utf-8-sig")
    print(per_fund.round(2).to_string(index=False))
    return per_fund


def main():
    shares, nav, hold, _ = V.load_inputs()
    p1 = part1_internal(shares)
    p2 = part2_gmbd_vs_sse()
    p3 = part3_aum_check()
    p4 = part4_weight_check()
    p5 = part5_window_flow()
    with open(os.path.join(OUT, "reconciliation_summary.txt"), "w", encoding="utf-8") as fh:
        for name, df in [("1_internal_shares", p1), ("2_gmbd_vs_sse", p2),
                         ("3_aum_check", p3), ("4_index_vs_report", p4),
                         ("5_window_flow_vs_aum", p5)]:
            fh.write(f"== {name} ==\n{df.to_string(index=False)}\n\n")
    print("\n[OK] reconciliation outputs ->", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
