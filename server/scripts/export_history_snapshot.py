"""Export immutable history shards, bounded to 1.8 MB each."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_queries as h

def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')

def export(parent):
    version = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S') + '-' + uuid.uuid4().hex
    staging = parent / ('.tmp-' + version)
    staging.mkdir(parents=True)
    files, hashes = [], {}
    def write(key, obj):
        payload = encode(obj)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError('历史文件超过上限: ' + key)
        (staging / (key+'.json')).write_bytes(payload)
        files.append(key); hashes[key] = hashlib.sha256(payload).hexdigest()
    with closing(h.connect()) as original, closing(sqlite3.connect(':memory:')) as conn:
        original.backup(conn); conn.row_factory = sqlite3.Row
        end = h.target_end(conn)
        funds = h.catalog(conn, end)
        meta = h.metadata(conn, end, funds)
        meta['version'] = version
        chunk, size, number = {}, 20, 0
        def flush():
            nonlocal chunk, size, number
            if chunk:
                write('data-'+str(number).zfill(4), {'funds':chunk})
                number += 1; chunk = {}; size = 20
        for f in funds:
            rows = h.history_rows(conn, f['fund_code'], end)
            payload = encode(rows)
            if size + len(payload) + 20 > 1800000 or len(chunk) >= 500:
                flush()
            f['file'] = 'data-'+str(number).zfill(4)
            f['flow'] = h.coverage(rows, 4)
            f['flow_status'] = 'partial' if f['flow']['count'] else 'unavailable'
            chunk[f['fund_code']] = rows; size += len(payload) + 20
        flush()
        meta['flow_funds'] = sum(f['flow']['count']>0 for f in funds)
        meta['flow_rows'] = sum(f['flow']['count'] for f in funds)
        meta['data_revision'] = hashlib.sha256(encode({'files':hashes,'catalog':funds,'target_start':meta['target_start'],'target_end':end,'scope':meta['scope'],'notes':meta['notes']})).hexdigest()
        write('meta', meta); write('catalog', {'items': funds})
    manifest = {'version':version, 'files':files, 'hashes':hashes, 'target_start':meta['target_start'], 'target_end':meta['target_end'], 'generated_at':meta['generated_at']}
    (staging/'manifest.json').write_bytes(encode(manifest))
    final = parent / version; staging.rename(final)
    print(json.dumps({'directory':str(final), 'funds':len(funds), 'files':len(files), 'shares_rows':meta['shares_rows'], 'nav_rows':meta['nav_rows']}, ensure_ascii=False))
    return final

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('parent',type=Path); args=parser.parse_args()
    export(args.parent)
