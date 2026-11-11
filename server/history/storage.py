"""Independent resumable ETF data store."""
import os
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'data' / 'market_history.db'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS funds(fund_code TEXT PRIMARY KEY, fund_name TEXT, market TEXT, category TEXT, listed_date TEXT, delisted_date TEXT, lifecycle_status TEXT, current_seen INTEGER NOT NULL DEFAULT 0, first_seen TEXT, last_seen TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS shares(fund_code TEXT NOT NULL, trade_date TEXT NOT NULL, shares REAL NOT NULL, source TEXT NOT NULL, PRIMARY KEY(fund_code,trade_date));
CREATE TABLE IF NOT EXISTS nav(fund_code TEXT NOT NULL, trade_date TEXT NOT NULL, unit_nav REAL NOT NULL, acc_nav REAL, source TEXT NOT NULL, PRIMARY KEY(fund_code,trade_date));
CREATE TABLE IF NOT EXISTS fund_events(fund_code TEXT NOT NULL, trade_date TEXT NOT NULL, description TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(fund_code,trade_date));
CREATE TABLE IF NOT EXISTS nav_verified(fund_code TEXT NOT NULL, trade_date TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(fund_code,trade_date));
CREATE TABLE IF NOT EXISTS calendar(trade_date TEXT PRIMARY KEY, source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(dataset TEXT NOT NULL, job_key TEXT NOT NULL, status TEXT NOT NULL, rows_count INTEGER NOT NULL DEFAULT 0, error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(dataset,job_key));
CREATE INDEX IF NOT EXISTS shares_by_date ON shares(trade_date,fund_code);
CREATE INDEX IF NOT EXISTS nav_by_date ON nav(trade_date,fund_code);
'''

def connect():
    path = Path(os.environ.get('ETF_HISTORY_DB_PATH', str(DEFAULT_PATH))).resolve()
    if path == (DEFAULT_PATH.parent / 'app.db').resolve():
        raise ValueError('history DB must not be the existing application database')
    path.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(SCHEMA)
    return db

def success(db, dataset, key):
    row=db.execute('SELECT status FROM jobs WHERE dataset=? AND job_key=?',(dataset,key)).fetchone()
    return bool(row and row['status'] in ('success','unsupported'))

def record(db,dataset,key,status,count=0,error=None):
    db.execute("INSERT INTO jobs(dataset,job_key,status,rows_count,error,updated_at) VALUES(?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now')) ON CONFLICT(dataset,job_key) DO UPDATE SET status=excluded.status,rows_count=excluded.rows_count,error=excluded.error,updated_at=excluded.updated_at", (dataset,key,status,count,str(error)[:1000] if error else None))
