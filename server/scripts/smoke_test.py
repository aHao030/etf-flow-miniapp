# -*- coding: utf-8 -*-
"""M2 自测：本地起服务后按《docs/m2-api-契约.md》v1.0 逐接口验证。

用法（先启动服务，如 python main.py --no-scheduler）:
  python scripts/smoke_test.py [base_url]
退出码：0=全部通过，1=存在失败。
"""
from __future__ import annotations

import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def get(path: str, params: dict | None = None, expect_http: int = 200):
    r = requests.get(BASE + path, params=params, timeout=30)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return r.status_code, j


def main() -> None:
    print(f"== M2 smoke test @ {BASE} ==")
    # meta
    sc, j = get("/api/v1/meta")
    check("meta http200", sc == 200, f"http={sc}")
    check("meta code=0", j.get("code") == 0, str(j))
    check("meta latest_date", bool(j.get("data", {}).get("latest_date")), str(j))
    check("meta disclosure", bool(j.get("data", {}).get("disclosure", {}).get("short")))
    check("meta disclosure.disclaimer(S1)", bool(
        j.get("data", {}).get("disclosure", {}).get("disclaimer")), str(j)[:200])
    check("meta szse_accumulating", isinstance(j.get("data", {}).get("szse_accumulating"), bool))
    latest = j.get("data", {}).get("latest_date")
    # health
    sc, j = get("/api/v1/health")
    check("health http200", sc == 200, f"http={sc}")
    check("health code=0", j.get("code") == 0, str(j))
    check("health pipeline 5 dates",
          len(j.get("data", {}).get("pipeline", {})) == 5, str(j))
    check("health latest_date", j.get("data", {}).get("latest_date") == latest)
    check("health szse_share_date(D4)", bool(
        j.get("data", {}).get("pipeline", {}).get("szse_share_date")), str(j)[:300])
    # rank（默认 / 5d / 20d / 行业 / 分页 / 参数校验）
    sc, j = get("/api/v1/rank")
    check("rank http200", sc == 200, f"http={sc}")
    items = j.get("data", {}).get("items", [])
    check("rank items 非空", len(items) > 0, str(j)[:200])
    check("rank trade_date", j.get("data", {}).get("trade_date") == latest)
    if items:
        check("rank item 字段", all(k in items[0] for k in
              ("rank", "stock_code", "stock_name", "flow", "intensity_bp",
               "valid_days", "flags")), str(items[0]))
        check("rank 降序", all(items[i]["flow"] >= items[i + 1]["flow"]
              for i in range(min(len(items), 5) - 1)), str(items[:3]))
    sc, j = get("/api/v1/rank", {"period": "5d"})
    check("rank 5d", sc == 200 and j.get("code") == 0 and len(j["data"]["items"]) > 0)
    sc, j = get("/api/v1/rank", {"period": "20d"})
    check("rank 20d", sc == 200 and j.get("code") == 0 and len(j["data"]["items"]) > 0)
    sc, j = get("/api/v1/rank", {"period": "bad"})
    check("rank 非法 period→40002", sc == 400 and j.get("code") == 40002, f"{sc} {j}")
    sc, j = get("/api/v1/rank", {"direction": "out"})
    check("rank out→40002（预留）", j.get("code") == 40002, f"{sc} {j}")
    sc, j = get("/api/v1/rank", {"page_size": 999})
    check("rank 分页越界→40003", sc == 400 and j.get("code") == 40003, f"{sc} {j}")
    sc, j = get("/api/v1/rank", {"trade_date": "1999-01-01"})
    check("rank 无数据日→40404", sc == 404 and j.get("code") == 40404, f"{sc} {j}")
    sc, j = get("/api/v1/rank", {"page": "abc"})
    check("rank page=abc→400/40001 信封(D1)", sc == 400 and j.get("code") == 40001
          and j.get("data") is None, f"{sc} {j}")
    sc, j = get("/api/v1/stock/688981", {"trade_date": "not-a-date"})
    check("stock trade_date 非法→40001 信封(D1)", sc == 400 and j.get("code") == 40001,
          f"{sc} {j}")
    # 行业
    sc, j = get("/api/v1/industries")
    inds = j.get("data", {}).get("items", [])
    check("industries code=0 且有行业", sc == 200 and j.get("code") == 0 and len(inds) > 0,
          str(j)[:200])
    check("industries version", bool(j.get("data", {}).get("version")), str(j)[:200])
    if inds:
        ind_code = inds[0]["industry_code"]
        sc, j = get("/api/v1/rank", {"industry": ind_code})
        check("rank 行业筛选", sc == 200 and j.get("code") == 0
              and len(j["data"]["items"]) > 0, f"{sc} {j}")
        sc, j = get("/api/v1/rank", {"industry": "999999"})
        check("rank 非法行业→40403", sc == 404 and j.get("code") == 40403, f"{sc} {j}")
    # 个股详情
    stock = items[0]["stock_code"] if items else "688981"
    sc, j = get(f"/api/v1/stock/{stock}")
    d = j.get("data", {})
    check("stock http200 code=0", sc == 200 and j.get("code") == 0, f"{sc} {j}")
    check("stock trade_date", d.get("trade_date") == latest)
    check("stock series 非空", len(d.get("series", [])) > 0, str(d)[:200])
    check("stock top_etfs ≤5", len(d.get("top_etfs", [])) <= 5)
    check("stock quality 字段", all(k in d.get("quality", {}) for k in
          ("weight_source", "coverage", "flags", "note")), str(d.get("quality")))
    check("stock quality weight_source 契约枚举",
          d.get("quality", {}).get("weight_source") in
          ("index", "quarterly", "none", None), str(d.get("quality")))
    cov = d.get("quality", {}).get("coverage")
    check("stock coverage 0-1 口径",
          cov is None or (isinstance(cov, (int, float)) and 0 < cov <= 1), str(cov))
    if d.get("top_etfs"):
        check("top_etfs weight_source 契约枚举",
              d["top_etfs"][0]["weight_source"] in ("index", "quarterly", "none"),
              str(d["top_etfs"][0]))
    check("stock valid_days", all(k in d.get("valid_days", {}) for k in ("d1", "d5", "d20")))
    check("stock data_status 枚举", d.get("data_status") in
          ("ok", "sse_only", "accumulating", "no_data"), str(d.get("data_status")))
    sc, j = get("/api/v1/stock/999999")
    check("stock 不存在→40401", sc == 404 and j.get("code") == 40401, f"{sc} {j}")
    # ETF 详情
    sc, j = get("/api/v1/etf/510300")
    e = j.get("data", {})
    check("etf http200 code=0", sc == 200 and j.get("code") == 0, f"{sc} {j}")
    check("etf trade_date", e.get("trade_date") == latest)
    check("etf est_flow/top_stocks", e.get("est_flow") is not None
          and isinstance(e.get("top_stocks"), list), str(e)[:200])
    check("etf weight_source 契约枚举", e.get("weight_source") in
          ("index", "quarterly", "none"), str(e.get("weight_source")))
    check("etf coverage 0-1 口径", e.get("coverage") is None
          or 0 < e.get("coverage") <= 1, str(e.get("coverage")))
    check("etf share_series(v1.1)", isinstance(e.get("share_series"), list)
          and len(e["share_series"]) > 0
          and all(k in e["share_series"][0] for k in
                  ("trade_date", "share_total", "share_change")), str(e)[:200])
    check("etf scale_series(v1.1)", isinstance(e.get("scale_series"), list)
          and len(e["scale_series"]) > 0
          and all(k in e["scale_series"][0] for k in ("trade_date", "scale")),
          str(e)[:200])
    sc, j = get("/api/v1/etf/159915")
    check("etf 深市 accumulating", j.get("data", {}).get("exchange") == "SZSE", f"{sc} {j}")
    sc, j = get("/api/v1/etf/000000")
    check("etf 不存在→40402", sc == 404 and j.get("code") == 40402, f"{sc} {j}")
    # ETF 覆盖清单
    sc, j = get("/api/v1/etfs")
    check("etfs code=0 18 只", sc == 200 and j.get("code") == 0
          and j["data"]["total"] >= 13, f"{sc} {j}")
    if j.get("data", {}).get("items"):
        ws = j["data"]["items"][0].get("weight_source")
        check("etfs weight_source 契约枚举", ws in ("index", "quarterly", "none"), str(ws))
    sc, j = get("/api/v1/etfs", {"category": "宽基"})
    check("etfs 分类筛选", sc == 200 and j.get("code") == 0 and j["data"]["total"] > 0)
    # watchlist 冻结桩
    sc, j = get("/api/v1/watchlist")
    check("watchlist→50302（冻结桩）", sc == 200 and j.get("code") == 50302, f"{sc} {j}")
    print(f"\n== 结果: {PASS} PASS / {FAIL} FAIL ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
