import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch, Mock

path = Path(__file__).resolve().parents[1] / 'server' / 'scripts' / 'publish_web_snapshot.py'
spec = importlib.util.spec_from_file_location('publisher', path)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)

class PublisherTest(unittest.TestCase):
    def test_dpapi_round_trip(self):
        plain = b'test-only-no-secret'
        self.assertNotEqual(publisher.dpapi(plain, True), plain)
        self.assertEqual(publisher.dpapi(publisher.dpapi(plain, True), False), plain)

    def test_snapshot_hash_and_date_checked_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            db = folder / 'data.db'
            with closing(sqlite3.connect(db)) as c:
                with c:
                    c.execute('CREATE TABLE meta(key TEXT,value TEXT)')
                    c.execute("INSERT INTO meta VALUES('latest_date','2026-10-30')")
            version = 'test-version'
            files = sorted(publisher.REQUIRED)
            hashes = {}
            for key in files:
                data = {'version':version, 'trade_date':'2026-10-30', 'generated_at':datetime.now(timezone.utc).isoformat()} if key == 'meta' else {}
                raw = json.dumps(data).encode()
                (folder / (key+'.json')).write_bytes(raw)
                hashes[key] = hashlib.sha256(raw).hexdigest()
            manifest = {'version':version, 'files':files, 'hashes':hashes, 'trade_date':'2026-10-30'}
            (folder / 'manifest.json').write_text(json.dumps(manifest))
            with patch.object(publisher.config, 'DB_PATH', str(db)), patch.object(publisher, 'date') as today:
                today.fromisoformat.side_effect = __import__('datetime').date.fromisoformat
                today.today.return_value = __import__('datetime').date(2026, 10, 31)
                result, _, _ = publisher.verify_snapshot(folder)
                self.assertEqual(result['version'], version)
                (folder / 'catalog.json').write_text('{"tampered":true}')
                with self.assertRaisesRegex(ValueError, '摘要'):
                    publisher.verify_snapshot(folder)

    def test_upload_failure_does_not_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / 'cipher'
            secret.write_bytes(b'cipher')
            manifest = {'version': 'test-version', 'trade_date': '2026-10-30',
                        'files': ['meta'], 'hashes': {'meta': 'a' * 64}}
            client = Mock()
            client.get.return_value.status_code = 503
            client.put.return_value.status_code = 500
            client.put.return_value.json.return_value = {'code': 50001}
            with patch.object(publisher, 'SECRET_PATH', secret), patch.object(publisher, 'dpapi', return_value=b'test-secret'), \
                 patch.object(publisher, 'verify_snapshot', return_value=(manifest, {'meta': b'{}'}, datetime.now(timezone.utc))), \
                 patch.object(publisher.requests, 'Session', return_value=client):
                with self.assertRaises(RuntimeError):
                    publisher.publish(Path(tmp), 'test-service-token')
            client.post.assert_not_called()

if __name__ == '__main__':
    unittest.main()
