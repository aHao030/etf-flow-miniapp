"""Read-only history access. Observations and estimates retain separate coverage."""
from contextlib import closing
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import os
from pathlib import Path
import sqlite3

TARGET_START = '2004-12-30'
DEFAULT_DB = Path(__file__).parent / 'data' / 'market_history.db'
NOTES = [
    '范围为沪深上市 ETF。目录由交易所历日记录合并，历史出现但当前未出现的产品保留；未核实的退市日期不推断。',
    'ETF 历史目标从境内首只 ETF 成立日 2004-12-30 起；各基金实际可用起点另列，不代表早期份额和净值已经完整。指数历史可以早于 ETF 存在的日期。',
    '展示范围仅保留交易日历中的开市日期；休市日期不占图表位置，缺失的交易日记录仍然留空，原始采集记录不删除。',
    '份额单位为份，单位净值为元；记录条数分别统计，不代表上市期间完整覆盖。缺失不会补零或沿用前值。',
    'ETF 估算净申赎 = 相邻交易日份额差 × 前一交易日单位净值。缺少前日观测或疑似折算时留空；不是实际成交资金。',
    '已处理净值源事件说明；存在事件说明的当日及下一交易日留空。异常阈值和源事件说明仍不是完整的基金事件登记。',
    '已知上市日期时，上市前及跨上市边界的份额变化不估算净申赎，归入基金事件并保留原始观测；上市后也不代表已核验建仓完成。',
    '货币基金份额变化可能包含收益结转，暂不以份额差估算净申赎；保留原始份额和可确认的单位净值。收益指标不作为单位净值。',
    '历史个股资金分配仍需要当时的持仓权重与披露日期，本页不使用今天的持仓伪造历史个股贡献。',
]

class HistoryRequestError(ValueError):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)

def connect():
    path = Path(os.environ.get('ETF_HISTORY_DB_PATH', DEFAULT_DB)).resolve()
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def coverage(rows, field):
    dates = [r[0] for r in rows if r[field] is not None]
    return {'count': len(dates), 'start': min(dates) if dates else None,
            'end': max(dates) if dates else None}

def catalog(conn, end):
    stats = {}
    verified = {r[0].split(':')[0] for r in conn.execute("SELECT job_key FROM jobs WHERE dataset='nav-v2' AND status IN ('success','pending') AND rows_count>0")}
    for table in ('shares', 'nav'):
        for r in conn.execute(f'SELECT fund_code,COUNT(*) n,MIN(trade_date) first,MAX(trade_date) last FROM {table} WHERE trade_date>=? AND trade_date<=? AND trade_date IN (SELECT trade_date FROM calendar) GROUP BY fund_code', (TARGET_START, end)):
            stats.setdefault(r['fund_code'], {})[table] = {'count': r['n'], 'start': r['first'], 'end': r['last']}
    empty = {'count': 0, 'start': None, 'end': None}
    result = []
    for r in conn.execute('SELECT * FROM funds ORDER BY fund_code'):
        row = dict(r)
        row.update({k: stats.get(r['fund_code'], {}).get(k, empty.copy()) for k in ('shares', 'nav')})
        row['flow_status'] = 'partial' if row['shares']['count'] > 1 and row['nav']['count'] and row['fund_code'] in verified else 'unavailable'
        if '货币' in (row['category'] or '') or 'sse_daily_money' in (row['source'] or ''):
            row['flow_status'] = 'unavailable'
        result.append(row)
    return result

def history_rows(conn, code, end):
    """Return compact [date, shares, NAV, acc NAV, estimated flow, quality]."""
    shares = {r['trade_date']: r['shares'] for r in conn.execute('SELECT trade_date,shares FROM shares WHERE fund_code=? AND trade_date<=? ORDER BY trade_date', (code, end))}
    nav = {r['trade_date']: (r['unit_nav'], r['acc_nav']) for r in conn.execute('SELECT trade_date,unit_nav,acc_nav FROM nav WHERE fund_code=? AND trade_date<=? ORDER BY trade_date', (code, end))}
    days = [r[0] for r in conn.execute('SELECT trade_date FROM calendar WHERE trade_date<=? ORDER BY trade_date', (end,))]
    trading_days = set(days)
    previous = dict(zip(days[1:], days[:-1]))
    fund = conn.execute('SELECT category,source,listed_date FROM funds WHERE fund_code=?',(code,)).fetchone()
    listed_date = fund['listed_date'] if fund else None
    money_market = bool(fund and ('货币' in (fund['category'] or '') or 'sse_daily_money' in (fund['source'] or '')))
    verified = []
    for r in conn.execute("SELECT job_key FROM jobs WHERE dataset='nav-v2' AND status IN ('success','pending') AND rows_count>0 AND job_key LIKE ?", (code+':%',)):
        parts = r[0].split(':')
        if len(parts) == 3:
            verified.append((parts[1], parts[2]))
    has_event_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fund_events'").fetchone()
    events = {r[0] for r in conn.execute('SELECT trade_date FROM fund_events WHERE fund_code=? AND trade_date<=?', (code,end))} if has_event_table else set()
    has_verification = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nav_verified'").fetchone()
    verified_dates = {r[0] for r in conn.execute('SELECT trade_date FROM nav_verified WHERE fund_code=? AND trade_date<=?', (code,end))} if has_verification else set()
    result = []
    for day in sorted(set(shares) | set(nav)):
        if day < TARGET_START or day not in trading_days:
            continue
        value = shares.get(day)
        unit, acc = nav.get(day, (None, None))
        prev = previous.get(day)
        old_shares, old_nav = shares.get(prev), nav.get(prev, (None, None))[0]
        if money_market:
            result.append([day,value,unit,acc,None,'money_market'])
            continue
        if value is None:
            result.append([day,value,unit,acc,None,'missing_current_shares'])
            continue
        # Exchange observations may precede listing; do not infer subscriptions
        # across that boundary or infer a listing date from first_seen.
        if listed_date and (day <= listed_date or (prev and prev < listed_date)):
            result.append([day,value,unit,acc,None,'fund_event'])
            continue
        if not prev or not any(first <= prev and day <= last for first,last in verified) or (unit is not None and day not in verified_dates) or (old_nav is not None and prev not in verified_dates):
            result.append([day, value, unit, acc, None, 'events_unverified'])
            continue
        if day in events or prev in events:
            result.append([day, value, unit, acc, None, 'fund_event'])
            continue
        flow, quality = None, 'missing_previous_shares'
        if value is not None and old_shares is not None and old_shares > 0:
            quality = 'missing_previous_nav'
            if old_nav is not None and old_nav > 0:
                if unit is None or unit <= 0:
                    result.append([day, value, unit, acc, None, 'missing_current_nav'])
                    continue
                delta = value / old_shares - 1
                # A conservative anomaly guard, not a complete corporate-action register.
                if abs(delta) > .30 or (unit is not None and abs(unit / old_nav - 1) > .25):
                    quality = 'possible_split'
                else:
                    flow, quality = round((value - old_shares) * old_nav, 2), 'estimated'
        result.append([day, value, unit, acc, flow, quality])
    return result

def metadata(conn, target_end, funds=None):
    funds = catalog(conn, target_end) if funds is None else funds
    jobs = [dict(r) for r in conn.execute('SELECT dataset,status,COUNT(*) jobs FROM jobs GROUP BY dataset,status')]
    expected = conn.execute('SELECT COUNT(*) FROM calendar WHERE trade_date>=? AND trade_date<=?',(TARGET_START,target_end)).fetchone()[0]
    markets = {}
    for r in conn.execute('SELECT f.market,COUNT(DISTINCT s.trade_date) n,MIN(s.trade_date) first,MAX(s.trade_date) last FROM shares s JOIN funds f ON f.fund_code=s.fund_code WHERE s.trade_date>=? AND s.trade_date<=? AND s.trade_date IN (SELECT trade_date FROM calendar) GROUP BY f.market',(TARGET_START,target_end)):
        markets[r['market']] = {'expected_days':expected,'observed_days':r['n'],'start':r['first'],'end':r['last']}
    sources = {}
    for r in conn.execute('SELECT source,COUNT(*) records,COUNT(DISTINCT trade_date) n,MIN(trade_date) first,MAX(trade_date) last FROM shares WHERE trade_date>=? AND trade_date<=? AND trade_date IN (SELECT trade_date FROM calendar) GROUP BY source',(TARGET_START,target_end)):
        sources[r['source']] = {'expected_days':expected,'observed_days':r['n'],'records':r['records'],'start':r['first'],'end':r['last']}
    return dict(target_start=TARGET_START, target_end=target_end,
                generated_at=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
                total_funds=len(funds), shares_funds=sum(f['shares']['count'] > 0 for f in funds),
                nav_funds=sum(f['nav']['count'] > 0 for f in funds),
                shares_rows=sum(f['shares']['count'] for f in funds), nav_rows=sum(f['nav']['count'] for f in funds),
                current_funds=sum(bool(f['current_seen']) for f in funds), market_days=markets, source_days=sources,
                scope='沪深上市ETF', complete=False, jobs=jobs, notes=NOTES)

def target_end(conn):
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    today = now.date().isoformat()
    operator = '<=' if now.hour >= 15 else '<'
    value = conn.execute(f'SELECT MAX(trade_date) FROM calendar WHERE trade_date {operator} ?', (today,)).fetchone()[0]
    if not value:
        raise ValueError('历史交易日历尚未就绪')
    return value

def install_routes(app):
    from fastapi import Query
    from fastapi.responses import JSONResponse
    def response(fn):
        try:
            with closing(connect()) as conn:
                return {'code': 0, 'message': 'ok', 'data': fn(conn)}
        except HistoryRequestError as exc:
            return JSONResponse(status_code=exc.status, content={'code': 40402 if exc.status == 404 else 40001, 'message': str(exc), 'data': None})
        except (sqlite3.Error, ValueError):
            return JSONResponse(status_code=503, content={'code': 50301, 'message': '历史数据未就绪', 'data': None})
    @app.get('/api/v1/history/meta')
    def meta():
        return response(lambda c: metadata(c, target_end(c)))
    @app.get('/api/v1/history/etfs')
    def etfs(q: str = Query('', max_length=50), market: str = Query('', pattern='^(|SH|SZ)$'), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        def run(c):
            rows = [f for f in catalog(c, target_end(c)) if (not market or f['market'] == market) and (q.strip() in f['fund_code'] or q.strip() in f['fund_name'])]
            return {'items': rows[(page-1)*page_size:page*page_size], 'page': page, 'page_size': page_size, 'total': len(rows)}
        return response(run)
    @app.get('/api/v1/history/etf/{fund_code}')
    def fund(fund_code: str, start: str = TARGET_START, end: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100)):
        import re
        def run(c):
            stop = end or target_end(c)
            if not re.fullmatch(r'\d{6}', fund_code) or start < TARGET_START or start > stop or stop > target_end(c):
                raise HistoryRequestError('ETF代码或日期范围非法')
            try:
                if date.fromisoformat(start).isoformat() != start or date.fromisoformat(stop).isoformat() != stop:
                    raise ValueError('date')
            except ValueError:
                raise HistoryRequestError('日期格式非法') from None
            item = next((f for f in catalog(c, target_end(c)) if f['fund_code'] == fund_code), None)
            if item is None:
                raise HistoryRequestError('ETF不存在',404)
            rows = [r for r in history_rows(c, fund_code, stop) if r[0] >= start]
            cov = {k: coverage(rows, i) for k, i in [('shares',1),('nav',2),('flow',4)]}
            names = ['trade_date','shares','unit_nav','acc_nav','estimated_flow','quality']
            return {'fund': item, 'start': start, 'end': stop, 'page': page, 'page_size': page_size, 'total': len(rows), 'coverage': cov, 'items': [dict(zip(names, r)) for r in rows[(page-1)*page_size:page*page_size]], 'notes': NOTES}
        return response(run)
