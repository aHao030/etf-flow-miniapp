"""Expand gated ETF factors into isolated stock/day estimates for review.

Only stock/days with every currently active, currently weighted ETF contribution
available are emitted. Missing funds do not become zero. This artifact does not
modify the application database or publish a snapshot.
"""
import argparse
import os
from pathlib import Path
import sqlite3


def expand(conn):
    conn.executescript('''
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    INSERT INTO metadata SELECT key,value FROM src.metadata;
    CREATE TEMP TABLE weights AS SELECT * FROM src.weights;
    CREATE INDEX temp.weights_by_stock ON weights(stock_code);
    CREATE TEMP TABLE expected AS SELECT stock_code,COUNT(*) AS n FROM weights GROUP BY stock_code;
    CREATE UNIQUE INDEX temp.expected_by_stock ON expected(stock_code);
    CREATE TABLE stock_daily(trade_date TEXT NOT NULL,stock_code TEXT NOT NULL,flow_yuan REAL NOT NULL,n_etf INTEGER NOT NULL,PRIMARY KEY(stock_code,trade_date));
    CREATE TABLE stock_etf_daily(trade_date TEXT NOT NULL,stock_code TEXT NOT NULL,fund_code TEXT NOT NULL,contribution_yuan REAL NOT NULL,PRIMARY KEY(stock_code,trade_date,fund_code));
    INSERT INTO stock_daily
      SELECT f.trade_date,w.stock_code,SUM(w.weight*f.etf_flow),COUNT(*)
      FROM weights w JOIN src.factors f ON f.fund_code=w.fund_code JOIN expected e ON e.stock_code=w.stock_code
      WHERE f.quality='estimated' AND f.etf_flow IS NOT NULL
      GROUP BY w.stock_code,f.trade_date
      HAVING COUNT(*)=MAX(e.n);
    INSERT INTO stock_etf_daily
      SELECT d.trade_date,d.stock_code,w.fund_code,w.weight*f.etf_flow
      FROM stock_daily d JOIN weights w ON w.stock_code=d.stock_code
      JOIN src.factors f ON f.fund_code=w.fund_code AND f.trade_date=d.trade_date
      WHERE f.quality='estimated' AND f.etf_flow IS NOT NULL;
    ''')
    conn.commit()
    return tuple(conn.execute('SELECT (SELECT count(*) FROM stock_daily),(SELECT count(*) FROM stock_etf_daily)').fetchone())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(); stage=Path(args.stage).resolve();output=Path(args.output).resolve()
    if output==stage or not stage.is_file(): raise ValueError('invalid input or output')
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    try:
        db=sqlite3.connect(output, uri=True)
        try:
            db.execute('ATTACH DATABASE ? AS src',(stage.as_uri()+'?mode=ro',))
            print('stock_daily, stock_etf_daily:',expand(db))
        finally: db.close()
    except BaseException:
        output.unlink(missing_ok=True)
        raise


if __name__=='__main__': main()
