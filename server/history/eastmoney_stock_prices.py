"""Independent, unadjusted full-IPO daily prices; never merge other providers."""
from datetime import date
import logging
import math

SOURCE='https://push2his.eastmoney.com/api/qt/stock/kline/get'
MAX_ROWS=12000
MAX_BYTES=2*1024*1024


def market_id(market):
    if market not in ('SH','SZ','BJ'):raise ValueError('unsupported exchange')
    return 1 if market=='SH' else 0


def validate_candles(candles,listed,target):
    if not isinstance(candles,list) or len(candles)>MAX_ROWS:raise ValueError('invalid candle count')
    previous=''
    for row in candles:
        if not isinstance(row,list) or len(row)!=7:raise ValueError('invalid OHLCV row shape')
        day=row[0]
        if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day or not listed<=day<=target or day<=previous:
            raise ValueError('invalid source date or order')
        previous=day
        if any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) for x in row[1:]):
            raise ValueError(f'invalid numeric observation at {day}')
        op,close,high,low,volume,amount=row[1:]
        if min(op,close,high,low)<=0 or max(op,close,high,low)>=1e8 or volume<0 or amount<0:
            raise ValueError(f'invalid price or volume at {day}')
        if not low<=min(op,close)<=max(op,close)<=high:raise ValueError(f'inconsistent source OHLC at {day}')


def parse_range(body,stock,start,target):
    if not isinstance(body,dict) or type(body.get('rc')) is not int or body['rc']!=0:raise ValueError('source response not successful')
    data=body.get('data')
    if not isinstance(data,dict) or data.get('code')!=stock['stock_code'] or type(data.get('market')) is not int or data['market']!=market_id(stock['market']):
        raise ValueError('source security identity mismatch')
    lines=data.get('klines');total=data.get('dktotal')
    if not isinstance(lines,list) or len(lines)>MAX_ROWS or type(total) is not int or not 0<=total<=MAX_ROWS:
        raise ValueError('invalid reported source total')
    candles=[]
    for line in lines:
        if not isinstance(line,str):raise ValueError('invalid kline format')
        fields=line.split(',')
        if len(fields)<7:raise ValueError('invalid kline fields')
        try:nums=[float(x) for x in fields[1:7]]
        except ValueError as exc:raise ValueError(f'invalid numeric observation at {fields[0][:10]}') from exc
        candles.append([fields[0],*nums])
    validate_candles(candles,start,target)
    return {'provider_total':total,'source_name':data.get('name'),'candles':candles,
            'series':[[r[0],r[2]] for r in candles if r[5]>0]}


def parse_payload(body,stock,target):
    bundle=parse_range(body,stock,stock['listed_date'],target)
    if bundle['provider_total']!=len(bundle['candles']):
        raise ValueError('source total mismatch or possible truncation')
    return bundle


def fetch_history(stock,target):
    from scrapling.fetchers import Fetcher
    # This fixed anonymous query selector is from the installed AKShare public
    # endpoint adapter, not a user credential. Never include query strings in logs.
    params={'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116',
            'ut':'7eea3edcaed734bea9cbfc24409ed989','klt':'101','fqt':'0',
            'secid':str(market_id(stock['market']))+'.'+stock['stock_code'],
            'beg':stock['listed_date'].replace('-',''),'end':target.replace('-','')}
    logging.getLogger('scrapling').setLevel(logging.WARNING)
    response=Fetcher.get(SOURCE,params=params,timeout=30,retries=0,follow_redirects=False,stealthy_headers=False)
    if response.status!=200:raise RuntimeError(f'eastmoney returned HTTP {response.status}')
    if len(response.body)>MAX_BYTES:raise ValueError('source response exceeds 2 MiB')
    return parse_payload(response.json(),stock,target)


def make_record(stock,target,bundle,fetched_at):
    return {'schema':1,'provider':'eastmoney','stock_code':stock['stock_code'],
            'stock_name':stock['stock_name'],'market':stock['market'],'source':SOURCE,
            'source_name':bundle.get('source_name'),'adjustment':'none','fqt':0,'klt':101,
            'price_unit':'元/股','listed_date':stock['listed_date'],'checked_through':target,
            'fetched_at':fetched_at,'provider_total':bundle['provider_total'],
            'columns':['date','open','close','high','low','provider_volume','provider_amount'],
            'volume_unit':'provider_raw_unverified','amount_unit':'provider_raw_unverified',
            'series':bundle['series'],'candles':bundle['candles']}


def valid_record(record,stock):
    try:
        if not isinstance(record,dict) or type(record.get('schema')) is not int or record['schema']!=1:
            return False
        if any(record.get(k)!=v for k,v in {'provider':'eastmoney','stock_code':stock['stock_code'],
            'market':stock['market'],'listed_date':stock['listed_date'],'source':SOURCE,'adjustment':'none',
            'price_unit':'元/股','fqt':0,'klt':101}.items()):return False
        if type(record['fqt']) is not int or type(record['klt']) is not int:return False
        if not isinstance(record.get('stock_name'),str) or not record['stock_name'].strip():return False
        target=date.fromisoformat(record['checked_through']).isoformat()
        if target!=record['checked_through'] or target<stock['listed_date']:return False
        from datetime import datetime
        if datetime.fromisoformat(record['fetched_at'].replace('Z','+00:00')).tzinfo is None:return False
        validate_candles(record['candles'],stock['listed_date'],target)
        if type(record['provider_total']) is not int or record['provider_total']!=len(record['candles']):return False
        if record.get('columns')!=['date','open','close','high','low','provider_volume','provider_amount'] or record.get('volume_unit')!='provider_raw_unverified' or record.get('amount_unit')!='provider_raw_unverified':return False
        if not isinstance(record.get('series'),list) or not all(isinstance(r,list) and len(r)==2 and isinstance(r[0],str) and not isinstance(r[1],bool) and isinstance(r[1],(int,float)) and math.isfinite(r[1]) for r in record['series']):return False
        return record.get('series')==[[r[0],r[2]] for r in record['candles'] if r[5]>0]
    except (KeyError,ValueError,TypeError,AttributeError):return False
