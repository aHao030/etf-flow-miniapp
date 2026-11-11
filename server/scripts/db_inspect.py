import sqlite3
c = sqlite3.connect(r'etf-flow-miniapp\server\data\app.db')
print('== flow_derived top5 10-30 ==')
for r in c.execute("SELECT stock_code, flow_1d_yuan FROM flow_derived WHERE trade_date='2026-10-30' ORDER BY flow_1d_yuan DESC LIMIT 5"):
    print(' ', r)
print('== rank_daily 1d all-market top5 10-30 ==')
for r in c.execute("SELECT rank, stock_code, flow FROM rank_daily WHERE trade_date='2026-10-30' AND period='1d' AND industry_code='' ORDER BY rank LIMIT 5"):
    print(' ', r)
print('== rank_daily industry counts (1d, 10-30) ==')
for r in c.execute("SELECT industry_code, COUNT(*) AS n FROM rank_daily WHERE trade_date='2026-10-30' AND period='1d' GROUP BY industry_code ORDER BY n DESC LIMIT 6"):
    print(' ', r)
print('== flow_derived distinct dates ==')
print(' ', c.execute("SELECT COUNT(DISTINCT trade_date) FROM flow_derived").fetchone())
print('== rank_daily distinct dates ==')
print(' ', c.execute("SELECT COUNT(DISTINCT trade_date) FROM rank_daily").fetchone())
