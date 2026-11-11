"""Read-only observation coverage; successful fetch is not completeness proof."""
import argparse
import json
import sqlite3
from pathlib import Path
from . import storage
from .providers import valid_date


def report(db,start,end):
    calendar=db.execute('SELECT COUNT(*) FROM calendar WHERE trade_date BETWEEN ? AND ?',(start,end)).fetchone()[0]
    jobs=[dict(r) for r in db.execute('''SELECT dataset,status,COUNT(*) AS jobs,SUM(rows_count) AS rows
       FROM jobs WHERE dataset IN ('shares','shares-sh-money','shares-sz','shares-sz-verified') AND job_key BETWEEN ? AND ? GROUP BY dataset,status ORDER BY dataset,status''',(start,end))]
    missing={}
    for dataset in ('shares','shares-sh-money','shares-sz','shares-sz-verified'):
        missing[dataset]=[r[0] for r in db.execute('''SELECT c.trade_date FROM calendar c LEFT JOIN jobs j ON j.dataset=? AND j.job_key=c.trade_date AND j.status='success' WHERE c.trade_date BETWEEN ? AND ? AND j.job_key IS NULL ORDER BY c.trade_date''',(dataset,start,end))]
    funds=[dict(r) for r in db.execute('SELECT market,COUNT(*) AS observed,SUM(current_seen) AS current_seen FROM funds GROUP BY market ORDER BY market')]
    nav_jobs=[dict(r) for r in db.execute("SELECT dataset,status,COUNT(*) AS jobs FROM jobs WHERE dataset LIKE 'nav%' GROUP BY dataset,status ORDER BY dataset,status")]
    return {'start':start,'end':end,'calendar_days_observed':calendar,'jobs':jobs,'missing_or_unsuccessful_dates':missing,'funds':funds,'nav_jobs':nav_jobs,'note':'日历来自来源观测；成功仅代表响应通过校验并入库，不证明交易日历、基金列表或基金当日覆盖完整；上市和退市状态可能未知。'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--start',default='2020-12-31');p.add_argument('--end',default='2026-10-30');a=p.parse_args()
    start,end=valid_date(a.start),valid_date(a.end)
    if start>end:p.error('start after end')
    path=Path(__import__('os').environ.get('ETF_HISTORY_DB_PATH',str(storage.DEFAULT_PATH))).resolve(strict=True)
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:print(json.dumps(report(db,start,end),ensure_ascii=False,indent=2))
    finally:db.close()
if __name__=='__main__':main()
