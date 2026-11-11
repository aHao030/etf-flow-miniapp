# -*- coding: utf-8 -*-
"""M2 每日盘后管线编排（backend-dev）。

步骤（契约 §4 / Lead 实现顺序）：
  数据就绪探测 → SSE 份额 → 净值 → 持仓（披露窗口重拉）→ 流通市值快照
  → 计算（复用 model）→ 入库 → 榜单预计算 → latest_date 原子推进 → 缓存失效
  → 个股行情（60 日序列用，历史流通市值近似法的分子）→ 深市份额落库

幂等：以 trade_date 为键，全部步骤 INSERT OR REPLACE / 追加去重，可反复重跑；
      computed_date 已到目标日则整体跳过。
定时（APScheduler，交易日，Asia/Shanghai）：
  19:30 主任务 / 22:30 兜底重试 / 8:10 T+1 补算；20:30 深市快照落库 / 8:00 核对补拉。
"""
from __future__ import annotations

import csv
import os
import sys
import time
from datetime import date, datetime, timedelta

import config
import compute
import db
import fetchers
from cache import cache

SHARES_CSV = os.path.join(config.INPUTS_DIR, "etf_shares_sse_history.csv")
NAV_CSV = os.path.join(config.INPUTS_DIR, "etf_nav_history.csv")
HOLD_CSV = os.path.join(config.INPUTS_DIR, "etf_holdings_all_quarters.csv")
FLOATCAP_CSV = os.path.join(config.INPUTS_DIR, "stock_floatcap_snapshot.csv")


def log(trade_date: str, step: str, status: str, message: str = "") -> None:
    db.execute("INSERT INTO task_log(trade_date,step,status,message,started_at,finished_at) "
               "VALUES(?,?,?,?,?,?)",
               (trade_date, step, status, message[:500], db._now(), db._now()))
    print(f"  [{step}] {status} {message}")


def load_universe():
    import pandas as pd
    df = pd.read_csv(os.path.join(config.MODEL_DIR, "etf_universe.csv"), dtype=str)
    return df


def universe_fund_names() -> dict[str, str]:
    u = load_universe()
    return dict(zip(u["fund_code"], u["fund_name"]))


# ---------------- 输入文件工具（追加去重 / 整表重写） ----------------
def _read_csv_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_csv_rows(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def ensure_inputs() -> None:
    """首次引导：从 M1 样例（data/samples，只读）拷贝为 server 输入。"""
    os.makedirs(config.INPUTS_DIR, exist_ok=True)
    for name in ("etf_shares_sse_history.csv", "etf_nav_history.csv",
                 "etf_holdings_all_quarters.csv", "stock_floatcap_snapshot.csv"):
        dst = os.path.join(config.INPUTS_DIR, name)
        src = os.path.join(config.DATA_SAMPLES_DIR, name)
        if not os.path.exists(dst) and os.path.exists(src):
            with open(src, encoding="utf-8-sig") as fs, \
                 open(dst, "w", encoding="utf-8-sig") as fd:
                fd.write(fs.read())
            print(f"  [bootstrap] 从 M1 样例拷贝 {name}")
        if not os.path.exists(dst):
            _write_csv_rows(dst, [], [])


# ---------------- 数据就绪探测 ----------------
def probe_latest_sse_date(max_back: int = 7) -> str | None:
    """回退探测上交所最新有数据的交易日（非交易日接口返回空）。"""
    d = date.today()
    for _ in range(max_back):
        if d.weekday() < 5:
            try:
                if fetchers.fetch_sse_etf_scale(d.strftime("%Y-%m-%d")):
                    return d.strftime("%Y-%m-%d")
            except Exception as e:  # noqa: BLE001
                print(f"  [probe] {d} 探测失败: {e}")
            time.sleep(config.SLEEP_SSE)
        d -= timedelta(days=1)
    return None


def last_trade_date_before(day: date) -> str | None:
    """≤ day-1 的最近交易日（以 DB 中 SSE 份额存在为准，否则按工作日近似）。"""
    d = day - timedelta(days=1)
    for _ in range(10):
        if d.weekday() < 5:
            row = db.query_one(
                "SELECT COUNT(*) AS n FROM etf_share_daily WHERE market='SH' "
                "AND trade_date=?", (d.strftime("%Y-%m-%d"),))
            if row and row["n"]:
                return d.strftime("%Y-%m-%d")
            if row is None or db.query_one(
                    "SELECT COUNT(*) AS n FROM etf_share_daily WHERE market='SH'")["n"] == 0:
                return d.strftime("%Y-%m-%d")  # 库空时按工作日近似（仅引导期）
        d -= timedelta(days=1)
    return None


# ---------------- ① 份额 ----------------
def etl_sse_shares(trade_date: str) -> int:
    rows = fetchers.fetch_sse_etf_scale(trade_date)
    if not rows:
        raise RuntimeError(f"SSE {trade_date} 无数据（非交易日或未披露）")
    uni = load_universe()
    sh_codes = set(uni[uni["market"] == "SH"]["fund_code"])
    names = universe_fund_names()
    new = []
    for r in rows:
        code = r.get("SEC_CODE")
        if code not in sh_codes:
            continue
        vol = r.get("TOT_VOL")
        new.append({
            "stat_date": trade_date, "market": "SH", "fund_code": code,
            "fund_name": names.get(code, r.get("SEC_NAME", "")),
            "shares_wan": vol,
            "shares": (f"{float(vol) * 10000:.4f}" if vol else ""),
        })
    if not new:
        raise RuntimeError(f"SSE {trade_date} 覆盖池内无匹配基金")
    # 追加去重（幂等）
    old = _read_csv_rows(SHARES_CSV)
    merged = {f"{r['stat_date']}|{r['market']}|{r['fund_code']}": r for r in old}
    added = 0
    for r in new:
        k = f"{r['stat_date']}|{r['market']}|{r['fund_code']}"
        if k not in merged:
            merged[k] = r
            added += 1
    rows = sorted(merged.values(), key=lambda r: (r["stat_date"], r["fund_code"]))
    _write_csv_rows(SHARES_CSV, ["stat_date", "market", "fund_code", "fund_name",
                                 "shares_wan", "shares"], rows)
    # 全量 upsert（含历史），支撑 etf 详情的 share_series（契约 v1.1）
    db.replace_rows("etf_share_daily",
                    ["fund_code", "trade_date", "market", "shares", "shares_wan", "source"],
                    [(r["fund_code"], r["stat_date"], "SH",
                      float(r["shares"]) if r["shares"] else None,
                      float(r["shares_wan"]) if r["shares_wan"] else None, "sse")
                     for r in rows])
    return added


def backfill_share_history_db() -> int:
    """将 inputs/etf_shares_sse_history.csv 全部行回填 etf_share_daily（幂等）。"""
    rows = _read_csv_rows(SHARES_CSV)
    db.replace_rows("etf_share_daily",
                    ["fund_code", "trade_date", "market", "shares", "shares_wan", "source"],
                    [(r["fund_code"], r["stat_date"], r["market"],
                      float(r["shares"]) if r["shares"] else None,
                      float(r["shares_wan"]) if r["shares_wan"] else None, "sse")
                     for r in rows if r.get("stat_date")])
    return len(rows)


def etl_szse_shares(effective_date: str, slot: str = "manual") -> int:
    """深交所快照落库；日期由调用方推断，未经源文件校验，不可用于日频计算。"""
    uni = load_universe()
    sz_codes = set(uni[uni["market"] == "SZ"]["fund_code"])
    names = universe_fund_names()
    rows = fetchers.fetch_szse_fund_scale()
    out, raw = [], []
    for r in rows:
        code = str(r.get("基金代码", "")).strip()
        if code not in sz_codes:
            continue
        shares = str(r.get("当前规模(份)") or "").replace(",", "").strip()
        out.append((code, effective_date, "SZ",
                    float(shares) if shares else None, None, f"szse:unverified_inferred_date:{slot}"))
        raw.append({"market": "SZ", "fund_code": code,
                    "fund_name": names.get(code, str(r.get("基金简称", "")).strip()),
                    "shares": shares, "nav": r.get("净值"),
                    "listed_date": r.get("上市日期"), "effective_date": effective_date})
    db.replace_rows("etf_share_daily",
                    ["fund_code", "trade_date", "market", "shares", "shares_wan", "source"],
                    out)
    if raw:
        _write_csv_rows(config.SZSE_LATEST_CSV,
                        ["market", "fund_code", "fund_name", "shares", "nav",
                         "listed_date", "effective_date"], raw)
    if out:
        # D4：落库成功即同步推进 szse_share_date 指针（health.pipeline 与 meta 口径一致）
        db.meta_set("szse_share_date", effective_date)
    return len(out)


# ---------------- ② 净值 / 持仓 ----------------
def etl_nav() -> int:
    names = universe_fund_names()
    old = _read_csv_rows(NAV_CSV)
    merged = {f"{r['fund_code']}|{r['nav_date']}": r for r in old}
    added = 0
    for code, name in names.items():
        try:
            items = fetchers.fetch_lsjz_nav(code)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 净值 {code} 获取失败: {e}")
            continue
        for it in items:
            k = f"{code}|{it['nav_date']}"
            if it["nav_date"] and k not in merged:
                merged[k] = {"fund_code": code, "fund_name": name,
                             "nav_date": it["nav_date"],
                             "unit_nav": it["unit_nav"] or "",
                             "acc_nav": it["acc_nav"] or "",
                             "nav_pct": it["nav_pct"] or ""}
                added += 1
        time.sleep(config.SLEEP_FUND)
    rows = sorted(merged.values(), key=lambda r: (r["fund_code"], r["nav_date"]))
    _write_csv_rows(NAV_CSV, ["fund_code", "fund_name", "nav_date", "unit_nav",
                              "acc_nav", "nav_pct"], rows)
    db.replace_rows("etf_nav_daily", ["fund_code", "nav_date", "unit_nav", "acc_nav"],
                    [(r["fund_code"], r["nav_date"],
                      float(r["unit_nav"]) if r["unit_nav"] else None,
                      float(r["acc_nav"]) if r["acc_nav"] else None) for r in rows])
    return added


def holdings_refresh_needed() -> bool:
    if not os.path.exists(HOLD_CSV) or os.path.getsize(HOLD_CSV) < 10:
        return True
    return date.today().month in config.WINDOW_MONTHS


def etl_holdings() -> int:
    """季报披露窗口（1/4/7/10 月）每日重拉至覆盖率达标；非窗口仅缺失时补。"""
    def _num(v):
        s = str(v).replace(",", "").strip()
        if s in ("", "---", "--", "-", "None", "nan"):
            return None
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    names = universe_fund_names()
    rows = _read_csv_rows(HOLD_CSV)
    changed = 0
    for code, name in names.items():
        try:
            items = fetchers.fetch_em_holdings(code)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 持仓 {code} 获取失败: {e}")
            continue
        if not items:
            print(f"  [WARN] 持仓 {code}: 无数据")
            continue
        rows = [r for r in rows if r["fund_code"] != code]  # 整基金替换（幂等）
        for it in items:
            it["fund_code"], it["fund_name"] = code, name
            rows.append(dict(it))
        qs = sorted({r["quarter"] for r in rows if r["fund_code"] == code})
        latest_q = qs[-1] if qs else ""
        cov = sum(_num(r["pct_nav"]) or 0.0 for r in rows
                  if r["fund_code"] == code and r["quarter"] == latest_q)
        print(f"  [holdings] {code} {name}: 季度 {qs}，最新 {latest_q} 覆盖 {cov:.1f}%")
        changed += len(items)
        time.sleep(config.SLEEP_FUND)
    rows.sort(key=lambda r: (r["fund_code"], r["quarter"], r["stock_code"]))
    _write_csv_rows(HOLD_CSV, ["fund_code", "fund_name", "quarter", "stock_code",
                               "stock_name", "pct_nav", "shares_wan", "mktcap_wan"],
                    rows)
    db.replace_rows("holdings",
                    ["fund_code", "quarter", "stock_code", "stock_name",
                     "pct_nav", "shares_wan", "mktcap_wan"],
                    [(r["fund_code"], r["quarter"], r["stock_code"], r["stock_name"],
                      _num(r["pct_nav"]), _num(r["shares_wan"]), _num(r["mktcap_wan"]))
                     for r in rows])
    db.meta_set("holdings_date", date.today().strftime("%Y-%m-%d"))
    return changed


# ---------------- ③ 个股行情 / 流通市值 ----------------
def stock_universe_from_sources() -> set[str]:
    """计算前的股票池：持仓表 ∪ 中证指数权重文件。"""
    stocks: set[str] = set()
    for r in _read_csv_rows(HOLD_CSV):
        if r["stock_code"]:
            stocks.add(r["stock_code"])
    idx_dir = os.path.join(config.MODEL_DIR, "cache", "csindex_weights")
    if os.path.isdir(idx_dir):
        for fn in os.listdir(idx_dir):
            if not fn.endswith(".csv") or fn.startswith("_"):
                continue
            for r in _read_csv_rows(os.path.join(idx_dir, fn)):
                if r.get("stock_code"):
                    stocks.add(r["stock_code"])
    return stocks


def etl_floatcap(trade_date: str) -> int:
    """流通市值快照（强度分母，A7）。模型输入 CSV + 落库（幂等）。"""
    stocks = sorted(stock_universe_from_sources())
    rows, failed = [], 0
    for i in range(0, len(stocks), config.SNAPSHOT_BATCH):
        batch = [fetchers.tx_symbol(s) for s in stocks[i:i + config.SNAPSHOT_BATCH]]
        batch = [b for b in batch if b]
        if not batch:
            continue
        try:
            got = fetchers.fetch_tx_snapshot(batch)
            rows.extend(got)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 快照批次 {i} 失败: {e}")
            failed += len(batch)
        time.sleep(0.3)
    def _num(v):
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None
    cleaned = [{"symbol": r["symbol"], "name": r["name"], "price": r["price"],
                "total_mv_yi": r["total_mv_yi"],
                "float_mv_yi": "" if _num(r["float_mv_yi"]) is None else f"{_num(r['float_mv_yi']):.2f}"}
               for r in rows if r.get("symbol")]
    _write_csv_rows(FLOATCAP_CSV, ["symbol", "name", "price", "total_mv_yi",
                                   "float_mv_yi"], cleaned)
    db.replace_rows("float_mcap_daily",
                    ["stock_code", "trade_date", "float_mv_yi"],
                    [(r["symbol"], trade_date, _num(r["float_mv_yi"]))
                     for r in cleaned])
    return len(cleaned)


def etl_klines() -> int:
    """个股 60 日日 K 落库（序列图 + 历史流通市值近似法：市值×价格比）。

    增量：已有最新交易日收盘价的股票跳过；双源：腾讯 → 新浪备源；
    连续 40 次失败熔断（双源均被限流时避免无谓空转），下次任务继续补。
    """
    stocks = sorted({r["stock_code"] for r in db.query(
        "SELECT DISTINCT stock_code FROM flow_daily")})
    latest_flow = db.query_one("SELECT MAX(trade_date) AS d FROM flow_daily")
    latest_flow = latest_flow["d"] if latest_flow else None
    ok, consec_fail = 0, 0
    for code in stocks:
        sym = fetchers.tx_symbol(code)
        if not sym:
            continue
        if latest_flow:
            have = db.query_one(
                "SELECT COUNT(*) AS n FROM stock_quote_daily "
                "WHERE stock_code=? AND trade_date=?", (code, latest_flow))
            if have and have["n"]:
                continue
        bars = None
        try:
            bars = fetchers.fetch_tx_kline(sym)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] K线(腾讯) {code}: {e}")
        if not bars:
            try:
                bars = fetchers.fetch_sina_kline(sym)
            except Exception as e:  # noqa: BLE001
                print(f"  [WARN] K线(新浪) {code}: {e}")
        if not bars:
            consec_fail += 1
            if consec_fail >= 40:
                print(f"  [ABORT] K线双源连续 {consec_fail} 次失败，疑似限流，本次中止")
                break
            time.sleep(1.0)
            continue
        consec_fail = 0
        db.replace_rows("stock_quote_daily", ["stock_code", "trade_date", "close"],
                        [(code, b[0], float(b[2])) for b in bars
                         if len(b) >= 3 and b[2]])
        ok += 1
        time.sleep(config.SLEEP_KLINE)
    return ok


def validate_model_target(target: str) -> None:
    """Reject stale or partially covered outputs before replacing the snapshot."""
    import pandas as pd
    universe = load_universe()
    expected = set(universe.loc[universe["status"] == "active", "fund_code"])
    if not expected:
        raise RuntimeError("无已启用基金，不能推进数据日期")
    flows = pd.read_csv(os.path.join(config.OUTPUTS_DIR, "flow_by_etf.csv"), dtype=str)
    if not {"date", "fund_code"}.issubset(flows.columns):
        raise RuntimeError("模型输出缺少日期或基金代码")
    target_flows = flows[flows["date"] == target]
    missing = expected - set(target_flows["fund_code"])
    if missing:
        raise RuntimeError("目标日缺少已启用基金测算: " + ",".join(sorted(missing)))
    if flows["date"].max() != target:
        raise RuntimeError("模型输出最新日期与目标日期不一致")


# ---------------- 主管线 ----------------
def run(trade_date: str | None = None) -> dict:
    db.init()
    t0 = time.time()
    target = trade_date or probe_latest_sse_date()
    if not target:
        log("", "probe", "failed", "连续 7 天无 SSE 数据")
        return {"status": "failed", "trade_date": None}
    done = db.meta_get("computed_date")
    if done == target:
        log(target, "pipeline", "skipped", "已计算（幂等跳过）")
        return {"status": "skipped", "trade_date": target}
    result = {"status": "ok", "trade_date": target}
    ensure_inputs()
    try:
        n = etl_sse_shares(target)
        log(target, "sse_shares", "ok", f"新增 {n} 行")
        db.meta_set("sse_share_date", target)
    except Exception as e:  # noqa: BLE001
        log(target, "sse_shares", "failed", str(e))
        result["status"] = "deferred"  # 数据未披露，22:30/次日 8:10 重试
        return result
    for step, fn in (("nav", etl_nav), ("holdings", etl_holdings),
                     ("floatcap", lambda: etl_floatcap(target))):
        try:
            n = fn()
            log(target, step, "ok", f"{n} 行")
        except Exception as e:  # noqa: BLE001
            log(target, step, "failed", str(e))
            result["status"] = "degraded"
    if result["status"] != "ok":
        result["error"] = "必要采集步骤失败，保留上一期模型结果"
        return result
    # 深市快照（同 main 管线落一次，幂等；20:30/8:00 定时再补）
    try:
        eff = last_trade_date_before(date.today())
        if eff:
            n = etl_szse_shares(eff, slot="pipeline")  # 内部同步推进 szse_share_date
            log(target, "szse_shares", "ok", f"{n} 只（有效日 {eff}）")
    except Exception as e:  # noqa: BLE001
        log(target, "szse_shares", "failed", str(e))
    try:
        compute.run_model()
        validate_model_target(target)
        with db.transaction_scope():
            latest = compute.ingest()
            if not latest or latest != target:
                raise RuntimeError("模型输出日期与目标日期不一致")
            compute.update_stock_meta()
            compute.update_etf_meta(load_universe())
            compute.build_ranks()  # 全部历史日期（支持历史回看）
            compute.advance_meta(latest)
        cache.invalidate()  # latest_date 原子切换：整批缓存失效
        log(latest, "compute", "ok", f"入库完成，latest_date={latest}")
        result["latest_date"] = latest
    except Exception as e:  # noqa: BLE001
        log(target, "compute", "failed", str(e))
        result["status"] = "degraded"
        result["error"] = str(e)
        return result
    try:
        n = etl_klines()
        log(target, "klines", "ok", f"{n} 只")
    except Exception as e:  # noqa: BLE001
        log(target, "klines", "failed", str(e))
    log(target, "pipeline", result["status"], f"用时 {time.time()-t0:.0f}s")
    return result


# ---------------- APScheduler 任务（交易日） ----------------
def _is_weekday() -> bool:
    return date.today().weekday() < 5


def job_pipeline() -> None:
    if not _is_weekday():
        return
    run()


def job_pipeline_retry() -> None:
    """22:30 兜底：今日数据 19:30 未出时在此重试。"""
    if not _is_weekday():
        return
    run(date.today().strftime("%Y-%m-%d"))


def job_pipeline_morning() -> None:
    """T+1 早间补算：以 T+1 早间为准，补昨日未完成的计算。"""
    if not _is_weekday():
        return
    target = last_trade_date_before(date.today())
    if target and (db.meta_get("computed_date") or "") < target:
        run(target)


def job_szse_evening() -> None:
    if not _is_weekday():
        return
    eff = last_trade_date_before(date.today())
    if eff:
        try:
            etl_szse_shares(eff, slot="evening")  # 内部同步推进 szse_share_date
            log(eff, "szse_shares", "ok", "20:30 落库")
        except Exception as e:  # noqa: BLE001
            log(eff, "szse_shares", "failed", str(e))


def job_szse_morning() -> None:
    if not _is_weekday():
        return
    eff = last_trade_date_before(date.today())
    if eff:
        try:
            etl_szse_shares(eff, slot="morning")  # 核对补拉，同 key 覆盖
            log(eff, "szse_shares", "ok", "8:00 核对补拉")
        except Exception as e:  # noqa: BLE001
            log(eff, "szse_shares", "failed", str(e))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        d = None
        if "--date" in sys.argv:
            d = sys.argv[sys.argv.index("--date") + 1]
        print(run(d))
    elif cmd == "probe":
        print("latest SSE date:", probe_latest_sse_date())
    elif cmd == "finalize":
        # 补完成（ETL/计算已就绪时）：份额回填 → 榜单 → latest_date 推进 → K线 → 缓存失效
        n = backfill_share_history_db()
        latest = db.query_one("SELECT MAX(trade_date) AS d FROM flow_daily")
        if not latest or not latest["d"]:
            print("finalize failed: flow_daily 为空，先跑 run")
            sys.exit(1)
        latest = latest["d"]
        compute.build_ranks()  # 全部历史日期
        compute.advance_meta(latest)
        k = etl_klines()
        cache.invalidate()
        print(f"finalize ok: latest_date={latest}, 份额回填 {n} 行, K线 {k} 只")
    elif cmd == "szse-pull":
        eff = last_trade_date_before(date.today())
        slot = sys.argv[2] if len(sys.argv) > 2 else "manual"
        print(f"szse pull ({slot}), effective={eff} ->", etl_szse_shares(eff, slot))
    elif cmd == "industry-build":
        import industry
        print(industry.build())
    else:
        print("用法: pipeline.py [run|finalize|probe|szse-pull|industry-build] [--date YYYY-MM-DD]")
