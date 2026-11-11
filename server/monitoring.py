"""Read-only ETF movement views over existing model outputs."""
import calendar
import csv
import os
import sqlite3
from datetime import date
from functools import lru_cache
from pathlib import Path
import db
import config

HISTORY_NOTE = '历史数据按当前权重回溯估算，可能使用当时尚未披露的信息，不适用于历史策略回测。'
LONG_MONTHS = {'3m':3,'6m':6,'1y':12,'2y':24,'3y':36,'5y':60}
SHORT_DAYS = {'1d':1,'5d':5,'20d':20}

@lru_cache(maxsize=2)
def _catalog(path, mtime):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return {r['stock_code']: r for r in csv.DictReader(f) if r.get('stock_code')}

def catalog():
    try:
        return _catalog(config.INDUSTRY_CSV, os.stat(config.INDUSTRY_CSV).st_mtime_ns)
    except FileNotFoundError:
        return {}

def global_coverage():
    row = db.query_one("SELECT COUNT(*) AS total, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active FROM etf_meta")
    total, active = row['total'], row['active'] or 0
    return {'tracked_etfs': total, 'active_etfs': active, 'pending_etfs': total-active}

def _observed_dates(td):
    rows=db.query('SELECT trade_date FROM etf_share_daily WHERE trade_date<=? UNION SELECT nav_date AS trade_date FROM etf_nav_daily WHERE nav_date<=? ORDER BY trade_date',(td,td))
    return [r['trade_date'] for r in rows]

def _subtract_months(day, months):
    offset=day.year*12+day.month-1-months
    year, month=divmod(offset,12)
    month+=1
    return date(year,month,min(day.day,calendar.monthrange(year,month)[1]))

def calendar_snapshot(td):
    """Read calendar only; failures fall back to observed model dates."""
    path=Path(config.INPUTS_DIR)/'trading_calendar.csv'
    try:
        if path.exists():
            with path.open(encoding='utf-8-sig',newline='') as f:
                days=sorted({date.fromisoformat(r['trade_date']).isoformat() for r in csv.DictReader(f) if r['trade_date']<=td})
                if days and days[-1]==td: return (days,True,'input_csv')
        history=Path(os.environ.get('ETF_HISTORY_DB_PATH',str(Path(config.DATA_DIR)/'market_history.db')))
        if history.exists():
            c=sqlite3.connect(history.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
            try:
                rows=c.execute('SELECT trade_date,source FROM calendar WHERE trade_date<=? ORDER BY trade_date',(td,)).fetchall()
                if rows and rows[-1][0]==td and all(source for _,source in rows):
                    return ([date.fromisoformat(day).isoformat() for day,_ in rows],True,'history_calendar')
            finally:c.close()
    except (OSError,sqlite3.Error,ValueError,KeyError):
        pass
    return (_observed_dates(td),False,'observed_inputs')

def window_for(td,period,calendar_data=None):
    """Window metadata plus observed dates; long start is an excluded anchor."""
    if period not in SHORT_DAYS and period not in LONG_MONTHS:
        raise ValueError('unsupported period')
    end=date.fromisoformat(td)
    data=calendar_data if calendar_data is not None else calendar_snapshot(td)
    all_dates,trusted=data[:2]
    source=data[2] if len(data)>2 else ('provided_calendar' if trusted else 'observed_inputs')
    all_dates=[d for d in all_dates if d<=td]
    if period in SHORT_DAYS:
        n=SHORT_DAYS[period]
        if trusted and len(all_dates)<n:
            all_dates=sorted(set(all_dates)|set(_observed_dates(td)))
            trusted=False;source='observed_inputs'
        dates=all_dates[-n:]
        start=dates[0] if dates else td
        complete_calendar=trusted and len(dates)==n and all_dates[-1]==td
        return {'window_start':start,'window_end':td,'start_exclusive':False,
                'window_days':n,'calendar_status':'verified' if complete_calendar else 'observed', 'calendar_source':source,'_dates':dates}
    anchor=_subtract_months(end,LONG_MONTHS[period]).isoformat()
    if trusted and (not all_dates or all_dates[0]>anchor):
        all_dates=sorted(set(all_dates)|set(_observed_dates(td)))
        trusted=False;source='observed_inputs'
    dates=[d for d in all_dates if anchor<d<=td]
    complete_calendar=bool(trusted and all_dates and all_dates[0]<=anchor and all_dates[-1]==td)
    return {'window_start':anchor,'window_end':td,'start_exclusive':True,
            'window_days':len(dates) if complete_calendar else None,
            'calendar_status':'verified' if complete_calendar else 'observed','calendar_source':source,'_dates':dates}

def public_window(td,period):
    return {k:v for k,v in window_for(td,period).items() if not k.startswith('_')}

def stock_movement(code, td, period, derived):
    window=window_for(td,period)
    dates=window['_dates']
    allowed=set(dates)
    first=dates[0] if dates else td
    expected=window['window_days']
    funds=db.query('SELECT w.fund_code, e.fund_name, e.status, w.weight, w.weight_source, w.weight_quarter FROM weights_used w LEFT JOIN etf_meta e ON e.fund_code=w.fund_code WHERE w.stock_code=? ORDER BY w.fund_code',(code,))
    all_rows=db.query('SELECT fund_code,trade_date,contribution_yuan FROM flow_by_etf WHERE stock_code=? AND trade_date>=? AND trade_date<=? AND contribution_yuan IS NOT NULL',(code,first,td)) if dates else []
    grouped={}
    for row in all_rows:
        if row['trade_date'] in allowed: grouped.setdefault(row['fund_code'],[]).append(row)
    contributions=[]
    for f in funds:
        rows=grouped.get(f['fund_code'],[])
        flow=sum(r['contribution_yuan'] for r in rows) if rows else None
        complete=expected is not None and expected>0 and len(rows)==expected and (period in SHORT_DAYS or window['calendar_status']=='verified')
        status='pending' if f['status']!='active' else ('complete' if complete else ('partial' if rows else 'missing'))
        contributions.append({'etf_code':f['fund_code'],'etf_name':f['fund_name'],'flow':round(flow,2) if flow is not None else None,'weight':f['weight'],'weight_source':f['weight_source'],'weight_date':f['weight_quarter'],'valid_days':len(rows),'status':status})
    contributions.sort(key=lambda x:(x['flow'] is None,-abs(x['flow'] or 0),x['etf_code']))
    active=sum(f['status']=='active' for f in funds)
    participating=[f for f in contributions if f['flow'] is not None]
    rows=db.query('SELECT trade_date,flow_yuan FROM flow_daily WHERE stock_code=? AND trade_date>=? AND trade_date<=? AND flow_yuan IS NOT NULL ORDER BY trade_date',(code,first,td)) if dates else []
    daily=[r for r in rows if r['trade_date'] in allowed]
    valid=len(daily)
    observed=sum(r['flow_yuan'] for r in daily) if daily else None
    complete=expected is not None and expected>0 and len(dates)==expected and valid==expected and bool(funds) and all(f['status']=='complete' for f in contributions) and (period in SHORT_DAYS or window['calendar_status']=='verified')
    if period in SHORT_DAYS:
        minimum={1:1,5:3,20:12}[SHORT_DAYS[period]]
        net=observed if valid>=minimum and derived.get('flow_1d_yuan') is not None else None
    else:
        net=observed if complete and derived.get('flow_1d_yuan') is not None else None
    values=[f['flow'] for f in participating]
    show_components=period in SHORT_DAYS or complete
    public={k:v for k,v in window.items() if not k.startswith('_')}
    return {'etf_contributions':contributions,
            'movement':{'period':period,'net_flow':round(net,2) if net is not None else None,
              'observed_net_flow':round(observed,2) if observed is not None else None,
              'inflow':round(sum(x for x in values if x>0),2) if values and show_components else None,
              'outflow':round(sum(x for x in values if x<0),2) if values and show_components else None,
              'etf_count':len(funds),'inflow_etf_count':sum(x>0 for x in values),'outflow_etf_count':sum(x<0 for x in values),
              'missing_etf_count':sum(f['status'] in ('missing','partial','pending') for f in contributions),
              'valid_days':valid,'missing_days':max(0,expected-valid) if expected is not None else None,
              'available_start':daily[0]['trade_date'] if daily else None,'available_end':daily[-1]['trade_date'] if daily else None,
              'complete':complete,**public},
            'coverage':{'tracked_etfs':len(funds),'active_etfs':active,'contributing_etfs':len(participating),'pending_etfs':len(funds)-active,'note':'仅统计项目追踪池中持有该股的 ETF；未纳入的 ETF 不计入。'},
            'history_basis':'current_weights','history_note':HISTORY_NOTE}
