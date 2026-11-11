# -*- coding: utf-8 -*-
"""上游采集函数（M2 ETL，backend-dev）。

来源与请求头要求来自《data/README.md》：
- 上交所份额：query.sse.com.cn，必须带 Referer: https://www.sse.com.cn/
- 深交所份额：fund.szse.cn xlsx（T-1 快照），必须带 Referer
- 净值/持仓：天天基金 lsjz / fundf10 jjcc（Referer + X-Requested-With）
- 个股行情/流通市值：腾讯 fqkline / qt 快照（无需 Referer）
- 东财 push2 高频会被 IP 限流 → 主链路完全不依赖 push2（仅保留说明）
全部带重试与指数退避；调用侧负责限速间隔（config.SLEEP_*）。
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.parse
import urllib.request

import config

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:  # pragma: no cover
    HAS_OPENPYXL = False

try:
    from lxml import html as lxml_html
    HAS_LXML = True
except ImportError:  # pragma: no cover
    HAS_LXML = False


class FetchError(RuntimeError):
    pass


def http_get(url: str, headers: dict | None = None, decode: str = "utf-8",
             timeout: int = config.HTTP_TIMEOUT,
             retries: int = config.FETCH_RETRIES) -> str:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers=headers or {"User-Agent": config.UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(decode, "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(config.FETCH_BACKOFF * (attempt + 1))
    raise FetchError(f"GET {url[:120]} 失败: {type(last).__name__} {last}")


def http_bytes(url: str, headers: dict | None = None,
               timeout: int = 60) -> bytes:
    last = None
    for attempt in range(config.FETCH_RETRIES):
        try:
            req = urllib.request.Request(
                url, headers=headers or {"User-Agent": config.UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < config.FETCH_RETRIES - 1:
                time.sleep(config.FETCH_BACKOFF * (attempt + 1))
    raise FetchError(f"GET(bytes) {url[:120]} 失败: {type(last).__name__} {last}")


# ---------------- ① ETF 份额 ----------------
def fetch_sse_etf_scale(stat_date: str) -> list[dict]:
    """上交所官方 ETF 份额（万份）。支持历史日期；非交易日返回空。"""
    params = {
        "isPagination": "true", "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
        "STAT_DATE": stat_date,
    }
    url = config.SSE_URL + "?" + urllib.parse.urlencode(params)
    text = http_get(url, headers=config.SSE_HEADERS)
    return json.loads(text).get("result") or []


def fetch_szse_fund_scale() -> list[dict]:
    """深交所官方基金列表 xlsx（仅最新 T-1 快照，无历史日期参数）。"""
    if not HAS_OPENPYXL:
        raise FetchError("openpyxl 不可用")
    content = http_bytes(config.SZSE_URL, headers=config.SZSE_HEADERS, timeout=60)
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


# ---------------- ② 净值 / 持仓（天天基金） ----------------
def fetch_lsjz_nav(fund_code: str, page_size: int = config.NAV_PAGE_SIZE) -> list[dict]:
    """基金单位净值历史（LSJZList: FSRQ/DWJZ/LJJZ/JZZZL）。"""
    url = (f"{config.LSJZ_URL}?fundCode={fund_code}&pageIndex=1&pageSize={page_size}")
    headers = {"Referer": "https://fundf10.eastmoney.com/", "User-Agent": config.UA}
    text = http_get(url, headers=headers)
    j = json.loads(text)
    out = []
    for it in (j.get("Data") or {}).get("LSJZList") or []:
        out.append({"nav_date": it.get("FSRQ"), "unit_nav": it.get("DWJZ"),
                    "acc_nav": it.get("LJJZ"), "nav_pct": it.get("JZZZL")})
    return out


def _parse_holdings_html(html: str) -> list[dict]:
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
            tail = [t.text_content().strip() for t in tds[-3:]]
            out.append({
                "quarter": quarter,
                "stock_code": code_a[0],
                "stock_name": name_a[0] if name_a else "",
                "pct_nav": tail[0].rstrip("%"),
                "shares_wan": tail[1],
                "mktcap_wan": tail[2],
            })
    return out


def fetch_em_holdings(fund_code: str) -> list[dict]:
    """天天基金 F10 季报持仓（每季最多前 100 大）。"""
    url = (f"{config.JJCC_URL}?type=jjcc&code={fund_code}&topline=100"
           f"&year=2026&month=&rt={time.time():.6f}")
    headers = {"Referer": f"https://fundf10.eastmoney.com/jjcc_{fund_code}.html",
               "User-Agent": config.UA, "X-Requested-With": "XMLHttpRequest"}
    text = http_get(url, headers=headers)
    m = re.search(r"content:\"(.*)\",arryear", text, re.S)
    if not m:
        return []
    content = m.group(1).replace('\\"', '"').replace("\\/", "/")
    return _parse_holdings_html(content)


# ---------------- ③ 个股行情 / 流通市值（腾讯，无 Referer 要求） ----------------
def fetch_tx_kline(symbol: str, count: int = config.KLINE_COUNT) -> list[list]:
    """腾讯日 K（前复权）：每行 [日期,开,收,高,低,量]；被软封时返回 501。"""
    url = f"{config.KLINE_URL}?param={symbol},day,,,{count},qfq"
    text = http_get(url, retries=1)
    j = json.loads(text)
    node = j.get("data", {}).get(symbol, {})
    return node.get("qfqday") or node.get("day") or []


def fetch_sina_kline(symbol: str, count: int = config.KLINE_COUNT) -> list[list]:
    """新浪日 K（备源，data/README：仅 OHLCV 但可用无 token）。
    返回与腾讯同构 [日期,开,收,高,低,量]。"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={count}")
    text = http_get(url, decode="gbk", retries=1)
    j = json.loads(text)
    return [[it["day"], it.get("open"), it.get("close"), it.get("high"),
             it.get("low"), it.get("volume")] for it in j]


def fetch_tx_snapshot(symbols: list[str]) -> list[dict]:
    """腾讯快照（GBK）。idx44=流通市值(亿)、idx45=总市值(亿)（data/README 已验证）。"""
    url = config.SNAPSHOT_URL + ",".join(symbols)
    text = http_get(url, decode="gbk")
    out = []
    for line in text.strip().split(";"):
        if "=" not in line or "~" not in line:
            continue
        payload = line.split("=", 1)[1].strip('"').strip()
        f = payload.split("~")
        if len(f) < 46:
            continue
        out.append({"symbol": f[2], "name": f[1], "price": f[3],
                    "total_mv_yi": f[45], "float_mv_yi": f[44]})
    return out


def market_prefix(stock_code: str) -> str | None:
    """6 位代码 → 腾讯市场前缀；北交所（92/4/8 开头）前缀 bj。"""
    if stock_code.startswith("92") or stock_code[:1] in ("4", "8"):
        return "bj"
    c = stock_code[:1]
    if c in ("5", "6", "9"):
        return "sh"
    if c in ("0", "3"):
        return "sz"
    return None


def tx_symbol(stock_code: str) -> str | None:
    p = market_prefix(stock_code)
    return f"{p}{stock_code}" if p else None
