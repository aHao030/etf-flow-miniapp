# -*- coding: utf-8 -*-
"""M2 REST API（FastAPI，backend-dev）。

严格实现《docs/m2-api-契约.md》v1.0（已冻结）：路径 /api/v1/*、金额单位「元」、
强度 bp、每个数据接口必返 trade_date、分页 page/page_size/has_more、
响应信封 {code,message,data}（成功 code=0 / 失败 code=业务码且 data=null）、
错误码表 §3、latest_date 指针语义。
读路径：cache.Cache（进程内 TTL）→ SQLite 预计算表（API 零计算）。
自选/提醒 M2 冻结（契约 §2.8 保留，不实现；watchlist 路由返回 50302 提示前端用本地缓存）。
"""
from __future__ import annotations

import bisect
import re
from datetime import datetime, timedelta

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import config
import compute
import db
import monitoring
from cache import cache

app = FastAPI(title="A股ETF资金流入个股监控 - M2 API", version=config.APP_VERSION)
import history_queries
history_queries.install_routes(app)
app.add_middleware(CORSMiddleware,
                   allow_origins=["http://127.0.0.1:4173", "http://localhost:4173"],
                   allow_methods=["GET"], allow_headers=["Accept", "Content-Type"])

CODE_RE = re.compile(r"^\d{6}$")
CATEGORY_MAP = {"broad": "宽基", "industry": "行业", "theme": "主题"}
CATEGORY_REV = {v: k for k, v in CATEGORY_MAP.items()}


class ApiError(Exception):
    def __init__(self, code: int, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status


def abort(code: int, message: str, http_status: int = 400):
    raise ApiError(code, message, http_status)


@app.exception_handler(ApiError)
async def api_error_handler(request, exc: ApiError):  # noqa: ANN001
    return JSONResponse(status_code=exc.http_status,
                        content={"code": exc.code, "message": exc.message,
                                 "data": None})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):  # noqa: ANN001
    """D1：FastAPI 参数类型/校验错误（如 /rank?page=abc）→ 契约信封 400/40001。"""
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in first.get("loc", [])) if first.get("loc") else ""
    msg = str(first.get("msg", ""))
    detail = f"参数缺失/格式非法: {loc} {msg}".strip()
    return JSONResponse(status_code=400,
                        content={"code": 40001, "message": detail, "data": None})


@app.exception_handler(Exception)
async def unhandled(request, exc):  # noqa: ANN001
    return JSONResponse(status_code=500,
                        content={"code": 50001,
                                 "message": f"服务器内部错误: {type(exc).__name__}",
                                 "data": None})


def ok(data):
    return {"code": 0, "message": "ok", "data": data}


def r2(v, ndigits: int = 2):
    """金额/bp 统一保留 2 位小数；None 保持 None。"""
    return round(float(v), ndigits) if v is not None else None


def _ws_enum(raw: str | None) -> str | None:
    """权重来源归一化到契约 §2.4 枚举：index|quarterly|none。

    模型原始值（index_csindex(000300) / report(2026Q2) / missing）→ 契约枚举；
    细节（指数代码/季度）由 weight_date / weight_quarter 字段承载。
    """
    if not raw:
        return None
    r = str(raw)
    if r.startswith("index"):
        return "index"
    if r.startswith("report") or r.startswith("quarterly"):
        return "quarterly"
    return "none"


def _cov01(pct) -> float | None:
    """覆盖率百分比（模型口径 0-100）→ 契约口径 0–1（4 位小数）。"""
    return round(float(pct) / 100.0, 4) if pct is not None else None


def _latest_or_503() -> str:
    latest = db.meta_get("latest_date")
    if not latest:
        abort(50301, "数据未就绪：首次盘后任务尚未完成，请稍后重试", http_status=503)
    return latest


def _valid_trade_date(td: str | None, default: str) -> str:
    td = td or default
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", td):
        abort(40001, f"trade_date 格式非法: {td}")
    row = db.query_one("SELECT COUNT(*) AS n FROM flow_daily WHERE trade_date=?",
                       (td,))
    if not row or not row["n"]:
        abort(40404, f"trade_date={td} 无数据（非交易日或未落库）", http_status=404)
    return td


def _paginate(page: int, page_size: int):
    if page < 1:
        abort(40003, "page 从 1 开始")
    if not 1 <= page_size <= config.PAGE_SIZE_MAX:
        abort(40003, f"page_size 需在 1-{config.PAGE_SIZE_MAX} 之间")
    return page, page_size


def _meta_pipeline() -> dict:
    def m(k):
        return db.meta_get(k) or None
    return {"sse_share_date": m("sse_share_date"), "szse_share_date": m("szse_share_date"),
            "holdings_date": m("holdings_date"), "computed_date": m("computed_date"),
            "cached_date": m("cached_date")}


def _szse_accumulating() -> bool:
    row = db.query_one("SELECT COUNT(*) AS n FROM etf_meta WHERE status != 'active'")
    return bool(row and row["n"])


# ---------------- 2.1 meta ----------------
@app.get("/api/v1/meta")
def meta():
    latest = _latest_or_503()
    ver_row = db.query_one("SELECT version, MAX(updated_at) AS updated FROM industry_dict")
    calendar_data = monitoring.calendar_snapshot(latest)
    stock_windows = {p: {k:v for k,v in monitoring.window_for(latest,p,calendar_data).items() if not k.startswith('_')} for p in config.STOCK_PERIODS}
    return ok({
        "latest_date": latest,
        "prev_trade_date": db.meta_get("prev_trade_date") or None,
        "app_version": config.APP_VERSION,
        "industry_dict_version": ver_row["version"] if ver_row else None,
        "units": {"money": "元", "intensity": "bp"},
        "windows": list(config.PERIODS),
        "stock_periods": list(config.STOCK_PERIODS), "stock_windows": stock_windows,
        "coverage": monitoring.global_coverage(),
        "history_basis": "current_weights", "history_note": monitoring.HISTORY_NOTE,
        "szse_accumulating": _szse_accumulating(),
        "disclosure": {"short": config.DISCLOSURE_SHORT,
                       "long": config.DISCLOSURE_LONG,
                       "disclaimer": config.DISCLAIMER},
    })


# ---------------- 2.2 health ----------------
@app.get("/api/v1/health")
def health():
    latest = db.meta_get("latest_date")
    pipeline = _meta_pipeline()
    if not latest:
        status, msg = "degraded", "数据未就绪：首次盘后任务尚未完成"
    elif pipeline["computed_date"] and pipeline["sse_share_date"] \
            and pipeline["computed_date"] < pipeline["sse_share_date"]:
        status, msg = "degraded", "最新份额数据已落库，计算尚未完成"
    else:
        status, msg = "ok", "数据正常"
    return ok({
        "status": status,
        "latest_date": latest,
        "prev_trade_date": db.meta_get("prev_trade_date") or None,
        "pipeline": pipeline,
        "szse_accumulating": _szse_accumulating(),
        "message": msg,
    })


# ---------------- 2.3 rank ----------------
@app.get("/api/v1/rank")
def rank(period: str = Query("1d"), industry: str | None = Query(None),
         direction: str = Query("in"), trade_date: str | None = Query(None),
         page: int = Query(1), page_size: int = Query(config.PAGE_SIZE_DEFAULT)):
    if period not in config.PERIODS:
        abort(40002, f"period 取值非法: {period}（1d/5d/20d）")
    if direction not in ("in", "out"):
        abort(40002, "direction 取值非法（in/out）")
    page, page_size = _paginate(page, page_size)
    latest = _latest_or_503()
    td = _valid_trade_date(trade_date, latest)

    industry_name = None
    industry_code = ""
    if industry:
        row = db.query_one("SELECT industry_name FROM industry_dict WHERE industry_code=?",
                           (industry,))
        if not row:
            abort(40403, f"industry={industry} 不存在", http_status=404)
        industry_code, industry_name = industry, row["industry_name"]
    else:
        industry_name = "全部行业"

    def build():
        flow, intensity, valid = {"1d": ("flow_1d_yuan", "intensity_1d_bp", "1"), "5d": ("flow_5d_yuan", "intensity_5d_bp", "d.valid_days_5"), "20d": ("flow_20d_yuan", "intensity_20d_bp", "d.valid_days_20")}[period]
        sign, ordering = (">0", "DESC") if direction == "in" else ("<0", "ASC")
        rows = db.query(f"SELECT d.stock_code, COALESCE(m.stock_name,f.stock_name) AS stock_name, d.{flow} AS flow, d.{intensity} AS intensity_bp, {valid} AS valid_days, m.industry_name AS s_industry_name, m.industry_code AS s_industry_code FROM flow_derived d LEFT JOIN stock_meta m ON m.stock_code=d.stock_code LEFT JOIN flow_daily f ON f.stock_code=d.stock_code AND f.trade_date=d.trade_date WHERE d.trade_date=? AND d.{flow} {sign} AND (?='' OR m.industry_code=?) ORDER BY d.{flow} {ordering}, d.stock_code", (td, industry_code, industry_code))
        flag_rows = db.query("SELECT b.stock_code, GROUP_CONCAT(q.flags, '|') AS flags FROM flow_by_etf b JOIN quality_marks q ON q.trade_date=b.trade_date AND q.fund_code=b.fund_code WHERE b.trade_date=? AND q.flags != '' GROUP BY b.stock_code", (td,))
        flags = {r['stock_code']: sorted(set(r['flags'].split('|'))) for r in flag_rows}
        return [{
            "rank": i+1, "stock_code": r["stock_code"],
            "stock_name": r["stock_name"], "flow": r2(r["flow"]),
            "intensity_bp": r2(r["intensity_bp"]),
            "valid_days": f"{int(r['valid_days'] or 0)}/{period[:-1]}",
            "flags": flags.get(r['stock_code'], []),
            "industry": r["s_industry_code"] or None,
            "industry_name": r["s_industry_name"] or "未分类",
        } for i, r in enumerate(rows)]

    items = cache.get(f"rank:{period}:{direction}:{industry_code}:{td}", build)
    total = len(items)
    start = (page - 1) * page_size
    return ok({
        "trade_date": td, "period": period,
        "industry": industry_code or None, "industry_name": industry_name,
        "direction": direction, "total": total, "page": page,
        "page_size": page_size,
        "has_more": start + page_size < total,
        "items": items[start:start + page_size],
    })


# ---------------- 2.4 stock/{code} ----------------
def _quality_for(td: str, code: str) -> dict:
    contrib = db.query(
        "SELECT fund_code, contribution_yuan, weight_source, coverage_pct FROM flow_by_etf "
        "WHERE trade_date=? AND stock_code=?", (td, code))
    if not contrib:
        return {"weight_source": None, "coverage": None, "flags": [], "note": ""}
    dom = max(contrib, key=lambda r: abs(r["contribution_yuan"] or 0))
    tot = sum(abs(r["contribution_yuan"] or 0) for r in contrib) or 1.0
    cov = sum((r["coverage_pct"] or 0) * abs(r["contribution_yuan"] or 0)
              for r in contrib) / tot
    flags: list[str] = []
    for r in contrib:
        m = db.query_one("SELECT flags FROM quality_marks WHERE trade_date=? AND fund_code=?",
                         (td, r["fund_code"]))
        if m and m["flags"]:
            flags.extend(f for f in m["flags"].split("|") if f)
    # 权重过期（>6 个月，模型说明 §3 低置信标记）
    wrow = db.query_one(
        "SELECT weight_quarter FROM weights_used WHERE fund_code=? AND stock_code=?",
        (dom["fund_code"], code))
    if wrow and wrow["weight_quarter"]:
        m = re.match(r"(\d{4})Q([1-4])", wrow["weight_quarter"])
        if m:
            q_end = datetime(int(m.group(1)), int(m.group(2)) * 3, 28) \
                + timedelta(days=4)
            if (datetime.strptime(td, "%Y-%m-%d") - q_end).days > 183:
                flags.append("weight_stale")
    flags = sorted(set(flags))
    note_parts = []
    if flags:
        note_parts.append("事件标记：" + "、".join(flags))
    if dom["weight_source"] == "missing":
        note_parts.append("权重缺失")
    return {"weight_source": _ws_enum(dom["weight_source"]),
            "coverage": _cov01(cov),
            "flags": flags, "note": "；".join(note_parts)}


def _series_for(td: str, code: str, period: str = '5y') -> list[dict]:
    window = monitoring.window_for(td, period)
    allowed = set(window['_dates'])
    if not allowed:
        return []
    rows = db.query(
        "SELECT trade_date, flow_1d_yuan, intensity_1d_bp, float_mv_yi FROM flow_derived "
        "WHERE stock_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date DESC",
        (code, min(allowed), td))
    rows = [row for row in rows if row['trade_date'] in allowed and row['flow_1d_yuan'] is not None]
    if not rows:
        return []
    rows = list(reversed(rows))  # 升序
    closes = {r["trade_date"]: r["close"] for r in db.query(
        "SELECT trade_date, close FROM stock_quote_daily WHERE stock_code=?",
        (code,))}
    snap_row = db.query_one(
        "SELECT trade_date, float_mv_yi FROM float_mcap_daily WHERE stock_code=? "
        "ORDER BY trade_date DESC LIMIT 1", (code,))
    out = []
    for r in rows:
        flow = r["flow_1d_yuan"]
        bp = r["intensity_1d_bp"]  # 模型口径（当日快照分母）
        if flow is not None and snap_row and snap_row["float_mv_yi"] \
                and r["trade_date"] in closes and snap_row["trade_date"] in closes:
            ratio = closes[r["trade_date"]] / closes[snap_row["trade_date"]]
            if ratio > 0:
                # A7 历史流通市值近似法：市值(t) ≈ 当日市值 × 收盘价(t)/收盘价(当日)
                bp = flow / (snap_row["float_mv_yi"] * 1e8 * ratio) * 1e4
        out.append({"trade_date": r["trade_date"], "flow": r2(flow),
                    "intensity_bp": r2(bp)})
    return out


@app.get("/api/v1/stocks")
def stocks(q: str = Query("", max_length=50), page: int = Query(1), page_size: int = Query(config.PAGE_SIZE_DEFAULT)):
    page, page_size = _paginate(page, page_size)
    td = _latest_or_503()
    term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.query("SELECT u.stock_code, COALESCE(m.stock_name, u.stock_name) AS stock_name, m.industry_name, d.flow_1d_yuan FROM (SELECT stock_code, MAX(stock_name) AS stock_name FROM (SELECT stock_code, stock_name FROM stock_meta UNION ALL SELECT stock_code, stock_name FROM weights_used) GROUP BY stock_code) u LEFT JOIN stock_meta m ON m.stock_code=u.stock_code LEFT JOIN flow_derived d ON d.stock_code=u.stock_code AND d.trade_date=?", (td,))
    merged = {code: {"stock_code": code, "stock_name": r.get("stock_name"), "industry_name": r.get("industry_name"), "flow_1d_yuan": None} for code, r in monitoring.catalog().items()}
    merged.update({r["stock_code"]: r for r in rows})
    term = q.strip()
    rows = sorted((r for r in merged.values() if term in r['stock_code'] or term in (r['stock_name'] or '')), key=lambda r: r['stock_code'])
    start = (page - 1) * page_size
    items = [{"stock_code": r["stock_code"], "stock_name": r["stock_name"], "industry_name": r["industry_name"] or "未分类", "data_status": compute.stock_data_status(r["stock_code"]), "flow_1d": r2(r["flow_1d_yuan"])} for r in rows[start:start+page_size]]
    return ok({"trade_date": td, "total": len(rows), "page": page, "page_size": page_size, "has_more": start+page_size < len(rows), "items": items})


@app.get("/api/v1/stock/{code}")
def stock_detail(code: str, trade_date: str | None = Query(None), period: str = Query("1d")):
    if not CODE_RE.match(code):
        abort(40001, f"股票代码格式非法: {code}")
    if period not in config.STOCK_PERIODS:
        abort(40002, "period 取值非法")
    wrow = db.query_one("SELECT (SELECT COUNT(*) FROM weights_used WHERE stock_code=?) + (SELECT COUNT(*) FROM stock_meta WHERE stock_code=?) AS n", (code, code))
    catalog_stock = monitoring.catalog().get(code)
    if (not wrow or not wrow["n"]) and not catalog_stock:
        abort(40401, f"stock_code={code} 不存在或不在覆盖池", http_status=404)
    latest = _latest_or_503()
    td = _valid_trade_date(trade_date, latest)

    def build():
        d = db.query_one(
            "SELECT d.trade_date, d.flow_1d_yuan, d.flow_5d_yuan, d.valid_days_5, "
            "d.flow_20d_yuan, d.valid_days_20, d.float_mv_yi, d.intensity_1d_bp, "
            "d.intensity_5d_bp, d.intensity_20d_bp, m.stock_name, m.industry_code, "
            "m.industry_name FROM flow_derived d LEFT JOIN stock_meta m "
            "ON m.stock_code = d.stock_code WHERE d.trade_date=? AND d.stock_code=?",
            (td, code))
        meta = db.query_one("SELECT stock_name, industry_code, industry_name FROM stock_meta WHERE stock_code=?",
                            (code,))
        if not d:
            d = {"trade_date": td, "flow_1d_yuan": None, "flow_5d_yuan": None,
                 "valid_days_5": None, "flow_20d_yuan": None, "valid_days_20": None,
                 "float_mv_yi": None, "intensity_1d_bp": None, "intensity_5d_bp": None,
                 "intensity_20d_bp": None, "stock_name": None,
                 "industry_code": None, "industry_name": None}
        top_rows = db.query(
            "SELECT f.fund_code, e.fund_name, f.contribution_yuan, f.delta_shares, "
            "f.nav_used, f.weight, f.weight_source, w.weight_quarter "
            "FROM flow_by_etf f JOIN etf_meta e ON e.fund_code = f.fund_code "
            "LEFT JOIN weights_used w ON w.fund_code = f.fund_code "
            "AND w.stock_code = f.stock_code "
            "WHERE f.trade_date=? AND f.stock_code=? "
            "ORDER BY ABS(f.contribution_yuan) DESC LIMIT 5", (td, code))
        top_etfs = [{
            "etf_code": r["fund_code"], "etf_name": r["fund_name"],
            "flow": r2(r["contribution_yuan"]), "share_change": r2(r["delta_shares"]),
            "nav": r2(r["nav_used"]), "weight": r2(r["weight"], 6),
            "weight_source": _ws_enum(r["weight_source"]),
            "weight_date": r["weight_quarter"],
        } for r in top_rows]
        name = d["stock_name"] or (meta["stock_name"] if meta else None) or (catalog_stock.get("stock_name") if catalog_stock else None)
        if not name:
            wname = db.query_one("SELECT stock_name FROM weights_used WHERE stock_code=? LIMIT 1", (code,))
            name = wname["stock_name"] if wname else None
        movement = monitoring.stock_movement(code, td, period, d)
        for contribution in movement["etf_contributions"]:
            contribution["weight_source"] = _ws_enum(contribution["weight_source"])
        status = compute.stock_data_status(code)
        note = None
        if status in ("sse_only", "accumulating"):
            note = "深市 ETF 份额历史积累中，日频估算暂仅覆盖沪市 ETF"
        return {
            "trade_date": td, "stock_code": code, "stock_name": name,
            "industry": d["industry_code"] or (meta["industry_code"] if meta else None) or (catalog_stock.get("industry_code") if catalog_stock else None),
            "industry_name": d["industry_name"] or (meta["industry_name"] if meta else None) or (catalog_stock.get("industry_name") if catalog_stock else None) or "未分类",
            "float_mcap": r2(d["float_mv_yi"] * 1e8) if d["float_mv_yi"] else None,
            "flow_1d": r2(d["flow_1d_yuan"]), "flow_5d": r2(d["flow_5d_yuan"]),
            "flow_20d": r2(d["flow_20d_yuan"]),
            "intensity_1d_bp": r2(d["intensity_1d_bp"]),
            "intensity_5d_bp": r2(d["intensity_5d_bp"]),
            "intensity_20d_bp": r2(d["intensity_20d_bp"]),
            "valid_days": {
                "d1": "1/1" if d["flow_1d_yuan"] is not None else "0/1",
                "d5": f"{int(d['valid_days_5'] or 0)}/5",
                "d20": f"{int(d['valid_days_20'] or 0)}/20",
            },
            "series": _series_for(td, code, period),
            "top_etfs": top_etfs,
            "quality": _quality_for(td, code),
            "data_status": status,
            "note": note,
            **movement,
        }

    return ok(cache.get(f"stock:{code}:{td}:{period}", build))


# ---------------- 2.5 etf/{code} ----------------
@app.get("/api/v1/etf/{code}")
def etf_detail(code: str, trade_date: str | None = Query(None)):
    if not CODE_RE.match(code):
        abort(40001, f"ETF 代码格式非法: {code}")
    em = db.query_one("SELECT * FROM etf_meta WHERE fund_code=?", (code,))
    if not em:
        abort(40402, f"etf_code={code} 不在覆盖清单", http_status=404)
    latest = _latest_or_503()
    td = _valid_trade_date(trade_date, latest)

    def build():
        dec = db.query_one("SELECT * FROM weight_decisions WHERE fund_code=?", (code,))
        mark = db.query_one("SELECT * FROM quality_marks WHERE trade_date=? AND fund_code=?",
                            (td, code))
        share_row = db.query_one(
            "SELECT * FROM etf_share_daily WHERE fund_code=? AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT 1", (code, td))
        share_change = mark["delta_shares"] if mark else None
        if share_change is None and share_row:
            prev = db.query_one(
                "SELECT shares FROM etf_share_daily WHERE fund_code=? AND trade_date<? "
                "ORDER BY trade_date DESC LIMIT 1", (code, td))
            if prev and prev["shares"] is not None and share_row["shares"] is not None:
                share_change = share_row["shares"] - prev["shares"]
        est = db.query_one(
            "SELECT SUM(contribution_yuan) AS s FROM flow_by_etf "
            "WHERE trade_date=? AND fund_code=?", (td, code))
        top_rows = db.query(
            "SELECT stock_code, stock_name, contribution_yuan, weight FROM flow_by_etf "
            "WHERE trade_date=? AND fund_code=? ORDER BY ABS(contribution_yuan) DESC LIMIT 5",
            (td, code))
        # v1.1 补丁：60 日份额序列与规模走势（升序、截至 td、不足按实际条数）
        share_rows = db.query(
            "SELECT trade_date, shares FROM etf_share_daily "
            "WHERE fund_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 60",
            (code, td))
        share_rows = list(reversed(share_rows))
        nav_rows = db.query(
            "SELECT nav_date, unit_nav FROM etf_nav_daily "
            "WHERE fund_code=? AND nav_date<=? ORDER BY nav_date", (code, td))
        nav_dates = [r["nav_date"] for r in nav_rows]
        nav_vals = [r["unit_nav"] for r in nav_rows]
        share_series, scale_series = [], []
        prev_shares = None
        for s in share_rows:
            i = bisect.bisect_right(nav_dates, s["trade_date"]) - 1
            nav_t = nav_vals[i] if i >= 0 else None
            scale = (s["shares"] * nav_t) \
                if (s["shares"] is not None and nav_t is not None) else None
            share_series.append({
                "trade_date": s["trade_date"], "share_total": r2(s["shares"]),
                "share_change": r2(s["shares"] - prev_shares)
                if prev_shares is not None else None})
            scale_series.append({"trade_date": s["trade_date"], "scale": r2(scale)})
            prev_shares = s["shares"]
        note = em["notes"] or None
        if em["listed_date"]:
            try:
                listed = datetime.strptime(em["listed_date"], "%Y-%m-%d").date()
                if (datetime.strptime(td, "%Y-%m-%d").date() - listed).days \
                        < config.LISTING_DAYS_MIN:
                    note = (note + "；" if note else "") + "上市未满 60 交易日，暂不纳入测算"
            except ValueError:
                pass
        return {
            "trade_date": td, "etf_code": code, "etf_name": em["fund_name"],
            "exchange": "SSE" if em["market"] == "SH" else "SZSE",
            "category": CATEGORY_MAP.get(em["category"], em["category"]),
            "share_total": r2(share_row["shares"]) if share_row else None,
            "share_change": r2(share_change),
            "nav_prev": r2(mark["nav_used"]) if mark else None,
            "est_flow": r2(est["s"]) if est and est["s"] is not None else None,
            "weight_source": _ws_enum(dec["weight_source"]) if dec else None,
            "weight_date": dec["weight_quarter"] if dec else None,
            "coverage": _cov01(dec["coverage_pct"]) if dec and dec["coverage_pct"] else None,
            "share_series": share_series,
            "scale_series": scale_series,
            "top_stocks": [{"stock_code": r["stock_code"], "stock_name": r["stock_name"],
                            "flow": r2(r["contribution_yuan"]),
                            "weight": r2(r["weight"], 6)} for r in top_rows],
            "note": note,
            "flags": [f for f in (mark["flags"] or "").split("|") if f] if mark else [],
        }

    return ok(cache.get(f"etf:{code}:{td}", build))


# ---------------- 2.6 industries ----------------
@app.get("/api/v1/industries")
def industries():
    def build():
        rows = db.query(
            "SELECT industry_code, industry_name, stock_count, version, updated_at "
            "FROM industry_dict WHERE industry_code != '' ORDER BY industry_name")
        ver_row = db.query_one(
            "SELECT version, MAX(updated_at) AS updated FROM industry_dict")
        return {
            "version": ver_row["version"] if ver_row else None,
            "updated_date": ver_row["updated"] if ver_row else None,
            "items": [{"industry_code": r["industry_code"],
                       "industry_name": r["industry_name"],
                       "stock_count": r["stock_count"]} for r in rows],
        }
    return ok(cache.get("industries", build))


# ---------------- 2.7 etfs ----------------
@app.get("/api/v1/etfs")
def etfs(category: str | None = Query(None),
         page: int = Query(1), page_size: int = Query(config.PAGE_SIZE_DEFAULT)):
    if category and category not in CATEGORY_REV:
        abort(40002, f"category 取值非法: {category}（宽基/行业/主题）")
    page, page_size = _paginate(page, page_size)
    td = _latest_or_503()
    cat_en = CATEGORY_REV.get(category) if category else None

    def build():
        sql = ("SELECT e.fund_code, e.fund_name, e.category, e.market, e.status, "
               "e.listed_date, d.weight_source, d.weight_quarter FROM etf_meta e "
               "LEFT JOIN weight_decisions d ON d.fund_code = e.fund_code")
        params: tuple = ()
        if cat_en:
            sql += " WHERE e.category=?"
            params = (cat_en,)
        rows = db.query(sql + " ORDER BY e.fund_code", params)
        out = []
        for r in rows:
            status = "active" if r["status"] == "active" else "accumulating"
            if r["listed_date"]:
                try:
                    listed = datetime.strptime(r["listed_date"], "%Y-%m-%d").date()
                    if (datetime.strptime(td, "%Y-%m-%d").date() - listed).days \
                            < config.LISTING_DAYS_MIN:
                        status = "new"
                except ValueError:
                    pass
            out.append({
                "etf_code": r["fund_code"], "etf_name": r["fund_name"],
                "category": CATEGORY_MAP.get(r["category"], r["category"]),
                "exchange": "SSE" if r["market"] == "SH" else "SZSE",
                "status": status,
                "listing_date": r["listed_date"] or None,
                "weight_source": _ws_enum(r["weight_source"]),
                "weight_date": r["weight_quarter"],
            })
        return out

    items = cache.get(f"etfs:{cat_en or ''}", build)
    total = len(items)
    start = (page - 1) * page_size
    return ok({
        "trade_date": td, "total": total, "page": page, "page_size": page_size,
        "has_more": start + page_size < total,
        "items": items[start:start + page_size],
    })


# ---------------- 2.8 watchlist（M2 冻结：路由保留，返回 50302） ----------------
@app.get("/api/v1/watchlist")
def watchlist_get():
    abort(50302, "自选/提醒 M2 冻结（契约 §2.8）：请使用前端本地缓存", http_status=200)


@app.post("/api/v1/watchlist")
def watchlist_post():
    abort(50302, "自选/提醒 M2 冻结（契约 §2.8）：请使用前端本地缓存", http_status=200)


@app.delete("/api/v1/watchlist")
def watchlist_delete():
    abort(50302, "自选/提醒 M2 冻结（契约 §2.8）：请使用前端本地缓存", http_status=200)
