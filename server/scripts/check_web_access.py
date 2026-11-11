"""Read-only hosted metadata check. Credentials arrive only over hidden stdin."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import requests
from publish_web_snapshot import ORIGIN, SECRET_PATH, dpapi, read_access

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'origin': ORIGIN}
    try:
        token = read_access()
        secret = dpapi(SECRET_PATH.read_bytes(), False).decode('ascii')
        response = requests.get(ORIGIN + '/api/v1/market/meta', headers={
            'OAI-Sites-Authorization': 'Bearer ' + token,
            'Authorization': 'Bearer ' + secret,
        }, timeout=(15, 30), allow_redirects=False)
        result.update(status=response.status_code, content_type=response.headers.get('Content-Type', '').split(';')[0])
        if result['content_type'] == 'application/json' and response.status_code != 200:
            body = response.json()
            if type(body.get('code')) is int:
                result['error_code'] = body['code']
            # Do not log response text, headers, or credential-bearing error details.
        if response.status_code == 200:
            body = response.json()
            data = body.get('data', {}) if body.get('code') == 0 else {}
            result['metadata'] = {key: data.get(key) for key in ('version', 'history_version', 'latest_date', 'target_end', 'total_funds')}
        result['accessible'] = response.status_code == 200 and bool(result.get('metadata', {}).get('version'))
        if result['accessible']:
            result['linked_metadata'] = {}
            for dataset in ('history', 'broad'):
                linked = requests.get(ORIGIN + '/api/v1/' + dataset + '/meta', headers={
                    'OAI-Sites-Authorization': 'Bearer ' + token,
                    'Authorization': 'Bearer ' + secret,
                }, timeout=(15, 30), allow_redirects=False)
                entry = {'status': linked.status_code}
                if linked.status_code == 200:
                    body = linked.json()
                    data = body.get('data', {}) if body.get('code') == 0 else {}
                    entry.update({key: data.get(key) for key in ('version', 'history_version', 'target_end', 'total_funds', 'shares_rows', 'nav_rows')})
                result['linked_metadata'][dataset] = entry
                if linked.status_code in (401, 403, 429):
                    break
            history = result['linked_metadata'].get('history', {})
            broad = result['linked_metadata'].get('broad', {})
            result['versions_aligned'] = bool(history.get('version')) and result['metadata'].get('history_version') == history.get('version') == broad.get('history_version') and result['metadata'].get('target_end') == history.get('target_end') == broad.get('target_end')
    except Exception:
        result.update(accessible=False, error='network_or_metadata_unavailable')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
