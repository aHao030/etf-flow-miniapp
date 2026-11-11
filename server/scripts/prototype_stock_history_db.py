"""Create a disposable application DB with quality-gated older history.

Existing app records always win; this is a capacity/consistency prototype, not
a production migration. Historical intensity is unknown and remains NULL.
"""
import argparse
import os
from pathlib import Path
import sqlite3


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('app','factors','expanded','output'): ap.add_argument('--'+name,required=True)
    args=ap.parse_args(); output=Path(args.output).resolve()
    inputs=[Path(getattr(args,n)).resolve() for n in ('app','factors','expanded')]
    if output in inputs or not all(x.is_file() for x in inputs): raise ValueError('invalid inputs/output')
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    try:
        src=sqlite3.connect(inputs[0].as_uri()+'?mode=ro',uri=True); db=sqlite3.connect(output,uri=True)
        try:
            src.backup(db);src.close()
            db.execute('ATTACH DATABASE ? AS fx',(inputs[1].as_uri()+'?mode=ro',))
            db.execute('ATTACH DATABASE ? AS ex',(inputs[2].as_uri()+'?mode=ro',))
            end=db.execute("SELECT value FROM meta WHERE key='computed_date'").fetchone()[0]
            if db.execute("SELECT value FROM fx.metadata WHERE key='end'").fetchone()[0]!=end or db.execute("SELECT value FROM ex.metadata WHERE key='end'").fetchone()[0]!=end:
                raise ValueError('source computed dates do not agree')
            mismatch=db.execute('''SELECT COUNT(*) FROM (
                SELECT fund_code,stock_code,weight FROM weights_used WHERE fund_code IN (SELECT fund_code FROM etf_meta WHERE status='active' AND market='SH')
                EXCEPT SELECT fund_code,stock_code,weight FROM fx.weights)''').fetchone()[0]
            reverse=db.execute('''SELECT COUNT(*) FROM (
                SELECT fund_code,stock_code,weight FROM fx.weights EXCEPT
                SELECT fund_code,stock_code,weight FROM weights_used WHERE fund_code IN (SELECT fund_code FROM etf_meta WHERE status='active' AND market='SH'))''').fetchone()[0]
            if mismatch or reverse: raise ValueError('active weights changed during stage')
            overlap=db.execute('''SELECT COUNT(*),SUM(CASE WHEN e.stock_code IS NULL OR ABS(a.flow_yuan-e.flow_yuan)>0.1 THEN 1 ELSE 0 END)
                FROM flow_daily a LEFT JOIN ex.stock_daily e USING(stock_code,trade_date)''').fetchone()
            if not overlap[0] or overlap[1]: raise ValueError('existing model days disagree with audited history')
            cutoff=db.execute('SELECT MIN(trade_date) FROM flow_daily').fetchone()[0]
            if not cutoff: raise ValueError('missing application history')
            with db:
                db.execute('INSERT INTO flow_daily SELECT d.trade_date,d.stock_code,COALESCE(m.stock_name,d.stock_code),d.flow_yuan,d.n_etf FROM ex.stock_daily d LEFT JOIN stock_meta m USING(stock_code) WHERE d.trade_date<?',(cutoff,))
                db.execute('INSERT INTO flow_derived(trade_date,stock_code,flow_1d_yuan) SELECT trade_date,stock_code,flow_yuan FROM ex.stock_daily WHERE trade_date<?',(cutoff,))
                db.execute('''INSERT INTO flow_by_etf(trade_date,fund_code,stock_code,stock_name,weight,delta_shares,nav_used,contribution_yuan,weight_source,coverage_pct)
                  SELECT x.trade_date,x.fund_code,x.stock_code,w.stock_name,w.weight,f.delta_shares,f.previous_nav,x.contribution_yuan,w.weight_source,w.coverage_pct
                  FROM ex.stock_etf_daily x JOIN fx.weights w USING(fund_code,stock_code) JOIN fx.factors f USING(fund_code,trade_date)
                  WHERE x.trade_date<?''',(cutoff,))
            print('cutoff',cutoff,'flow_daily',db.execute('SELECT COUNT(*) FROM flow_daily').fetchone()[0], 'contributions',db.execute('SELECT COUNT(*) FROM flow_by_etf').fetchone()[0])
        finally: db.close()
    except BaseException:
        output.unlink(missing_ok=True)
        raise


if __name__=='__main__':main()
