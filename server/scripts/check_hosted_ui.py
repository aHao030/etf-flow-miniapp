"""Verify the private hosted UI's release files without persisting response bodies or access credentials."""
from argparse import ArgumentParser
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path

import requests

from publish_web_snapshot import ORIGIN, read_access


def fingerprint(body):
    return sha256(body.replace(b'\r\n', b'\n')).hexdigest()


class MainContent(HTMLParser):
    """Compare the application workspace independently of the host's outer page."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events, self.mains, self.inside = [], 0, False

    def handle_starttag(self, tag, attrs):
        if tag == 'main':
            self.mains += 1
            self.inside = True
        if self.inside:
            self.events.append(('start', tag, sorted(attrs)))

    def handle_endtag(self, tag):
        if self.inside:
            self.events.append(('end', tag))
        if tag == 'main':
            self.inside = False

    def handle_data(self, data):
        if self.inside and data.strip():
            self.events.append(('text', data.strip()))


def workspace(body):
    parser = MainContent()
    parser.feed(body.decode('utf-8', errors='strict').replace('\r\n', '\n'))
    parser.close()
    return parser.events if parser.mains == 1 and not parser.inside and parser.events else None


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    public = Path(__file__).resolve().parents[2] / 'web-trial' / 'public'
    results = []
    token = read_access()
    with requests.Session() as client:
        for route, name in [('/', 'index.html'), ('/styles.css', 'styles.css'), ('/broad.mjs', 'broad.mjs')]:
            item = {'route': route}
            results.append(item)
            try:
                with client.get(ORIGIN + route, headers={'OAI-Sites-Authorization': 'Bearer ' + token}, timeout=(10, 25), allow_redirects=False, stream=True) as response:
                    item['status'] = response.status_code
                    if response.status_code != 200:
                        break
                    raw = bytearray()
                    for chunk in response.iter_content(65536):
                        raw.extend(chunk)
                        if len(raw) > 2 * 1024 * 1024:
                            raise ValueError('size_limit')
                local = (public / name).read_bytes()
                item['matches_release'] = fingerprint(bytes(raw)) == fingerprint(local)
                if name == 'index.html':
                    expected = workspace(local)
                    item['workspace_matches_release'] = bool(expected) and workspace(bytes(raw)) == expected
            except (requests.RequestException, ValueError, OSError):
                item['error'] = 'read_or_validation_failed'
                break
    verified = len(results) == 3 and all(item.get('matches_release') or item.get('workspace_matches_release') for item in results)
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'origin': ORIGIN, 'normalized_newlines': True, 'verified': verified, 'assets': results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
