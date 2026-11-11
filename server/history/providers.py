"""Validated public-source ETF history collection."""
import io, json, math, re, threading, time, urllib.parse, urllib.request, warnings
from datetime import date, datetime
import akshare as ak
import config
import fetchers
import openpyxl

CODE=re.compile(r'^\d{6}$')
_money_lock=threading.Lock()
_money_next=0.0

def money_get(url):
    """Rate-limit every network attempt across all currency-provider threads."""
    global _money_next
    last=None
    for attempt in range(config.FETCH_RETRIES):
        try:
            req=urllib.request.Request(url,headers=config.SSE_HEADERS)
            with _money_lock:
                wait=_money_next-time.monotonic()
                if wait>0: time.sleep(wait)
                _money_next=time.monotonic()+0.35
                resp=urllib.request.urlopen(req,timeout=config.HTTP_TIMEOUT)
            with resp:
                return resp.read().decode('utf-8','strict')
        except Exception as exc:
            last=exc
            if attempt<config.FETCH_RETRIES-1: time.sleep(config.FETCH_BACKOFF*(attempt+1))
    raise RuntimeError(f'SSE money request failed: {type(last).__name__}') from last

def valid_date(value):
    text=str(value)
    try:
        parsed=date.fromisoformat(text)
    except (TypeError,ValueError):
        raise ValueError(f'invalid date: {text!r}')
    if parsed.isoformat()!=text:
        raise ValueError(f'invalid ISO date: {text!r}')
    return text

def code(value):
    text=str(value).strip()
    if not CODE.fullmatch(text): raise ValueError(f'invalid fund code: {text!r}')
    return text

def number(value, *, positive=False):
    try: n=float(str(value).replace(',','').strip())
    except (TypeError,ValueError): raise ValueError(f'invalid numeric value: {value!r}')
    if not math.isfinite(n) or n < 0 or (positive and n == 0): raise ValueError(f'invalid numeric value: {value!r}')
    return n

def dates():
    frame=ak.tool_trade_date_hist_sina()
    values=[valid_date(x) for x in frame['trade_date']]
    if len(values)!=len(set(values)) or not values: raise ValueError('empty or duplicate calendar')
    return sorted(values)

def _sse_shares(day,sql_id,get):
    day=valid_date(day)
    params={'isPagination':'true','pageHelp.pageSize':'10000','pageHelp.pageNo':'1','pageHelp.beginPage':'1','pageHelp.cacheSize':'1','pageHelp.endPage':'1','sqlId':sql_id,'STAT_DATE':day}
    text=get(config.SSE_URL+'?'+urllib.parse.urlencode(params))
    j=json.loads(text)
    raw=j.get('result'); page=j.get('pageHelp') or {}
    if not isinstance(raw,list) or not raw: raise ValueError('empty SSE response on calendar day')
    if page.get('total') is None or int(page['total']) != len(raw): raise ValueError('incomplete SSE pagination')
    seen=set(); out=[]
    for item in raw:
        c=code(item.get('SEC_CODE')); d=valid_date(item.get('STAT_DATE'))
        if d!=day or c in seen: raise ValueError(f'unexpected date or duplicate SSE fund {c}: {d}')
        seen.add(c)
        name=str(item.get('SEC_NAME') or '').strip()
        if not name: raise ValueError(f'empty fund name: {c}')
        out.append((c,name,d,number(item.get('TOT_VOL'))*10000))
    return out

def sse_shares(day):
    return _sse_shares(day,'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L',lambda url: fetchers.http_get(url,config.SSE_HEADERS))

def sse_currency_shares(day):
    return _sse_shares(day,'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_JYXJJ_SEARCH_L',money_get)

def sz_catalog():
    rows=fetchers.fetch_szse_fund_scale(); out=[]; seen=set()
    for r in rows:
        typ=str(r.get('基金类别') or '').strip()
        if typ != 'ETF': continue
        c=code(r.get('基金代码')); name=str(r.get('基金简称') or '').strip()
        if not name or c in seen: raise ValueError(f'invalid SZSE ETF catalog entry {c}')
        seen.add(c)
        raw=r.get('上市日期'); listing=valid_date(str(raw)[:10]) if raw else None
        out.append((c,name,str(r.get('投资类别') or '').strip(),listing))
    if not out: raise ValueError('SZSE ETF catalog empty or changed schema')
    return out

def sz_shares(start,end,valid_days):
    start=valid_date(start); end=valid_date(end)
    params={'SHOWTYPE':'xlsx','CATALOGID':'scsj_fund_jjgm','TABKEY':'tab1','txtStart':start,'txtEnd':end,'jjlb':'ETF','random':'0.123'}
    content=fetchers.http_bytes('https://www.szse.cn/api/report/ShowReport?'+urllib.parse.urlencode(params),headers={'Referer':'https://www.szse.cn/market/fund/volume/etf/index.html','User-Agent':config.UA})
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',UserWarning)
        workbook=openpyxl.load_workbook(io.BytesIO(content),read_only=True,data_only=True)
    sheet=workbook.active
    # This exchange workbook declares A1:A1 despite containing many rows.
    sheet.reset_dimensions()
    iterator=sheet.iter_rows(values_only=True)
    headers=next(iterator,None)
    if not headers: raise ValueError('empty SZSE workbook')
    headers=[str(x or '').strip() for x in headers]
    required=('日期','基金代码','基金简称','基金规模(份)')
    if not all(x in headers for x in required): raise ValueError('unexpected SZSE workbook columns')
    out={}; seen=set()
    for values in iterator:
        if not any(v is not None for v in values): continue
        r=dict(zip(headers,values))
        day=valid_date(r.get('日期')); c=code(r.get('基金代码'))
        if not start<=day<=end or day not in valid_days or (day,c) in seen:
            raise ValueError(f'invalid or duplicate SZSE row: {day} {c}')
        seen.add((day,c)); name=str(r.get('基金简称') or '').strip()
        if not name: raise ValueError(f'empty SZSE fund name: {c}')
        out.setdefault(day,[]).append((c,name,day,number(r.get('基金规模(份)'))))
    workbook.close()
    if not out: raise ValueError('empty SZSE response')
    return out

