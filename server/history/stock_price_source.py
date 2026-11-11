"""Bounded, identity-verified unadjusted Tencent daily closing prices.

The realtime quote node is used only for identity. Its price is never used.
Five-calendar-year windows stay below the provider's 2,000-row response cap.
"""
from datetime import date
import math
import re

ENDPOINT = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
CODE = re.compile(r'^\d{6}$')


def symbol_for(code):
    if not isinstance(code, str) or not CODE.fullmatch(code):
        raise ValueError('invalid stock code')
    if code.startswith(('4', '8', '92')):
        return 'bj' + code
    if code.startswith(('6', '9')):
        return 'sh' + code
    if code.startswith(('0', '3')):
        return 'sz' + code
    raise ValueError('unsupported stock market')


def window_bounds(year, end):
    if not isinstance(year, int) or year < 1990 or year > 2100 or year % 5:
        raise ValueError('invalid five-year window')
    end_date = date.fromisoformat(end)
    start = date(year, 1, 1)
    finish = min(date(year + 4, 12, 31), end_date)
    if finish < start:
        raise ValueError('window is beyond target date')
    return start.isoformat(), finish.isoformat()


def parse_candles(payload, code, start, end):
    """Return verified [date, open, close, high, low, provider_volume]."""
    symbol = symbol_for(code)
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last - first).days >= 5 * 366:
        raise ValueError('price request must fit within five years')
    if not isinstance(payload, dict) or isinstance(payload.get('code'), bool) or payload.get('code') != 0:
        raise ValueError('source did not return success')
    data = payload.get('data')
    if not isinstance(data, dict) or set(data) != {symbol}:
        raise ValueError('source symbol mismatch')
    node = data[symbol]
    if not isinstance(node, dict) or 'qfqday' in node or 'hfqday' in node:
        raise ValueError('adjusted or invalid price payload')
    quote = node.get('qt', {}).get(symbol) if isinstance(node.get('qt'), dict) else None
    if not isinstance(quote, list) or len(quote) < 3 or str(quote[2]) != code:
        raise ValueError('source quote identity mismatch')
    bars = node.get('day')
    if not isinstance(bars, list) or len(bars) > 1500:
        raise ValueError('source prices missing or possibly truncated')
    result, seen = [], set()
    for bar in bars:
        if not isinstance(bar, list) or len(bar) < 6:
            raise ValueError('invalid daily price row')
        day = bar[0]
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day or not start <= day <= end:
            raise ValueError('source price date outside requested window')
        if day in seen:
            raise ValueError('duplicate source price date')
        seen.add(day)
        if any(isinstance(value, bool) for value in bar[1:6]):
            raise ValueError(f'invalid numeric price field at {day}')
        try:
            op, close, high, low, volume = map(float, bar[1:6])
        except (TypeError,ValueError) as exc:
            raise ValueError(f'invalid numeric price field at {day}') from exc
        if not all(math.isfinite(x) for x in (op, close, high, low, volume)) or min(op, close, high, low) <= 0 or max(op, close, high, low) >= 1e8 or volume < 0:
            raise ValueError(f'non-positive or non-finite source price at {day}')
        if not low <= min(op, close) <= max(op, close) <= high:
            raise ValueError(f'inconsistent source OHLC at {day}')
        result.append([day, op, close, high, low, volume])
    return sorted(result)


def parse_payload(payload, code, start, end):
    # A zero-volume source row is not a fresh trade; keep a chart gap.
    return [[r[0], r[2]] for r in parse_candles(payload, code, start, end) if r[5] > 0]


def fetch_daily_window(code, year, end):
    start, finish = window_bounds(year, end)
    symbol = symbol_for(code)
    params = {'param': f'{symbol},day,{start},{finish},2000,'}
    # Prefer the user's installed Scrapling HTTP collector. No challenge solving,
    # impersonation, alternate host, proxy rotation, or certificate weakening.
    from scrapling.fetchers import Fetcher
    response = Fetcher.get(ENDPOINT, params=params, timeout=25, retries=0,
                           follow_redirects=False, stealthy_headers=False)
    if response.status != 200:
        raise RuntimeError(f'price source returned HTTP {response.status}')
    candles = parse_candles(response.json(), code, start, finish)
    return {'series': [[r[0], r[2]] for r in candles if r[5] > 0], 'candles': candles}


def fetch_window(code, year, end):
    return fetch_daily_window(code, year, end)['series']
