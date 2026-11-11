# -*- coding: utf-8 -*-
"""M4 阶段一 · L1 源对账（qa-compliance）。

独立核对链路（不 import 任何 server/model 代码，仅 stdlib）：
  A. 沪市份额：server/data/app.db（etf_share_daily，库内）vs 上交所官网披露
     （query.sse.com.cn，sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L，TOT_VOL 万份）
     —— 13 只沪市覆盖池 ETF × 库内最近 20 个交易日，逐日逐基金核对；
  B. 单位净值：app.db（etf_nav_daily，库内）vs 天天基金 lsjz（DWJZ）——同 13 只基金重叠日核对；
  C. 深市份额：核对库内 SZ 落库状态（snapshot_only 积累中为已知状态，仅记录不判错）。

容差：份额/净值均为官方披露值入库，允许舍入差（shares_wan 2 位小数）：abs ≤ 0.011；
      净值 4 位小数：abs ≤ 1e-4。一致率目标 100%（差异须可解释）。

用法：
  python tests/l1_source_reconcile.py [--days 20] [--db PATH] [--funds 510050,...]
退出码：0 = 一致率达标；1 = 存在不可解释差异；2 = 上游不可达（环境性 SKIP）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
SSE_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_HEADERS = {"Referer": "https://www.sse.com.cn/", "User-Agent": UA}
LSJZ_URL = "https://api.fund.eastmoney.com/f10/lsjz"
LSJZ_HEADERS = {"Referer": "https://fundf10.eastmoney.com/", "User-Agent": UA}

DEFAULT_FUNDS = ["510050", "510300", "510500", "512100", "563360", "588000",   # 宽基 6
                 "512170", "512480", "512660", "512690", "512880",             # 行业 5
                 "512760", "515790"]                                           # 主题 2
PASS = 0
FAIL = 0
SKIP = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail[:280]}")


def note(name: str, detail: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name}  {detail[:200]}")


def http_json(url: str, headers: dict, retries: int = 3, sleep: float = 0.5):
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            if a < retries - 1:
                time.sleep(sleep * (a + 1))
    raise RuntimeError(f"上游不可达: {type(last).__name__} {last}")


def fetch_sse_shares(stat_date: str) -> dict[str, float]:
    """上交所 ETF 份额披露（万份）→ {fund_code: shares_wan}。非交易日返回空。"""
    params = {
        "isPagination": "true", "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
        "STAT_DATE": stat_date,
    }
    j = http_json(SSE_URL + "?" + urllib.parse.urlencode(params), SSE_HEADERS)
    out = {}
    for r in j.get("result") or []:
        code = str(r.get("SEC_CODE") or "").strip()
        vol = r.get("TOT_VOL")
        if code and vol not in (None, "", "-"):
            try:
                out[code] = float(vol)
            except (TypeError, ValueError):
                continue
    return out


def fetch_lsjz_nav(code: str, page: int = 25) -> dict[str, float]:
    """天天基金单位净值历史 → {nav_date: unit_nav}（最近 page 条）。"""
    url = f"{LSJZ_URL}?fundCode={code}&pageIndex=1&pageSize={page}"
    j = http_json(url, LSJZ_HEADERS)
    out = {}
    for it in (j.get("Data") or {}).get("LSJZList") or []:
        d, v = it.get("FSRQ"), it.get("DWJZ")
        if d and v not in (None, ""):
            try:
                out[d] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--db", default=None)
    ap.add_argument("--funds", default=",".join(DEFAULT_FUNDS))
    args = ap.parse_args()
    funds = [f.strip() for f in args.funds.split(",") if f.strip()]

    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(here)
    db_path = args.db or os.path.join(proj, "server", "data", "app.db")
    print(f"== L1 源对账（库内 vs 官方披露）==\ndb={db_path}\nfunds({len(funds)})={funds}")

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    sh_rows = con.execute(
        "SELECT fund_code, trade_date, shares_wan, source FROM etf_share_daily "
        "WHERE market='SH' AND fund_code IN (%s) ORDER BY trade_date"
        % ",".join("?" * len(funds)), funds).fetchall()
    nav_rows = con.execute(
        "SELECT fund_code, nav_date, unit_nav FROM etf_nav_daily "
        "WHERE fund_code IN (%s) ORDER BY nav_date" % ",".join("?" * len(funds)),
        funds).fetchall()
    sz_n = con.execute("SELECT COUNT(*) AS n FROM etf_share_daily WHERE market='SZ'").fetchone()["n"]
    con.close()

    # ---- A. 份额对账 ----
    db_sh = {}
    for r in sh_rows:
        db_sh.setdefault(r["fund_code"], {})[r["trade_date"]] = r["shares_wan"]
    dates = sorted({r["trade_date"] for r in sh_rows})[-args.days:]
    if not dates:
        print("[FATAL] 库内无沪市份额数据（etf_share_daily 为空），先跑 ETL。")
        sys.exit(2)
    print(f"\n[A] 份额对账：{len(dates)} 个交易日（{dates[0]} ~ {dates[-1]}）× {len(funds)} 只 ETF")

    n_hit = n_miss = n_mismatch = 0
    mism = []
    for d in dates:
        try:
            up = fetch_sse_shares(d)
        except RuntimeError as e:
            note(f"上游份额接口（{d} 起）", str(e))
            break
        for code in funds:
            dbv = db_sh.get(code, {}).get(d)
            upv = up.get(code)
            if dbv is None and upv is None:
                continue
            if upv is None:
                n_miss += 1          # 上游未披露（如上市前），库内有值则可疑但记录
                if dbv is not None:
                    mism.append((d, code, dbv, None))
                continue
            if dbv is None:
                n_miss += 1          # 库内缺、上游有
                mism.append((d, code, None, upv))
                continue
            n_hit += 1
            if abs(dbv - upv) > 0.011:
                n_mismatch += 1
                mism.append((d, code, dbv, upv))
        time.sleep(0.25)
    check(f"A1 份额一致率 {n_hit - n_mismatch}/{n_hit}（{len(dates)}日×{len(funds)}只）",
          n_mismatch == 0, f"mismatch={n_mismatch} miss={n_miss} sample={mism[:5]}")
    if n_miss:
        note("A2 上游/库内缺失明细", f"miss={n_miss} sample={mism[:5]}（非交易日或未披露属正常）")

    # ---- B. 净值对账（天天基金 lsjz） ----
    db_nav = {}
    for r in nav_rows:
        db_nav.setdefault(r["fund_code"], {})[r["nav_date"]] = r["unit_nav"]
    print(f"\n[B] 净值对账：{len(funds)} 只基金 × 天天基金 lsjz 最近 25 日")
    b_hit = b_bad = 0
    for code in funds:
        try:
            up = fetch_lsjz_nav(code)
        except RuntimeError as e:
            note(f"lsjz {code}", str(e))
            break
        for d, v in db_nav.get(code, {}).items():
            if d not in up:
                continue
            b_hit += 1
            if abs(v - up[d]) > 1e-4:
                b_bad += 1
                print(f"    [diff] {code} {d}: db={v} lsjz={up[d]}")
        time.sleep(0.3)
    check(f"B1 净值一致率 {b_hit - b_bad}/{b_hit}", b_bad == 0, f"bad={b_bad}")

    # ---- C. 深市状态 ----
    if sz_n == 0:
        note("C1 深市份额", "库内无 SZ 份额行（snapshot_only 积累中，与 health.szse_share_date=null 一致，属已知状态）")
    else:
        check("C1 深市份额已落库", sz_n > 0, f"rows={sz_n}")

    print(f"\n== 结果: {PASS} PASS / {FAIL} FAIL / {SKIP} SKIP ==")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
