# -*- coding: utf-8 -*-
"""M4 阶段一 · API 契约边界用例（qa-compliance）。

覆盖（依据 docs/m2-api-契约.md v1.1 与 Lead 派发清单）：
  1. 错误码触发：40001 / 40002 / 40003 / 40401-40404 / 42901 / 50001；
  2. 50301 与 health 联动（临时空库实例 8010 端口实测）；
  3. watchlist 冻结桩 50302（GET/POST/DELETE）；
  4. 分页边界：page<1、page_size>100/0/负、越界页、has_more 一致性；
  5. trade_date 历史回看（prev_trade_date / 首日 / 未来日 / 非交易日）；
  6. 空值语义：有效天数 5日<3 / 20日<12 返回 null（非 0）；
  7. meta.disclosure 口径披露文案存在性；
  8. 附带探测：FastAPI 参数类型错误（422 信封偏离）、NaN 边界回归（920982）。

用法：
  python tests/api_contract_test.py [base_url]        # 默认 http://127.0.0.1:8000
前置：
  server/ 目录下 `python main.py --no-scheduler`（端口 8000）。
  50301 用例会自动在临时目录复制 server/model 代码并起 8010 空库实例，
  只读原代码、不改动工作区任何 server/ 文件。

退出码：0 = 全部 PASS；1 = 存在 FAIL 或 GAP（契约缺口）。
结果标签：[PASS] 通过 / [FAIL] 断言失败（已实现但行为不符）/ [GAP] 契约缺口（未实现或无法自然触发）。
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
TIMEOUT = 20
PASS = 0
FAIL = 0
GAP = 0
GAP_NOTES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail[:300]}")


def gap(name: str, note: str) -> None:
    global GAP
    GAP += 1
    GAP_NOTES.append(f"{name}: {note}")
    print(f"  [GAP ] {name}  {note[:200]}")


def req(method: str, path: str, **kw):
    r = requests.request(method, BASE + path, timeout=TIMEOUT, **kw)
    try:
        j = r.json()
    except ValueError:
        j = None
    return r.status_code, j


def get(path: str, **kw):
    return req("GET", path, **kw)


def expect_error(name: str, path: str, code: int, http: int, method: str = "GET") -> None:
    """契约信封 + 业务码 + HTTP 状态三要素断言。"""
    sc, j = req(method, path)
    ok = (sc == http and isinstance(j, dict)
          and j.get("code") == code and j.get("data") is None
          and isinstance(j.get("message"), str) and j["message"])
    check(f"{name} -> {code}(http {http})", ok,
          f"http={sc} body={str(j)[:200]}")


# ---------------- A. meta 与口径披露 ----------------
def test_meta(meta_j: dict) -> None:
    d = meta_j.get("data", {})
    check("A1 meta 信封 code=0", meta_j.get("code") == 0 and meta_j.get("message") == "ok")
    check("A2 latest_date 格式", bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d.get("latest_date")))))
    check("A3 prev_trade_date < latest_date",
          bool(d.get("prev_trade_date")) and d["prev_trade_date"] < d["latest_date"])
    dis = d.get("disclosure", {})
    short = str(dis.get("short", ""))
    long_ = str(dis.get("long", ""))
    check("A4 disclosure.short 非空且含口径+红线", "估算" in short and "不构成投资建议" in short)
    check("A5 disclosure.long 含测算方法/数据来源/红线",
          all(k in long_ for k in ("测算方法", "数据来源", "不构成投资建议")))
    check("A6 units/windows",
          d.get("units") == {"money": "元", "intensity": "bp"}
          and d.get("windows") == ["1d", "5d", "20d"])
    check("A7 szse_accumulating 为 bool", isinstance(d.get("szse_accumulating"), bool))
    check("A8 app_version 非空", bool(d.get("app_version")))


# ---------------- B. health ----------------
def test_health(meta_j: dict) -> dict:
    sc, j = get("/api/v1/health")
    d = j.get("data", {}) if j else {}
    check("B1 health 信封 code=0", sc == 200 and j.get("code") == 0)
    check("B2 status 枚举", d.get("status") in ("ok", "degraded"), str(d.get("status")))
    check("B3 latest_date 与 meta 一致", d.get("latest_date") == meta_j["data"]["latest_date"])
    pipe = d.get("pipeline", {})
    check("B4 pipeline 5 字段", set(pipe.keys()) == {
        "sse_share_date", "szse_share_date", "holdings_date", "computed_date", "cached_date"},
        str(pipe))
    check("B5 message 非空", bool(d.get("message")))
    check("B6 szse_accumulating 与 meta 一致",
          d.get("szse_accumulating") == meta_j["data"].get("szse_accumulating"))
    return d


# ---------------- C. rank 契约边界 ----------------
def test_rank(meta_j: dict, health: dict) -> dict:
    latest = meta_j["data"]["latest_date"]
    prev = meta_j["data"]["prev_trade_date"]
    sc, j = get("/api/v1/rank")
    d = j.get("data", {}) if j else {}
    items = d.get("items", [])
    check("C1 rank 默认 200 且有数据", sc == 200 and j.get("code") == 0 and len(items) > 0)
    check("C2 全覆盖池分页总数为正整数", isinstance(d.get("total"), int) and d["total"] > 0,
          str(d.get("total")))
    check("C3 trade_date=latest", d.get("trade_date") == latest, str(d.get("trade_date")))
    check("C4 按 flow 降序", all(items[i]["flow"] >= items[i + 1]["flow"]
          for i in range(len(items) - 1)), str(items[:3]))
    check("C5 字段完备", all(k in items[0] for k in
          ("rank", "stock_code", "stock_name", "flow", "intensity_bp", "valid_days", "flags")))
    check("C6 rank 从 1 连续", [i["rank"] for i in items[:5]] == [1, 2, 3, 4, 5])
    # 分页边界
    sc, j = get("/api/v1/rank", params={"page_size": 100})
    full = j["data"]["items"]
    total = j["data"]["total"]
    check("C7 page_size=100 合法且 has_more 正确",
          sc == 200 and len(full) == min(100, total) and j["data"]["has_more"] == (total > 100))
    sc, j = get("/api/v1/rank", params={"page": 2, "page_size": 60})
    d = j["data"]
    expect = max(0, min(60, total - 60))
    check("C8 第 2 页切片与 has_more", sc == 200 and len(d["items"]) == expect
          and d["has_more"] == (60 + 60 < total), f"got {len(d['items'])} exp {expect}")
    sc, j = get("/api/v1/rank", params={"page": total // 20 + 2, "page_size": 20})
    check("C9 越界页返回空 items 且 has_more=false",
          sc == 200 and j["data"]["items"] == [] and j["data"]["has_more"] is False)
    expect_error("C10 page=0", "/api/v1/rank?page=0", 40003, 400)
    expect_error("C11 page=-1", "/api/v1/rank?page=-1", 40003, 400)
    expect_error("C12 page_size=0", "/api/v1/rank?page_size=0", 40003, 400)
    expect_error("C13 page_size=-5", "/api/v1/rank?page_size=-5", 40003, 400)
    expect_error("C14 page_size=101", "/api/v1/rank?page_size=101", 40003, 400)
    # 422 信封偏离探测（契约 §3 40001 参数格式非法；FastAPI 类型校验应归一为契约信封）
    for name, q in (("C15a page=abc", "page=abc"), ("C15b page=1.5", "page=1.5"),
                    ("C15c page_size=abc", "page_size=abc")):
        sc, j = get("/api/v1/rank", params=dict(p.split("=") for p in q.split("&")))
        ok = sc == 400 and isinstance(j, dict) and j.get("code") == 40001 and j.get("data") is None
        check(f"{name} -> 契约信封 40001", ok,
              f"http={sc} body={str(j)[:160]}（422 裸 detail 即信封偏离）")
    # 枚举
    expect_error("C16 period=7d", "/api/v1/rank?period=7d", 40002, 400)
    expect_error("C17 period=1D（大小写）", "/api/v1/rank?period=1D", 40002, 400)
    expect_error("C18 period 空串", "/api/v1/rank?period=", 40002, 400)
    sc, j = get("/api/v1/rank", params={"direction": "out"})
    out = j.get("data", {}).get("items", [])
    check("C19 流出榜仅含负值并升序", sc == 200 and j.get("code") == 0
          and all(r["flow"] < 0 for r in out)
          and all(a["flow"] <= b["flow"] for a, b in zip(out, out[1:])))
    expect_error("C19b direction 非法", "/api/v1/rank?direction=bad", 40002, 400)
    # trade_date 边界
    expect_error("C20 trade_date 格式非法", "/api/v1/rank?trade_date=20261030", 40001, 400)
    expect_error("C21 trade_date 非日期", "/api/v1/rank?trade_date=abc", 40001, 400)
    expect_error("C22 未来日 2099", "/api/v1/rank?trade_date=2099-01-01", 40404, 404)
    expect_error("C23 周六 2026-10-31", "/api/v1/rank?trade_date=2026-10-31", 40404, 404)
    # 历史回看
    sc, j = get("/api/v1/rank", params={"trade_date": prev})
    check("C24 prev_trade_date 回看", sc == 200 and j["data"]["trade_date"] == prev
          and len(j["data"]["items"]) > 0, f"http={sc} {str(j)[:150]}")
    # valid_days 格式
    sc, j = get("/api/v1/rank", params={"period": "5d", "page_size": 5})
    ok5 = sc == 200 and all(re.fullmatch(r"\d+/5", i["valid_days"]) for i in j["data"]["items"])
    sc, j = get("/api/v1/rank", params={"period": "20d", "page_size": 5})
    ok20 = sc == 200 and all(re.fullmatch(r"\d+/20", i["valid_days"]) for i in j["data"]["items"])
    check("C25 5d/20d valid_days 格式", ok5 and ok20, "5d/20d 榜单 valid_days 非 n/5、n/20")
    check("C26 1d valid_days=1/1", all(i["valid_days"] == "1/1" for i in items[:5]))
    return {"items": full, "total": total, "latest": latest}


# ---------------- D. stock 契约边界与空值语义 ----------------
def test_stock(meta_j: dict, rank: dict) -> str:
    latest = rank["latest"]
    head = rank["items"][0]["stock_code"]
    sc, j = get(f"/api/v1/stock/{head}")
    d = j.get("data", {}) if j else {}
    check("D1 stock 200 code=0", sc == 200 and j.get("code") == 0, str(j)[:150])
    check("D2 trade_date=latest", d.get("trade_date") == latest)
    check("D3 valid_days 三键", set(d.get("valid_days", {})) == {"d1", "d5", "d20"})
    series = d.get("series", [])
    check("D4 series 升序且截至 td", len(series) > 0 and len(series) <= 60
          and series[-1]["trade_date"] == latest
          and all(series[i]["trade_date"] < series[i + 1]["trade_date"]
                  for i in range(len(series) - 1)))
    check("D5 top_etfs<=5 且字段完备", len(d.get("top_etfs", [])) <= 5 and all(
        set(e) >= {"etf_code", "etf_name", "flow", "share_change", "nav", "weight",
                   "weight_source", "weight_date"} for e in d.get("top_etfs", [])))
    check("D5b top_etfs weight_source 归一化枚举", all(
        e.get("weight_source") in ("index", "quarterly", "none")
        for e in d.get("top_etfs", [])))
    q = d.get("quality", {})
    check("D6 quality 四字段", set(q) ==
          {"weight_source", "coverage", "flags", "note"})
    check("D6b quality.weight_source 归一化枚举",
          q.get("weight_source") in ("index", "quarterly", "none"))
    check("D6c quality.coverage 0–1", q.get("coverage") is None
          or 0 <= q.get("coverage") <= 1, str(q.get("coverage")))
    check("D6d quality.flags ⊆ 契约枚举", set(q.get("flags", [])) <= {
        "split_adjusted", "large_flow", "listing_period", "delisting",
        "weight_missing", "nav_missing", "share_gap", "weight_stale",
        "cash_substitute", "suspended"}, str(q.get("flags")))
    check("D7 data_status 枚举", d.get("data_status") in ("ok", "sse_only", "accumulating", "no_data"))
    # 错误码
    expect_error("D8 不存在 -> 40401", "/api/v1/stock/999999", 40401, 404)
    expect_error("D9 非 6 位 -> 40001", "/api/v1/stock/abc", 40001, 400)
    expect_error("D10 5 位 -> 40001", "/api/v1/stock/6000", 40001, 400)
    expect_error("D11 7 位 -> 40001", "/api/v1/stock/6000281", 40001, 400)
    expect_error("D12 trade_date 无数据 -> 40404",
                 f"/api/v1/stock/{head}?trade_date=2099-01-01", 40404, 404)
    expect_error("D13 trade_date 格式非法 -> 40001",
                 f"/api/v1/stock/{head}?trade_date=20261030", 40001, 400)
    # 空值语义（有效天数不足 -> null，非 0）—— 用历史首日/第 3 日窗口
    sc, j = get("/api/v1/stock/600028", params={"trade_date": "2026-09-28"})
    d = j["data"]
    check("D14 首日：5日<3、20日<12 -> null",
          sc == 200 and d["flow_5d"] is None and d["flow_20d"] is None
          and d["valid_days"] == {"d1": "1/1", "d5": "1/5", "d20": "1/20"}
          and d["flow_1d"] is not None, str(j)[:200])
    sc, j = get("/api/v1/stock/600028", params={"trade_date": "2026-09-30"})
    d = j["data"]
    check("D15 第 3 日：5日=3 达标、20日<12 -> null",
          sc == 200 and d["flow_5d"] is not None and d["flow_20d"] is None
          and d["valid_days"] == {"d1": "1/1", "d5": "3/5", "d20": "3/20"}, str(j)[:200])
    # NaN 边界回归：920982 流通市值缺失，flow 正常、intensity 全 null、不得 500
    sc, j = get("/api/v1/stock/920982")
    d = j.get("data", {}) if j else {}
    check("D16 NaN 边界不触发 50001（intensity/float_mcap=null）",
          sc == 200 and j.get("code") == 0 and d.get("float_mcap") is None
          and d.get("intensity_1d_bp") is None and d.get("flow_1d") is not None,
          f"http={sc} {str(j)[:160]}")
    return head


# ---------------- E. etf 契约边界 ----------------
def test_etf(rank: dict) -> None:
    latest = rank["latest"]
    sc, j = get("/api/v1/etf/510300")
    d = j.get("data", {}) if j else {}
    check("E1 etf 200 code=0", sc == 200 and j.get("code") == 0)
    check("E2 trade_date=latest 且 exchange=SSE", d.get("trade_date") == latest
          and d.get("exchange") == "SSE")
    ss, scl = d.get("share_series", []), d.get("scale_series", [])
    check("E3 share/scale 序列（v1.1）", len(ss) > 0 and len(ss) == len(scl)
          and ss[-1]["trade_date"] == latest and ss[0]["share_change"] is None
          and all(k in ss[0] for k in ("trade_date", "share_total", "share_change"))
          and all(k in scl[0] for k in ("trade_date", "scale")))
    check("E4 top_stocks<=5", len(d.get("top_stocks", [])) <= 5)
    check("E4b weight_source/coverage 归一化",
          d.get("weight_source") in ("index", "quarterly", "none", None)
          and (d.get("coverage") is None or 0 <= d.get("coverage") <= 1),
          f"{d.get('weight_source')} {d.get('coverage')}")
    sc, j = get("/api/v1/etf/159915")
    check("E5 深市 exchange=SZSE", sc == 200 and j["data"]["exchange"] == "SZSE")
    expect_error("E6 不在覆盖清单 -> 40402", "/api/v1/etf/999999", 40402, 404)
    expect_error("E7 非 6 位 -> 40001", "/api/v1/etf/abc", 40001, 400)
    expect_error("E8 trade_date 无数据 -> 40404",
                 "/api/v1/etf/510300?trade_date=2099-01-01", 40404, 404)
    sc, j = get("/api/v1/etf/510300", params={"trade_date": "2026-09-28"})
    check("E9 历史回看", sc == 200 and j["data"]["trade_date"] == "2026-09-28")


# ---------------- F. industries ----------------
def test_industries(meta_j: dict) -> None:
    sc, j = get("/api/v1/industries")
    d = j.get("data", {}) if j else {}
    items = d.get("items", [])
    check("F1 industries 200 code=0 且有行业", sc == 200 and j.get("code") == 0 and len(items) > 0)
    check("F2 字段完备且 stock_count>0", all(
        set(i) == {"industry_code", "industry_name", "stock_count"} and i["stock_count"] > 0
        for i in items))
    names = [i["industry_name"] for i in items]
    check("F3 按行业名排序", names == sorted(names))
    check("F4 version 与 meta 一致",
          d.get("version") == meta_j["data"].get("industry_dict_version")
          and bool(d.get("updated_date")))


# ---------------- G. etfs 覆盖清单 ----------------
def test_etfs(meta_j: dict) -> None:
    latest = meta_j["data"]["latest_date"]
    sc, j = get("/api/v1/etfs", params={"page_size": 100})
    d = j.get("data", {}) if j else {}
    items = d.get("items", [])
    check("G1 etfs 200 code=0 total>=13", sc == 200 and j.get("code") == 0
          and d.get("total", 0) >= 13, f"http={sc} {str(j)[:150]}")
    check("G2 trade_date=latest", d.get("trade_date") == latest)
    check("G3 字段与枚举", all(
        set(i) == {"etf_code", "etf_name", "category", "exchange", "status",
                   "listing_date", "weight_source", "weight_date"}
        and i["category"] in ("宽基", "行业", "主题")
        and i["exchange"] in ("SSE", "SZSE")
        and i["status"] in ("active", "accumulating", "new") for i in items))
    check("G3b etfs weight_source 归一化", all(
        i["weight_source"] in ("index", "quarterly", "none", None) for i in items))
    sc, j = get("/api/v1/etfs", params={"category": "宽基", "page_size": 100})
    check("G4 category=宽基 全过滤", sc == 200 and all(
        i["category"] == "宽基" for i in j["data"]["items"]))
    expect_error("G5 category 非法 -> 40002", "/api/v1/etfs?category=xx", 40002, 400)
    expect_error("G6 page=0 -> 40003", "/api/v1/etfs?page=0", 40003, 400)
    expect_error("G7 page_size=101 -> 40003", "/api/v1/etfs?page_size=101", 40003, 400)


# ---------------- H. watchlist 冻结桩 50302 ----------------
def test_watchlist() -> None:
    for m in ("GET", "POST", "DELETE"):
        sc, j = req(m, "/api/v1/watchlist")
        ok = sc == 200 and isinstance(j, dict) and j.get("code") == 50302 \
            and j.get("data") is None and bool(j.get("message"))
        check(f"H1 watchlist {m} -> 50302 冻结桩", ok, f"http={sc} {str(j)[:150]}")


# ---------------- I. 50301 + health 联动（空库临时实例） ----------------
def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def test_50301_fresh_instance() -> None:
    port = next((p for p in (8010, 8011, 8012) if _port_free(p)), None)
    if port is None:
        gap("I0 空库实例", "8010-8012 端口均被占用，跳过 50301 实测")
        return
    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.dirname(here)
    tmp = tempfile.mkdtemp(prefix="dsh_etf_50301_")
    proc = None
    try:
        shutil.copytree(os.path.join(proj, "server"), os.path.join(tmp, "server"),
                        ignore=shutil.ignore_patterns("__pycache__", "data", "work"))
        shutil.copytree(os.path.join(proj, "model"), os.path.join(tmp, "model"),
                        ignore=shutil.ignore_patterns("__pycache__", "cache", "outputs"))
        proc = subprocess.Popen(
            [sys.executable, "main.py", "--no-scheduler", "--port", str(port)],
            cwd=os.path.join(tmp, "server"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base2 = f"http://127.0.0.1:{port}"

        def req2(path: str):
            """临时实例专用请求（不复用全局 BASE 拼接），返回 (status, json)。"""
            r = requests.get(base2 + path, timeout=TIMEOUT)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, None

        up = False
        for _ in range(30):
            try:
                r_sc, j = req2("/api/v1/health")
                if r_sc == 200 and j and j.get("code") == 0:
                    up = True
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        if not up:
            gap("I1 空库实例启动", "30s 内未就绪（可能端口冲突或依赖问题），跳过")
            return
        check("I2 空库 health -> degraded + 未就绪提示",
              j.get("data", {}).get("status") == "degraded"
              and j["data"].get("latest_date") is None
              and "未就绪" in str(j["data"].get("message")), str(j)[:200])
        sc, j = req2("/api/v1/meta")
        check("I3 空库 meta -> 503/50301", sc == 503 and j.get("code") == 50301
              and j.get("data") is None, f"http={sc} {str(j)[:150]}")
        sc, j = req2("/api/v1/rank")
        check("I4 空库 rank -> 503/50301", sc == 503 and j.get("code") == 50301,
              f"http={sc} {str(j)[:150]}")
        sc, j = req2("/api/v1/etfs")
        check("I5 空库 etfs -> 503/50301", sc == 503 and j.get("code") == 50301,
              f"http={sc} {str(j)[:150]}")
        sc, j = req2("/api/v1/stock/999999")
        check("I6 空库 stock 校验优先级：40401 先于 50301",
              sc == 404 and j.get("code") == 40401, f"http={sc} {str(j)[:150]}")
        sc, j = req2("/api/v1/industries")
        check("I7 空库 industries 200 空列表", sc == 200 and j.get("code") == 0
              and j["data"]["items"] == [])
        sc, j = req2("/api/v1/watchlist")
        check("I8 空库 watchlist 仍 50302", sc == 200 and j.get("code") == 50302)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- J/K. 42901 / 50001 ----------------
def test_rate_limit_and_500() -> None:
    saw_429 = False
    for _ in range(60):
        sc, j = get("/api/v1/health")
        if sc == 429 and (j or {}).get("code") == 42901:
            saw_429 = True
            break
    if saw_429:
        check("J1 42901 限流可触发", True)
    else:
        gap("J1 42901 限流", "60 连击未触发 42901；server 代码无 rate-limit 中间件（grep 无 429），"
            "契约 §3 已定义但未实现，建议 M4 补齐或由 Lead 裁决豁免")
    # 50001：异常处理器存在于 app.py:53-58（源码审查）；正常运行态无法自然触发
    gap("K1 50001 内部错误", "无自然触发手段（handler 已实现但缺少故障注入入口）；"
        "D16 已验证 NaN 边界不会误触发。建议：M4 增加 TEST 模式故障注入路由或单测 handler")


def main() -> None:
    print(f"== M4 API 契约边界用例 @ {BASE} ==")
    sc, meta_j = get("/api/v1/meta")
    if not (sc == 200 and meta_j.get("code") == 0):
        print(f"[FATAL] /api/v1/meta 不可用（http={sc}），服务是否已启动？{str(meta_j)[:200]}")
        sys.exit(2)
    test_meta(meta_j)
    health = test_health(meta_j)
    rank = test_rank(meta_j, health)
    test_stock(meta_j, rank)
    test_etf(rank)
    test_industries(meta_j)
    test_etfs(meta_j)
    test_watchlist()
    test_50301_fresh_instance()
    test_rate_limit_and_500()
    print(f"\n== 结果: {PASS} PASS / {FAIL} FAIL / {GAP} GAP ==")
    for n in GAP_NOTES:
        print(f"  * {n}")
    sys.exit(1 if (FAIL or GAP) else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
