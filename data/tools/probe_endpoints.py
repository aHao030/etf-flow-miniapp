# -*- coding: utf-8 -*-
"""
M1 数据源探针：用 stdlib urllib 实测各免费数据源端点（不依赖 akshare/requests）。
运行：python probe_endpoints.py
输出：控制台打印各端点 HTTP 状态与前 500 字符；成功结果落盘 data/probe_results/。
"""
import json
import os
import time
import urllib.request

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_results")
os.makedirs(OUT_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

ENDPOINTS = [
    # --- ① ETF 份额（上交所官方，需要 Referer）---
    {
        "name": "SSE_ETF_share_latest",
        "url": "http://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_ZQPZ_ETFLB_L_NEW&etfClass=1",
        "headers": {"Referer": "http://www.sse.com.cn/", "User-Agent": UA},
    },
    {
        "name": "SSE_ETF_share_product",
        "url": "http://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_ZQPZ_ETFLB_L_NEW&PRODUCTID=510300",
        "headers": {"Referer": "http://www.sse.com.cn/", "User-Agent": UA},
    },
    # --- ① ETF 份额（腾讯财经 K 线，试探是否含份额字段）---
    {
        "name": "Tencent_fund_kline",
        "url": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh510300,day,,,5,qfq",
        "headers": {"User-Agent": UA},
    },
    # --- ② 持仓：东财 fundf10（带 Referer 试探）---
    {
        "name": "EM_fundf10_holdings",
        "url": ("https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc"
                "&code=510300&topline=20&year=&month=&rt=0.123"),
        "headers": {"Referer": "https://fundf10.eastmoney.com/jjcc_510300.html", "User-Agent": UA},
    },
    {
        "name": "EM_fundf10_scale",
        "url": ("https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd"
                "&code=510300&topline=10&year=&month=&rt=0.123"),
        "headers": {"Referer": "https://fundf10.eastmoney.com/gmbd_510300.html", "User-Agent": UA},
    },
    # --- ② 持仓：东财移动端（已实测可用，验证 pageSize 能否超过 10）---
    {
        "name": "EM_mobile_holdings_p100",
        "url": ("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition"
                "?FCODE=510300&pageIndex=1&pageSize=100&deviceid=Wap&plat=Wap&product=EFund&version=2.0.0"),
        "headers": {"User-Agent": UA},
    },
    # --- ③ 个股日行情（东财 kline，含市值字段试探）---
    {
        "name": "EM_stock_kline_cap",
        "url": ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600519"
                "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116,f117"
                "&klt=101&fqt=1&end=20500101&lmt=5"),
        "headers": {"User-Agent": UA},
    },
    # --- ③ 个股快照（东财 push2，含流通市值 f117）---
    {
        "name": "EM_stock_snapshot",
        "url": ("https://push2.eastmoney.com/api/qt/stock/get?secid=1.600519"
                "&fields=f57,f58,f43,f84,f85,f116,f117"),
        "headers": {"User-Agent": UA},
    },
    # --- ③ 乐咕乐股 历史市值 ---
    {
        "name": "Legulegu_market_cap",
        "url": "https://legulegu.com/api/stock-data/market-cap?stockCode=600519&marketId=1",
        "headers": {"User-Agent": UA},
    },
    # --- 深交所 LOF/ETF 基金列表（翻页找 159xxx ETF）---
    {
        "name": "SZSE_fund_list_page2",
        "url": ("http://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON"
                "&CATALOGID=1945_LOF&TABKEY=tab1&txtDate=2026-10-30&PAGENO=2"),
        "headers": {"User-Agent": UA},
    },
]


def fetch(name, url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        status = e.code
    except Exception as e:  # noqa: BLE001
        print(f"[{name}] ERROR: {e}")
        return
    text = body.decode("utf-8", errors="replace")
    fn = os.path.join(OUT_DIR, f"{name}.txt")
    with open(fn, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[{name}] HTTP {status}, {len(text)} chars -> {fn}")
    print("   preview:", text[:200].replace("\n", " "))


def main():
    for ep in ENDPOINTS:
        fetch(ep["name"], ep["url"], ep["headers"])
        time.sleep(1.0)


if __name__ == "__main__":
    main()
