"""Atomically select validated snapshots for the read-only local application."""
import argparse
from datetime import datetime, timezone, date
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2] / 'web-trial' / '.local'
VERSION = re.compile(r'^\d{14}-[a-f0-9]{32}$')
HASH = re.compile(r'^[a-f0-9]{64}$')
STOCK_REQUIRED = {'meta', 'catalog', 'ranks', 'industries', 'etfs', 'etf-details'}
HISTORY_REQUIRED = {'meta', 'catalog'}

def generated_time(meta, version):
    raw = meta.get('generated_at')
    if raw is None:
        return datetime.strptime(version[:14], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    if not isinstance(raw, str):
        raise ValueError('Invalid snapshot generation time')
    try:
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Invalid snapshot generation time') from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Snapshot generation time requires a timezone')
    return value.astimezone(timezone.utc)

def verify(folder, version):
    if not VERSION.fullmatch(version):
        raise ValueError('Invalid snapshot version')
    directory = ROOT / folder / version
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    files = manifest.get('files', [])
    hashes = manifest.get('hashes', {})
    if (not isinstance(files, list) or not isinstance(hashes, dict)
            or not files or not all(isinstance(k, str) for k in files)
            or len(files) != len(set(files)) or set(files) != set(hashes)
            or manifest.get('version') != version):
        raise ValueError('Invalid snapshot manifest')
    required = STOCK_REQUIRED if folder == 'snapshots' else {'meta','calendar','catalog','funds','ranks','industries'} if folder == 'market-snapshots' else {'meta','calendar','index-all'} if folder == 'broad-snapshots' else HISTORY_REQUIRED
    if not required.issubset(files) or (folder == 'history-snapshots' and not any(re.fullmatch(r'data-\d{4}', k) for k in files)):
        raise ValueError('Snapshot is missing required files')
    for key in files:
        if not re.fullmatch(r'[a-z0-9-]+', key) or not isinstance(hashes[key], str) or not HASH.fullmatch(hashes[key]):
            raise ValueError('Invalid snapshot filename')
        raw = (directory / (key + '.json')).read_bytes()
        if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != hashes[key]:
            raise ValueError('Snapshot content mismatch')
    meta = json.loads((directory / 'meta.json').read_text(encoding='utf-8'))
    if meta['version'] != version:
        raise ValueError('Snapshot metadata mismatch')
    for key in ('target_start', 'target_end', 'generated_at'):
        if key in manifest and manifest[key] != meta.get(key):
            raise ValueError('Snapshot metadata mismatch')
    if folder == 'snapshots':
        day = meta.get('latest_date', meta.get('trade_date'))
        if 'trade_date' in manifest and manifest['trade_date'] != day:
            raise ValueError('Snapshot metadata mismatch')
        if 'trade_date' in meta and meta['trade_date'] != day:
            raise ValueError('Snapshot metadata mismatch')
    generated_time(meta, version)
    return meta

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stock', required=True)
    parser.add_argument('--history', required=True)
    parser.add_argument('--broad')
    parser.add_argument('--market')
    args = parser.parse_args()
    stock = verify('snapshots', args.stock)
    history = verify('history-snapshots', args.history)
    stock_day = stock.get('latest_date', stock.get('trade_date'))
    history_day = history['target_end']
    if any(date.fromisoformat(d) > datetime.now(ZoneInfo('Asia/Shanghai')).date() for d in (stock_day, history_day)):
        raise ValueError('Future data date')
    config = {'stock_version': args.stock, 'history_version': args.history,
              'stock_date': stock_day, 'history_target': history_day,
              'verified_at': datetime.now(timezone.utc).isoformat()}
    target = ROOT / 'local-view.json'
    if target.exists():
        previous = json.loads(target.read_text(encoding='utf-8'))
        if previous.get('broad_version'):
            config.update(broad_version=previous['broad_version'],broad_target=previous['broad_target'])
        if previous.get('market_version'):
            config.update(market_version=previous['market_version'],market_target=previous['market_target'])
        if previous['stock_date'] > stock_day or previous['history_target'] > history_day:
            raise ValueError('Refusing older snapshot dates')
        for folder, version_key, date_key, new_version, new_date, new_meta in (
            ('snapshots', 'stock_version', 'stock_date', args.stock, stock_day, stock),
            ('history-snapshots', 'history_version', 'history_target', args.history, history_day, history),
        ):
            old_version = previous[version_key]
            if previous[date_key] == new_date and old_version != new_version:
                old_meta = verify(folder, old_version)
                if generated_time(new_meta, new_version) <= generated_time(old_meta, old_version):
                    raise ValueError('Refusing older or indistinguishable same-day snapshot')
    if args.broad:
        broad=verify('broad-snapshots',args.broad)
        if date.fromisoformat(broad['target_end'])>datetime.now(ZoneInfo('Asia/Shanghai')).date():
            raise ValueError('Future broad data date')
        if config.get('broad_target', '')>broad['target_end']:
            raise ValueError('Refusing older broad date')
        if config.get('broad_version') and config['broad_version']!=args.broad:
            old=verify('broad-snapshots',config['broad_version'])
            if generated_time(broad,args.broad)<=generated_time(old,config['broad_version']):
                raise ValueError('Refusing older broad snapshot')
        if broad.get('history_version') and broad['history_version']!=args.history:
            raise ValueError('Broad snapshot references a different history snapshot')
        config.update(broad_version=args.broad,broad_target=broad['target_end'])
    if args.market:
        market=verify('market-snapshots',args.market)
        if date.fromisoformat(market['target_end'])>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError('Future market date')
        if config.get('market_target','')>market['target_end']:raise ValueError('Refusing older market date')
        if config.get('market_version') and config['market_version']!=args.market:
            old=verify('market-snapshots',config['market_version'])
            if generated_time(market,args.market)<=generated_time(old,config['market_version']):raise ValueError('Refusing older market snapshot')
        if market.get('history_version')!=args.history:raise ValueError('Market snapshot references a different history snapshot')
        config.update(market_version=args.market,market_target=market['target_end'])
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    print(json.dumps(config, ensure_ascii=False))

if __name__ == '__main__':
    main()
