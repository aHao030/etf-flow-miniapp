"""Collect verified stock listing dates from bulk exchange directories.

Only the independent stock_listings table is written. Missing/failed exchanges
remain unknown; neither quote history nor code/name heuristics infer IPO dates.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
SSE_PAGE = "https://www.sse.com.cn/assortment/stock/list/share/"
SSE_API = "https://query.sse.com.cn/sseQuery/commonQuery.do"
SZSE_PAGE = "https://www.szse.cn/market/product/stock/list/index.html"
SZSE_API = "https://www.szse.cn/api/report/ShowReport"


def validated_rows(records, code_key: str, date_key: str, source: str, url: str, name_key: str | None = None):
    """Require an explicit source date and six-digit source security identifier."""
    rows = {}
    rejected = 0
    for record in records:
        raw_code = str(record.get(code_key, "")).strip()
        # XLSX integer cells omit leading zeros; this is formatting, not identity inference.
        if re.fullmatch(r"\d{1,6}(?:\.0)?", raw_code):
            code = raw_code.split(".")[0].zfill(6)
        else:
            rejected += 1
            continue
        raw_date = record.get(date_key)
        parsed = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(parsed) or not date(1980, 1, 1) <= parsed.date() <= date.today():
            rejected += 1
            continue
        listed_date = parsed.date().isoformat()
        if code in rows and rows[code][1] != listed_date:
            raise ValueError(f"conflicting listing dates for {code}")
        stock_name = str(record.get(name_key, "")).strip() if name_key else None
        if name_key and (not stock_name or stock_name in ("None", "nan")):
            rejected += 1
            continue
        rows[code] = (code, listed_date, source, url, stock_name)
    return list(rows.values()), rejected


def collect_sse(session: requests.Session, board: str):
    params = {
        "STOCK_TYPE": board, "REG_PROVINCE": "", "CSRC_CODE": "", "STOCK_CODE": "",
        "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L", "COMPANY_STATUS": "2,4,5,7,8",
        "type": "inParams", "isPagination": "true", "pageHelp.cacheSize": "1",
        "pageHelp.beginPage": "1", "pageHelp.pageSize": "10000", "pageHelp.pageNo": "1",
        "pageHelp.endPage": "1",
    }
    response = session.get(SSE_API, params=params, headers={"Referer": SSE_PAGE}, timeout=(10, 35))
    response.raise_for_status()
    records = response.json().get("result")
    if not isinstance(records, list) or not records:
        raise ValueError("SSE directory has no result records")
    if not all("A_STOCK_CODE" in item and "LIST_DATE" in item and "SEC_NAME_CN" in item for item in records):
        raise ValueError("SSE directory missing verified identifier/date fields")
    return validated_rows(records, "A_STOCK_CODE", "LIST_DATE", "SSE official stock directory", SSE_PAGE, "SEC_NAME_CN")


def collect_szse(session: requests.Session):
    response = session.get(SZSE_API, params={"SHOWTYPE": "xlsx", "CATALOGID": "1110", "TABKEY": "tab1"}, timeout=(10, 35))
    response.raise_for_status()
    if not response.content.startswith(b"PK"):
        raise ValueError("SZSE response is not the documented XLSX directory")
    frame = pd.read_excel(BytesIO(response.content), dtype={"A股代码": str})
    if not {"A股代码", "A股上市日期", "A股简称"}.issubset(frame.columns):
        raise ValueError("SZSE directory missing verified identifier/date fields")
    return validated_rows(frame.to_dict("records"), "A股代码", "A股上市日期", "SZSE official A-share directory", SZSE_PAGE, "A股简称")


def run(db: Path):
    db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db, timeout=60)
    connection.execute("CREATE TABLE IF NOT EXISTS stock_listings(stock_code TEXT PRIMARY KEY, listed_date TEXT NOT NULL, source TEXT NOT NULL, source_url TEXT NOT NULL)")
    if 'stock_name' not in {r[1] for r in connection.execute('PRAGMA table_info(stock_listings)')}:
        connection.execute('ALTER TABLE stock_listings ADD COLUMN stock_name TEXT')
    connection.commit()
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "exchanges": [], "note": "Current exchange directories; missing dates, delisted identities and uncollected exchanges remain unverified."}
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    for name, function in [("SSE main board", lambda: collect_sse(session, "1")), ("SSE STAR", lambda: collect_sse(session, "8")), ("SZSE A-share", lambda: collect_szse(session))]:
        try:
            rows, rejected = function()
            if not rows:
                raise ValueError("no verified listing dates")
            # A conflict is retained as unknown pending review, never silently shifted.
            existing = dict(connection.execute("SELECT stock_code,listed_date FROM stock_listings"))
            conflicts = [row[0] for row in rows if row[0] in existing and existing[row[0]] != row[1]]
            safe_rows = [row for row in rows if row[0] not in conflicts]
            with connection:
                connection.executemany("INSERT INTO stock_listings(stock_code,listed_date,source,source_url,stock_name) VALUES(?,?,?,?,?) ON CONFLICT(stock_code) DO UPDATE SET source=excluded.source,source_url=excluded.source_url,stock_name=excluded.stock_name", safe_rows)
            entry = {"exchange": name, "status": "observed", "verified_rows": len(safe_rows), "rejected_rows": rejected, "conflicts": conflicts, "min_date": min(row[1] for row in rows), "max_date": max(row[1] for row in rows)}
        except Exception as exc:
            entry = {"exchange": name, "status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:300]}
        report["exchanges"].append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        time.sleep(0.5)
    report["total_verified"] = connection.execute("SELECT COUNT(*) FROM stock_listings").fetchone()[0]
    report["verified_with_name"] = connection.execute("SELECT COUNT(*) FROM stock_listings WHERE stock_name IS NOT NULL AND stock_name!=''").fetchone()[0]
    table_exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_weights'").fetchone()
    if table_exists:
        report["holdings_stock_count"] = connection.execute("SELECT COUNT(DISTINCT stock_code) FROM stock_weights").fetchone()[0]
        report["holdings_with_verified_listing"] = connection.execute("SELECT COUNT(DISTINCT w.stock_code) FROM stock_weights w JOIN stock_listings l USING(stock_code)").fetchone()[0]
    connection.close()
    session.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "stock_holdings.db")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.db)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total_verified": result["total_verified"], "report": str(args.report)}, ensure_ascii=False))
