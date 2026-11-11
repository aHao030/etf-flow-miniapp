# -*- coding: utf-8 -*-
"""SQLite 存储层（M2 MVP）。

- MVP 用 SQLite（零依赖，单机本地部署）；MySQL/Redis 适配点：
  * 本模块是唯一直接写 SQL 的地方——将来换 MySQL 时仅改本模块的连接与方言；
  * 榜单/详情读路径先走 cache.Cache（进程内），缓存未命中再落本层——将来
    换 Redis 时仅替换 cache 实现，SQL 查询语义不变（契约 §4 的 key 为行为约定）。
- WAL 模式 + 每操作独立连接，适配 FastAPI 线程池并发。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_share_daily (
  fund_code  TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  market     TEXT NOT NULL,            -- SH / SZ
  shares     REAL,
  shares_wan REAL,
  source     TEXT,                     -- sse / szse
  PRIMARY KEY (fund_code, trade_date, market)
);
CREATE TABLE IF NOT EXISTS etf_nav_daily (
  fund_code TEXT NOT NULL,
  nav_date  TEXT NOT NULL,
  unit_nav  REAL,
  acc_nav   REAL,
  PRIMARY KEY (fund_code, nav_date)
);
CREATE TABLE IF NOT EXISTS etf_meta (
  fund_code      TEXT PRIMARY KEY,
  fund_name      TEXT,
  market         TEXT,
  category       TEXT,                 -- broad / industry / theme
  index_code     TEXT,
  weight_policy  TEXT,
  listed_date    TEXT,
  status         TEXT,                 -- active / snapshot_only
  notes          TEXT
);
CREATE TABLE IF NOT EXISTS holdings (
  fund_code  TEXT NOT NULL,
  quarter    TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  pct_nav    REAL,
  shares_wan REAL,
  mktcap_wan REAL,
  PRIMARY KEY (fund_code, quarter, stock_code)
);
CREATE TABLE IF NOT EXISTS stock_quote_daily (
  stock_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  close      REAL,
  PRIMARY KEY (stock_code, trade_date)
);
CREATE TABLE IF NOT EXISTS stock_meta (
  stock_code    TEXT PRIMARY KEY,
  stock_name    TEXT,
  industry_code TEXT,
  industry_name TEXT
);
CREATE TABLE IF NOT EXISTS float_mcap_daily (
  stock_code  TEXT NOT NULL,
  trade_date  TEXT NOT NULL,           -- 快照对应交易日（以最新 SSE 交易日为准）
  float_mv_yi REAL,
  PRIMARY KEY (stock_code, trade_date)
);
-- 以下为模型输出镜像（口径 100% 来自 model/v1_flow.py 产物，禁止另写口径）
CREATE TABLE IF NOT EXISTS flow_daily (
  trade_date TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  flow_yuan  REAL,
  n_etf      INTEGER,
  PRIMARY KEY (trade_date, stock_code)
);
CREATE TABLE IF NOT EXISTS flow_derived (
  trade_date      TEXT NOT NULL,
  stock_code      TEXT NOT NULL,
  flow_1d_yuan    REAL, flow_5d_yuan REAL, valid_days_5 INTEGER,
  flow_20d_yuan   REAL, valid_days_20 INTEGER,
  float_mv_yi     REAL,
  intensity_1d_bp REAL, intensity_5d_bp REAL, intensity_20d_bp REAL,
  PRIMARY KEY (trade_date, stock_code)
);
CREATE TABLE IF NOT EXISTS flow_by_etf (
  trade_date      TEXT NOT NULL,
  fund_code       TEXT NOT NULL,
  stock_code      TEXT NOT NULL,
  stock_name      TEXT,
  weight          REAL,
  delta_shares    REAL,
  nav_used        REAL,
  contribution_yuan REAL,
  weight_source   TEXT,
  coverage_pct    REAL,
  PRIMARY KEY (trade_date, fund_code, stock_code)
);
CREATE TABLE IF NOT EXISTS quality_marks (
  trade_date    TEXT NOT NULL,
  fund_code     TEXT NOT NULL,
  delta_shares  REAL,
  delta_pct     REAL,
  nav_used      REAL,
  coverage_pct  REAL,
  weight_source TEXT,
  flags         TEXT,
  PRIMARY KEY (trade_date, fund_code)
);
CREATE TABLE IF NOT EXISTS weights_used (
  fund_code      TEXT NOT NULL,
  stock_code     TEXT NOT NULL,
  stock_name     TEXT,
  weight         REAL,
  weight_source  TEXT,
  weight_quarter TEXT,
  coverage_pct   REAL,
  PRIMARY KEY (fund_code, stock_code)
);
CREATE TABLE IF NOT EXISTS weight_decisions (
  fund_code       TEXT PRIMARY KEY,
  policy          TEXT,
  weight_source   TEXT,
  weight_quarter  TEXT,
  coverage_pct    REAL,
  n_stocks        INTEGER,
  usable_quarters TEXT,
  used_index      INTEGER
);
CREATE TABLE IF NOT EXISTS rank_daily (
  trade_date    TEXT NOT NULL,
  period        TEXT NOT NULL,          -- 1d / 5d / 20d
  direction     TEXT NOT NULL,          -- in（MVP 仅 in；out 预留）
  industry_code TEXT NOT NULL,          -- '' = 全市场
  rank          INTEGER NOT NULL,
  stock_code    TEXT NOT NULL,
  stock_name    TEXT,
  flow          REAL,
  intensity_bp  REAL,
  valid_days    TEXT,                   -- "n/5" 形式
  flags         TEXT,                   -- "|".join
  PRIMARY KEY (trade_date, period, direction, industry_code, rank)
);
CREATE TABLE IF NOT EXISTS industry_dict (
  industry_code TEXT PRIMARY KEY,
  industry_name TEXT,
  stock_count   INTEGER,
  version       TEXT,
  updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS meta (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS task_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date  TEXT,
  step        TEXT,
  status      TEXT,                     -- ok / deferred / failed / skipped
  message     TEXT,
  started_at  TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rank_lookup ON rank_daily(trade_date, period, direction, industry_code);
CREATE INDEX IF NOT EXISTS idx_flow_stock ON flow_derived(stock_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_flowbyetf ON flow_by_etf(trade_date, stock_code);
CREATE INDEX IF NOT EXISTS idx_flowbyfund ON flow_by_etf(trade_date, fund_code);
CREATE INDEX IF NOT EXISTS idx_quote ON stock_quote_daily(stock_code, trade_date);
"""

_init_lock = threading.Lock()
_initialized = False
_transaction_connection = ContextVar("model_transaction_connection", default=None)

MODEL_SNAPSHOT_TABLES = frozenset({"flow_daily", "flow_derived", "flow_by_etf",
                                   "quality_marks", "weights_used", "weight_decisions"})


def replace_model_snapshot(snapshot: dict) -> None:
    """Atomically replace the complete model output; never touch source data."""
    if set(snapshot) != MODEL_SNAPSHOT_TABLES:
        raise ValueError("model snapshot must contain all six mirror tables")
    for table, (columns, rows) in snapshot.items():
        if not columns or not rows:
            raise ValueError(f"empty model snapshot: {table}")
    with conn() as c:
        if not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")
        for table, (columns, rows) in snapshot.items():
            actual_columns = {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}
            if not set(columns).issubset(actual_columns):
                raise ValueError(f"invalid model columns: {table}")
            c.execute(f'DELETE FROM "{table}"')
            names = ",".join(f'"{column}"' for column in columns)
            placeholders = ",".join("?" for _ in columns)
            c.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', rows)
        # Rankings are derived from the old snapshot; never leave stale rows.
        # The pipeline rebuilds them immediately after ingestion.
        c.execute("DELETE FROM rank_daily")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def conn():
    """每次操作一个独立连接（WAL 已开，读多写少）。"""
    existing = _transaction_connection.get()
    if existing is not None:
        yield existing
        return
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA busy_timeout=30000")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


@contextmanager
def transaction_scope():
    """Share one transaction among all DB helpers in this execution context."""
    if _transaction_connection.get() is not None:
        raise RuntimeError("nested transaction_scope is not supported")
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        token = _transaction_connection.set(c)
        try:
            yield c
        finally:
            _transaction_connection.reset(token)


def init() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        import os
        os.makedirs(config.DATA_DIR, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            c.commit()
        finally:
            c.close()
        _initialized = True


def query(sql: str, params: tuple = ()) -> list[dict]:
    with conn() as c:
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> None:
    with conn() as c:
        c.execute(sql, params)


def executemany(sql: str, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn() as c:
        c.executemany(sql, rows)


def replace_rows(table: str, columns: list[str], rows: list[tuple]) -> None:
    """INSERT OR REPLACE 批量 upsert（幂等）。"""
    if not rows:
        return
    cols = ",".join(columns)
    marks = ",".join(["?"] * len(columns))
    executemany(f"INSERT OR REPLACE INTO {table}({cols}) VALUES({marks})", rows)


# ---------------- meta 存取（latest_date 指针与管线日期） ----------------
def meta_get(key: str, default=None):
    row = query_one("SELECT value FROM meta WHERE key=?", (key,))
    return row["value"] if row else default


def meta_set(key: str, value: str) -> None:
    execute("INSERT OR REPLACE INTO meta(key,value,updated_at) VALUES(?,?,?)",
            (key, str(value), _now()))


def meta_get_json(key: str, default=None):
    v = meta_get(key)
    return json.loads(v) if v else default
