# -*- coding: utf-8 -*-
"""
M1 数据验证 POC：A股ETF资金流入个股监控 — 数据源实测与样例落盘
====================================================================

实测并拉取三块数据的免费可用路径（2026-10-30 实测）：

  ① ETF 每日份额变化
     - 沪市 ETF：上交所官方接口 query.sse.com.cn/commonQuery.do
       sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L，支持 STAT_DATE 历史回溯，
       单位：万份。实测 2026-10-29 数据与东财 push2 f84（总份额）完全一致。
     - 深市 ETF：深交所官方 fund.szse.cn/api/report/ShowReport（xlsx，CATALOGID=1000_lf），
       仅最新 T-1 快照（无历史日期参数）→ 深市份额历史需每日落库积累（已记录为缺口）。
  ② ETF 持仓与权重（季报）
     - 天天基金 F10 FundArchivesDatas.aspx?type=jjcc（需 Referer 头），
       每季度最多前 100 大持仓：股票代码/名称/占净值比例/持股数(万股)/持仓市值(万元)。
       行业主题 ETF 成分股 ≤100 只 → 覆盖完整；宽基 300/创业板指等前 100 不完整（文档记录）。
  ③ 个股日行情与流通市值
     - 行情：腾讯财经 web.ifzq.gtimg.cn（日 K，qfq 前复权），无需 token。
     - 流通市值：腾讯 qt.gtimg.cn 快照（总市值/流通市值，亿元），当日口径。
       历史流通市值序列免费渠道缺失（东财 kline 不含市值字段）→ V1 建议用当日快照。

依赖：仅 Python 标准库 + openpyxl + lxml（工作区自带）。不依赖 akshare/token。
运行：python poc_fetch_data.py   （输出到 ./samples/）
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

try:
    from lxml import html as lxml_html
    HAS_LXML = True
except ImportError:  # pragma: no cover
    HAS_LXML = False
try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:  # pragma: no cover
    HAS_OPENPYXL = False

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "samples")
os.makedirs(OUT, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ----------------------------- 配置 -----------------------------
# 沪市样本 ETF（宽基 + 行业主题），走 SSE 官方历史份额接口
SSE_ETFS = {
    "510300": "沪深300ETF华泰柏瑞", "510050": "上证50ETF华夏",
    "510500": "中证500ETF南方", "512100": "中证1000ETF南方",
    "588000": "科创50ETF华夏",
    "512880": "证券ETF国泰", "512480": "半导体ETF国联安",
    "512760": "芯片ETF国泰", "515790": "光伏ETF华泰柏瑞",
    "512660": "军工ETF国泰", "512690": "酒ETF鹏华", "512170": "医疗ETF华宝",
}
# 深市样本 ETF，走 SZSE 官方 xlsx（仅最新快照）
SZSE_ETFS = {
    "159915": "创业板ETF易方达", "159949": "创业板50ETF华安",
    "159928": "消费ETF汇添富", "159901": "深证100ETF易方达",
    "159922": "中证500ETF嘉实",
}
# 份额历史回溯区间（工作日逐日请求，非交易日接口返回空）
HIST_FROM = date(2026, 9, 21)
HIST_TO = date(2026, 10, 29)   # 实测时点 SSE 最新可查日期为 T-1
# 个股样例（来自样本 ETF 前十大重仓，覆盖沪/深/创业板/科创板）
STOCKS = {
    "sh600519": "贵州茅台", "sz300750": "宁德时代", "sz300308": "中际旭创",
    "sh601318": "中国平安", "sh600036": "招商银行", "sh601899": "紫金矿业",
    "sz000333": "美的集团", "sh600900": "长江电力", "sz300059": "东方财富",
    "sh600030": "中信证券", "sh601211": "国泰海通", "sz002594": "比亚迪",
    "sh688981": "中芯国际", "sz300502": "新易盛", "sh601012": "隆基绿能",
}
NAV_DAYS = 25  # 净值回看天数
KL_DAYS = 30   # 个股日K回看根数

# ----------------------------- 工具 -----------------------------
def http_get(url, headers=None, timeout=40, decode="utf-8"):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(decode, "replace")


def write_csv(name, rows, fieldnames):
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  [CSV] {name}: {len(rows)} rows -> {path}")
    return path


def trading_weekdays(d0, d1):
    out, cur = [], d0
    while cur <= d1:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


# ----------------------- ① ETF 每日份额 --------------------------
def fetch_sse_etf_scale(stat_date):
    """上交所官方 ETF 份额（万份），支持历史日期。"""
    params = {
        "isPagination": "true", "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
        "STAT_DATE": stat_date,
    }
    url = "https://query.sse.com.cn/commonQuery.do?" + urllib.parse.urlencode(params)
    headers = {"Referer": "https://www.sse.com.cn/", "User-Agent": UA}
    text = http_get(url, headers)
    rows = json.loads(text).get("result") or []
    return rows  # [{STAT_DATE, ETF_TYPE, SEC_CODE, SEC_NAME, TOT_VOL(万份)}]


def fetch_szse_fund_scale():
    """深交所官方基金列表 xlsx（仅最新 T-1 快照）。"""
    if not HAS_OPENPYXL:
        print("  [WARN] openpyxl 不可用，跳过深交所份额")
        return []
    url = ("https://fund.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx"
           "&CATALOGID=1000_lf&TABKEY=tab1&random=0.123")
    headers = {"Referer": "https://fund.szse.cn/marketdata/fundslist/index.html",
               "User-Agent": UA}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        content = resp.read()
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = [str(h).strip() for h in next(it)]
    out = []
    for r in it:
        if r[0] is None:
            continue
        out.append(dict(zip(header, r)))
    return out


def part1():
    print("== ① ETF 每日份额变化 ==")
    # SSE 历史序列（逐日）
    days = trading_weekdays(HIST_FROM, HIST_TO)
    sse_rows, a500_map = [], {}  # a500_map: code -> {name, max_vol}
    for d in days:
        try:
            rows = fetch_sse_etf_scale(d)
        except Exception as e:  # noqa: BLE001
            print(f"  [SKIP] SSE {d}: {type(e).__name__} {e}")
            continue
        if not rows:
            continue
        for r in rows:
            code, name, vol = r.get("SEC_CODE"), r.get("SEC_NAME"), r.get("TOT_VOL")
            if code in SSE_ETFS:
                sse_rows.append({
                    "stat_date": r.get("STAT_DATE"), "market": "SH",
                    "fund_code": code, "fund_name": name,
                    "shares_wan": vol,
                    "shares": float(vol) * 10000 if vol else None,
                })
            if name and "A500" in name and "增" not in name and "红" not in name:
                prev = a500_map.setdefault(code, {"name": name, "max_vol": -1.0})
                try:
                    prev["max_vol"] = max(prev["max_vol"], float(vol))
                except (TypeError, ValueError):
                    pass
        time.sleep(0.25)
    a500_code = max(a500_map, key=lambda c: a500_map[c]["max_vol"]) if a500_map else None
    if a500_code:
        print(f"  [INFO] 上交所列表中发现中证A500 ETF（按份额取最大）: {a500_code} "
              f"{a500_map[a500_code]['name']}，已并入样例（同接口可回溯）")
        for d in days:
            try:
                rows = fetch_sse_etf_scale(d)
            except Exception:  # noqa: BLE001
                continue
            for r in rows or []:
                if r.get("SEC_CODE") == a500_code:
                    vol = r.get("TOT_VOL")
                    sse_rows.append({
                        "stat_date": r.get("STAT_DATE"), "market": "SH",
                        "fund_code": a500_code, "fund_name": r.get("SEC_NAME"),
                        "shares_wan": vol,
                        "shares": float(vol) * 10000 if vol else None,
                    })
            time.sleep(0.2)
    n_codes = len({r["fund_code"] for r in sse_rows})
    print(f"  SSE 历史份额：{len(sse_rows)} 行（{n_codes} 只 ETF × 最多 {len(days)} 个工作日）")
    write_csv("etf_shares_sse_history.csv", sse_rows,
              ["stat_date", "market", "fund_code", "fund_name", "shares_wan", "shares"])
    # SZSE 最新快照
    szse_rows = []
    try:
        for r in fetch_szse_fund_scale():
            code = str(r.get("基金代码", "")).strip()
            if code in SZSE_ETFS:
                szse_rows.append({
                    "market": "SZ", "fund_code": code,
                    "fund_name": str(r.get("基金简称", "")).strip(),
                    "shares": r.get("当前规模(份)"),
                    "nav": r.get("净值"),
                    "listed_date": r.get("上市日期"),
                })
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] SZSE xlsx 获取失败: {type(e).__name__} {e}")
    write_csv("etf_shares_szse_latest.csv", szse_rows,
              ["market", "fund_code", "fund_name", "shares", "nav", "listed_date"])
    return {a500_code: a500_map[a500_code]["name"]} if a500_code else {}


# ----------------------- ② ETF 持仓与权重 -------------------------
def parse_holdings_html(html):
    """解析 F10 jjcc 返回的 HTML 片段（lxml.html）。"""
    if not HAS_LXML:
        return []
    doc = lxml_html.fromstring(html)
    out = []
    labels = doc.xpath("//h4[contains(@class,'t')]")
    boxes = doc.xpath("//div[contains(@class,'boxitem')]")
    if len(labels) != len(boxes):
        return out
    for lab, box in zip(labels, boxes):
        m = re.search(r"(\d{4})年(\d)季度", lab.text_content())
        if not m:
            continue
        quarter = f"{m.group(1)}Q{m.group(2)}"
        for tr in box.xpath(".//tbody/tr"):
            tds = tr.xpath("./td")
            if len(tds) < 7:
                continue
            code_a = tds[1].xpath(".//a/text()")
            name_a = tds[2].xpath(".//a/text()")
            if not code_a:
                continue
            tail = [t.text_content().strip() for t in tds[-3:]]  # 占净值比例/持股数/持仓市值
            out.append({
                "fund_code": "", "fund_name": "", "quarter": quarter,
                "stock_code": code_a[0], "stock_name": name_a[0] if name_a else "",
                "pct_nav": tail[0].rstrip("%"), "shares_wan": tail[1],
                "mktcap_wan": tail[2],
            })
    return out


def fetch_em_holdings(fund_code):
    url = ("https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
           f"?type=jjcc&code={fund_code}&topline=100&year=2026&month=&rt={time.time():.6f}")
    headers = {"Referer": f"https://fundf10.eastmoney.com/jjcc_{fund_code}.html",
               "User-Agent": UA, "X-Requested-With": "XMLHttpRequest"}
    text = http_get(url, headers, decode="utf-8")  # fundf10 返回 UTF-8
    m = re.search(r"content:\"(.*)\",arryear", text, re.S)
    if not m:
        return []
    content = m.group(1).replace('\\"', '"').replace("\\/", "/")
    return parse_holdings_html(content)


def part2(extra=None):
    print("== ② ETF 持仓与权重（季报，天天基金 F10）==")
    all_rows, all_quarters = [], []
    etf_map = {**SSE_ETFS, **SZSE_ETFS, **(extra or {})}
    for code, name in etf_map.items():
        try:
            rows = fetch_em_holdings(code)
        except Exception as e:  # noqa: BLE001
            print(f"  [SKIP] 持仓 {code}: {type(e).__name__} {e}")
            continue
        if not rows:
            print(f"  [SKIP] 持仓 {code}: 无数据")
            continue
        latest_q = max(rows, key=lambda r: r["quarter"])["quarter"]
        latest = [r for r in rows if r["quarter"] == latest_q]
        for r in rows:
            r["fund_code"], r["fund_name"] = code, name
            if r["pct_nav"] in ("", "---", "-", None):
                r["pct_nav"] = ""
            all_quarters.append(dict(r))

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        pct_sum = sum(_num(r["pct_nav"]) for r in latest)
        flag = "" if pct_sum >= 60 else "（覆盖率偏低，Q3 披露数据尚不完整）"
        print(f"  {code} {name}: 最新季度 {latest_q}，{len(latest)} 只重仓股，占净值合计 {pct_sum:.1f}%{flag}")
        all_rows.extend(latest)
        time.sleep(0.4)
    write_csv("etf_holdings_latest_quarter.csv", all_rows,
              ["fund_code", "fund_name", "quarter", "stock_code", "stock_name",
               "pct_nav", "shares_wan", "mktcap_wan"])
    write_csv("etf_holdings_all_quarters.csv", all_quarters,
              ["fund_code", "fund_name", "quarter", "stock_code", "stock_name",
               "pct_nav", "shares_wan", "mktcap_wan"])
    return all_rows


# ----------------------- ③ 个股行情与流通市值 ----------------------
def fetch_tx_kline(symbol, count=KL_DAYS):
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={symbol},day,,,{count},qfq")
    text = http_get(url)
    j = json.loads(text)
    node = j.get("data", {}).get(symbol, {})
    return node.get("qfqday") or node.get("day") or []


def fetch_tx_snapshot(symbols):
    url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
    text = http_get(url, decode="gbk")
    out = []
    for line in text.strip().split(";"):
        if "=" not in line or "~" not in line:
            continue
        _, payload = line.split("=", 1)
        f = payload.strip('"').split("~")
        if len(f) < 46:
            continue
        try:
            out.append({
                "symbol": f[2], "name": f[1], "price": f[3],
                # 腾讯 qt 快照：idx44=流通市值(亿)，idx45=总市值(亿)（用中国平安验证）
                "total_mv_yi": f[45], "float_mv_yi": f[44],
            })
        except IndexError:
            continue
    return out


def part3():
    print("== ③ 个股日行情与流通市值（腾讯财经）==")
    kline_rows = []
    for sym, name in STOCKS.items():
        try:
            for k in fetch_tx_kline(sym):
                kline_rows.append({
                    "symbol": sym, "name": name, "date": k[0],
                    "open": k[1], "close": k[2], "high": k[3],
                    "low": k[4], "volume": k[5],
                })
        except Exception as e:  # noqa: BLE001
            print(f"  [SKIP] K线 {sym}: {type(e).__name__} {e}")
        time.sleep(0.3)
    write_csv("stock_quotes_daily.csv", kline_rows,
              ["symbol", "name", "date", "open", "close", "high", "low", "volume"])
    snap = fetch_tx_snapshot(list(STOCKS.keys()))
    write_csv("stock_floatcap_snapshot.csv", snap,
              ["symbol", "name", "price", "total_mv_yi", "float_mv_yi"])


# ----------------------- 附：ETF 单位净值 -------------------------
def part_nav(extra=None):
    print("== 附：ETF 单位净值（天天基金 lsjz）==")
    rows = []
    etf_map = {**SSE_ETFS, **SZSE_ETFS, **(extra or {})}
    for code, name in etf_map.items():
        try:
            url = (f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}"
                   f"&pageIndex=1&pageSize={NAV_DAYS}")
            text = http_get(url, {"Referer": "https://fundf10.eastmoney.com/",
                                  "User-Agent": UA})
            j = json.loads(text)
            for it in (j.get("Data") or {}).get("LSJZList") or []:
                rows.append({
                    "fund_code": code, "fund_name": name,
                    "nav_date": it.get("FSRQ"), "unit_nav": it.get("DWJZ"),
                    "acc_nav": it.get("LJJZ"), "nav_pct": it.get("JZZZL"),
                })
        except Exception as e:  # noqa: BLE001
            print(f"  [SKIP] 净值 {code}: {type(e).__name__} {e}")
        time.sleep(0.3)
    write_csv("etf_nav_history.csv", rows,
              ["fund_code", "fund_name", "nav_date", "unit_nav", "acc_nav", "nav_pct"])


def main():
    t0 = time.time()
    a500_info = part1()
    part2(a500_info)
    part3()
    part_nav(a500_info)
    print(f"\n全部完成，用时 {time.time()-t0:.0f}s，样例 CSV 输出目录：{OUT}")


if __name__ == "__main__":
    sys.exit(main())
