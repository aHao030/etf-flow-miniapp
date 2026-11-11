"""One explicitly selected failed-501 window probe after the persisted cooldown.

Never loops, changes hosts, retries a successful window, or bypasses access denial.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path

import collect_stock_prices as p
import stock_price_batch as batch


def probe_once(args):
    universe,fingerprint=batch.read_universe(args.universe_file)
    target=universe['target_end'];latest,_=p.read_catalog()
    stock=next((s for s in universe['stocks'] if s['stock_code']==args.code),None)
    if not stock or target>latest or (args.code,args.year) not in batch.plan_windows([stock],'1990-01-01',target):
        raise ValueError('probe must belong to the verified listed universe and closed target')
    checkpoint=args.checkpoint or args.cache_root/'batches'/f'{fingerprint[:16]}-{target}.sqlite'
    result={'schema':1,'generated_at':batch.utcnow(),'stock_code':args.code,'window':args.year,
            'target_end':target,'source':p.ENDPOINT,'network_requests':0,'status':'not_started'}
    with batch.batch_lock(args.cache_root),closing(batch.open_checkpoint(checkpoint,fingerprint,target)) as db:
        state=dict(db.execute('SELECT key,value FROM meta'))
        now=datetime.now(timezone.utc)
        if state.get('blocked_until') and p.stamp(state['blocked_until'])>now:
            result.update(status='source_backoff',blocked_until=state['blocked_until'])
        else:
            old=p.read_json(args.cache_root/'prices/v1'/args.code/f'{args.year}.json')
            through=min(target,f'{args.year+4}-12-31')
            attempt=p.read_json(args.cache_root/'prices/attempts/v1'/args.code/f'{args.year}.json')
            if p.valid_cache(old,args.code,args.year) and old['checked_through']>=through:
                result['status']='already_cached'
            elif not isinstance(attempt,dict) or not str(attempt.get('reason','')).startswith('price source returned HTTP 501'):
                result['status']='not_a_failed_501_window'
            elif (now-p.stamp(attempt['attempted_at'])).total_seconds()<900:
                result['status']='window_backoff'
            else:
                def fetch(code,year,end):
                    result['network_requests']+=1
                    return p.fetch_daily_window(code,year,end)
                item=p.refresh_window(args.cache_root,args.code,args.year,target,fetcher=fetch)
                result.update(status=item['status'],result=item)
                with db:
                    db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?)',
                               (args.code,args.year,item['status'],batch.utcnow(),json.dumps(item,separators=(',',':'))))
                if item['status']=='success':
                    with db:db.execute("DELETE FROM meta WHERE key IN ('blocked_until','blocked_reason')")
                elif batch.source_cooldown_seconds(item.get('reason','')):
                    result['blocked_until']=batch.record_source_stop(db,item['reason'])
    p.atomic_json(args.report,result)
    print(json.dumps(result,ensure_ascii=True),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--universe-file',type=Path,required=True)
    parser.add_argument('--code',required=True)
    parser.add_argument('--year',type=int,required=True)
    parser.add_argument('--cache-root',type=Path,default=p.ROOT/'price-cache')
    parser.add_argument('--checkpoint',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    probe_once(parser.parse_args())


if __name__=='__main__':main()
