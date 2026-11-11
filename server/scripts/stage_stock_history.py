"""Build an isolated, quality-gated ETF daily-factor staging database.

The source databases are read-only. Output must be a new file; no application
database or input CSV is changed. Values use today's explicitly supplied stock
weights and must be described as retrospective estimates, never PIT holdings.
"""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_queries


def ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('BEGIN')
    return db


def stage(app, history, target, end):
    active = app.execute("SELECT fund_code FROM etf_meta WHERE status='active' AND market='SH' ORDER BY fund_code").fetchall()
    codes = [r['fund_code'] for r in active]
    if not codes or len(codes) != len(set(codes)):
        raise ValueError('active SH pool is empty or duplicated')
    if app.execute("SELECT value FROM meta WHERE key='computed_date'").fetchone()[0] != end:
        raise ValueError('stage end must equal computed application date')
    calendar = [r[0] for r in history.execute('SELECT trade_date FROM calendar WHERE trade_date<=? ORDER BY trade_date', (end,))]
    if not calendar or calendar[-1] != end or len(calendar) != len(set(calendar)):
        raise ValueError('target date missing from history calendar')
    previous = dict(zip(calendar[1:], calendar[:-1]))
    target.executescript('''CREATE TABLE factors(fund_code TEXT, trade_date TEXT, quality TEXT NOT NULL, delta_shares REAL, previous_nav REAL, etf_flow REAL, PRIMARY KEY(fund_code,trade_date));
CREATE TABLE weights(fund_code TEXT, stock_code TEXT, stock_name TEXT, weight REAL, weight_source TEXT, weight_quarter TEXT, coverage_pct REAL, PRIMARY KEY(fund_code,stock_code));
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);''')
    target.executemany('INSERT INTO metadata VALUES(?,?)', [('end',end),('history_basis','current_weights'),('quality_rule','history_queries.history_rows')])
    for code in codes:
        weights = [tuple(r) for r in app.execute('SELECT fund_code,stock_code,stock_name,weight,weight_source,weight_quarter,coverage_pct FROM weights_used WHERE fund_code=?', (code,))]
        if not weights or any(not isinstance(w[3], (float,int)) or not math.isfinite(w[3]) or w[3] <= 0 for w in weights):
            raise ValueError('missing or invalid weights for '+code)
        target.executemany('INSERT INTO weights VALUES(?,?,?,?,?,?,?)', weights)
        rows = history_queries.history_rows(history, code, end)
        by_day = {r[0]:r for r in rows}
        for day in calendar:
            if day < history_queries.TARGET_START:
                continue
            row = by_day.get(day)
            quality = row[5] if row else 'missing_observation'
            delta = old_nav = value = None
            if quality == 'estimated':
                prev = previous.get(day)
                old = history.execute('SELECT shares FROM shares WHERE fund_code=? AND trade_date=?', (code,prev)).fetchone()
                nav = history.execute('SELECT unit_nav FROM nav WHERE fund_code=? AND trade_date=?', (code,prev)).fetchone()
                if not old or not nav or row[1] is None:
                    raise ValueError('estimated factor missing its underlying inputs')
                delta = float(row[1]) - float(old[0]); old_nav = float(nav[0]); value = delta * old_nav
                if not all(math.isfinite(v) for v in (delta,old_nav,value)) or old_nav <= 0:
                    raise ValueError('invalid factor input')
            target.execute('INSERT INTO factors VALUES(?,?,?,?,?,?)', (code,day,quality,delta,old_nav,value))
    target.commit()
    return {'funds':len(codes),'estimated':target.execute("SELECT count(*) FROM factors WHERE quality='estimated'").fetchone()[0]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-db',required=True); parser.add_argument('--history-db',required=True)
    parser.add_argument('--end',required=True); parser.add_argument('--output',required=True)
    args=parser.parse_args(); output=Path(args.output).resolve()
    if output in (Path(args.app_db).resolve(),Path(args.history_db).resolve()):
        raise ValueError('output cannot replace an input')
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    try:
        app, history, dest = ro(args.app_db), ro(args.history_db), sqlite3.connect(output)
        try: print(stage(app,history,dest,args.end))
        finally: app.close();history.close();dest.close()
    except BaseException:
        output.unlink(missing_ok=True)
        raise


if __name__=='__main__': main()
