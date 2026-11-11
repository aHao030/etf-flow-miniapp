"""Strictly paginated historical fund NAV from Eastmoney F10."""
from datetime import date
import math
import re
import time
import requests

URL = 'https://api.fund.eastmoney.com/f10/lsjz'
HEADERS = {'Referer': 'https://fundf10.eastmoney.com/', 'User-Agent': 'Mozilla/5.0'}
PAGE_SIZE = 20

class NavError(RuntimeError):
    pass

class UnsupportedNav(NavError):
    pass

def fetch(code, start, end, session=None, delay=0.25):
    if not re.fullmatch(r'\d{6}', code):
        raise NavError('invalid fund code')
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise NavError('invalid date range')
    session = session or requests.Session()
    page, total, size, result, seen = 1, None, None, [], set()
    while True:
        params = {'fundCode':code, 'pageIndex':page, 'pageSize':PAGE_SIZE,
                  'startDate':start, 'endDate':end}
        response = session.get(URL, params=params, headers=HEADERS, timeout=(10,30))
        response.raise_for_status()
        doc = response.json()
        if not isinstance(doc, dict) or doc.get('ErrCode') != 0:
            raise NavError('source returned an error')
        data = doc.get('Data')
        if not isinstance(data, dict):
            raise NavError('empty or unavailable response; coverage not verified')
        if str(data.get('FundType') or '') == '005' or '收益' in str(data.get('SYType') or ''):
            raise UnsupportedNav('money-market yield is not a unit NAV')
        rows = data.get('LSJZList')
        count, psize, pindex = doc.get('TotalCount'), doc.get('PageSize'), doc.get('PageIndex')
        if (not isinstance(rows, list) or not isinstance(count, int) or count <= 0
                or not isinstance(psize, int) or psize <= 0 or pindex != page):
            raise NavError('empty or invalid pagination metadata')
        if total is None:
            total, size = count, psize
        if count != total or psize != size or size > PAGE_SIZE:
            raise NavError('pagination changed during download')
        expected = min(size, total - len(result))
        if expected <= 0 or len(rows) != expected:
            raise NavError('truncated or inconsistent page')
        for row in rows:
            try:
                day = date.fromisoformat(row['FSRQ'])
                if day < first or day > last or day in seen:
                    raise ValueError()
                nav = float(row['DWJZ'])
                acc = None if row.get('LJJZ') in ('', None) else float(row['LJJZ'])
                if not math.isfinite(nav) or nav <= 0 or (acc is not None and (not math.isfinite(acc) or acc <= 0)):
                    raise ValueError()
            except (KeyError, ValueError, TypeError, OverflowError):
                raise NavError('invalid, duplicate, or out-of-range NAV row') from None
            seen.add(day)
            result.append((code, day.isoformat(), nav, acc, 'eastmoney:lsjz'))
        if len(result) == total:
            return result
        page += 1
        if delay:
            time.sleep(delay)

def fetch_bundle(code, start, end, session=None):
    """Parse JSON arrays from provider's JS without executing script."""
    import json
    from datetime import datetime, timezone, timedelta
    if not re.fullmatch(r'\d{6}', code):
        raise NavError('invalid fund code')
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise NavError('invalid date range')
    session = session or requests.Session()
    response = session.get('https://fund.eastmoney.com/pingzhongdata/' + code + '.js',
                           headers=HEADERS, timeout=(10,45))
    response.raise_for_status()
    content = response.content
    if len(content) > 20_000_000:
        raise NavError('source response exceeds size limit')
    text = content.decode('utf-8-sig')
    decoder = json.JSONDecoder()
    identity = list(re.finditer(r'\bvar\s+fS_code\s*=\s*', text))
    if len(identity) != 1:
        raise NavError('source fund identity is missing or ambiguous')
    try:
        returned_code, consumed = decoder.raw_decode(text[identity[0].end():])
    except json.JSONDecodeError:
        raise NavError('invalid source fund identity') from None
    if returned_code != code or not text[identity[0].end()+consumed:].lstrip().startswith(';'):
        raise NavError('source fund identity mismatch')
    def array(name, required):
        matches = list(re.finditer(r'\bvar\s+' + re.escape(name) + r'\s*=\s*', text))
        if len(matches) != 1:
            if required:
                raise NavError('required NAV array missing or ambiguous')
            return None
        try:
            value, offset = decoder.raw_decode(text[matches[0].end():])
        except json.JSONDecodeError:
            raise NavError('invalid JSON NAV array') from None
        suffix = text[matches[0].end()+offset:].lstrip()
        if not isinstance(value, list) or not suffix.startswith(';'):
            raise NavError('invalid NAV array boundary')
        return value
    raw = array('Data_netWorthTrend', False)
    if raw is None:
        if array('Data_millionCopiesIncome', False) is not None:
            raise UnsupportedNav('money-market yield is not a unit NAV')
        raise NavError('source NAV array is missing')
    if not raw:
        raise NavError('empty NAV series; coverage not verified')
    cumulative = array('Data_ACWorthTrend', False)
    tz = timezone(timedelta(hours=8))
    def to_date(ms):
        if not isinstance(ms, int) or isinstance(ms, bool):
            raise NavError('invalid NAV timestamp')
        try:
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz).date()
        except (ValueError, OverflowError, OSError):
            raise NavError('invalid NAV timestamp') from None
    acc_map = {}
    if cumulative is not None:
        for point in cumulative:
            try:
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError()
                day = to_date(point[0])
                value = None if point[1] is None else float(point[1])
                if day in acc_map or (value is not None and (not math.isfinite(value) or value <= 0)):
                    raise ValueError()
                acc_map[day] = value
            except (TypeError, ValueError):
                raise NavError('invalid cumulative NAV data') from None
    result, events, seen = [], [], set()
    for point in raw:
        try:
            day = to_date(point['x'])
            value = float(point['y'])
            if day in seen or not math.isfinite(value) or value <= 0:
                raise ValueError()
            seen.add(day)
        except (KeyError, TypeError, ValueError):
            raise NavError('invalid or duplicate NAV row') from None
        if first <= day <= last:
            result.append((code, day.isoformat(), value, acc_map.get(day), 'eastmoney:pingzhongdata'))
            if 'unitMoney' not in point:
                raise NavError('fund event field is missing')
            event = point.get('unitMoney')
            if not isinstance(event, str):
                raise NavError('invalid fund event description')
            if isinstance(event, str) and event.strip():
                events.append((code, day.isoformat(), event.strip(), 'eastmoney:pingzhongdata'))
    if not result:
        raise NavError('no NAV in requested period; coverage not verified')
    return sorted(result, key=lambda row: row[1]), sorted(events, key=lambda row: row[1])


def fetch_all(code, start, end, session=None):
    """Compatibility wrapper for consumers needing NAV only."""
    return fetch_bundle(code, start, end, session)[0]
