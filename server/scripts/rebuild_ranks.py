import sqlite3
import sys

sys.path.insert(0, r'etf-flow-miniapp\server')
import db
import compute

db.init()
compute.build_ranks()

c = sqlite3.connect(r'etf-flow-miniapp\server\data\app.db')
print('rank rows:', c.execute("SELECT COUNT(*) FROM rank_daily").fetchone()[0],
      '| dates:', c.execute("SELECT COUNT(DISTINCT trade_date) FROM rank_daily").fetchone()[0])
print('top5 all-market 1d 2026-10-30:')
for r in c.execute("SELECT rank, stock_code, flow FROM rank_daily "
                   "WHERE trade_date='2026-10-30' AND period='1d' "
                   "AND industry_code='' ORDER BY rank LIMIT 5"):
    print(' ', r)
print('电子行业 top3 (1d 10-30):')
for r in c.execute("SELECT rank, stock_code, flow FROM rank_daily "
                   "WHERE trade_date='2026-10-30' AND period='1d' "
                   "AND industry_code='801080' ORDER BY rank LIMIT 3"):
    print(' ', r)
print('历史回看 2026-09-28 全市场 top3:')
for r in c.execute("SELECT rank, stock_code, flow FROM rank_daily "
                   "WHERE trade_date='2026-09-28' AND period='1d' "
                   "AND industry_code='' ORDER BY rank LIMIT 3"):
    print(' ', r)
