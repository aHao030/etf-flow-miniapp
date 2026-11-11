from datetime import datetime, timezone, timedelta
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


FILE = Path(__file__).resolve().parents[1] / 'scripts/export_current_stock_universe.py'
SPEC = importlib.util.spec_from_file_location('current_stock_universe_export', FILE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class CurrentStockUniverseTests(unittest.TestCase):
    def prepare(self, folder):
        root = Path(folder)
        today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        stamp = datetime.now(timezone.utc).isoformat()
        report = {'observed_at': stamp, 'total_verified': 3, 'verified_with_name': 3,
                  'exchanges': [{'exchange': name, 'status': 'observed', 'verified_rows': 1,
                                 'rejected_rows': 0, 'conflicts': []}
                                for name in ['SSE main board', 'SSE STAR', 'SZSE A-share']]}
        evidence = root / 'evidence.json'
        evidence.write_text(json.dumps(report), encoding='utf8')
        blocked = root / 'blocked.json'
        blocked.write_text(json.dumps({'observed_at': stamp, 'exchanges': [{'exchange': 'BSE', 'status': 'blocked', 'http_status': 403, 'source_url': 'https://www.bse.cn/nq/listedcompany.html'}]}), encoding='utf8')
        dbpath = root / 'stocks.db'
        with closing(sqlite3.connect(dbpath)) as db:
            db.execute('CREATE TABLE stock_listings(stock_code TEXT PRIMARY KEY,stock_name TEXT,listed_date TEXT,source TEXT,source_url TEXT)')
            for code, source in [('600519', 'SSE official stock directory'), ('688001', 'SSE official stock directory'), ('000001', 'SZSE official A-share directory')]:
                db.execute('INSERT INTO stock_listings VALUES(?,?,?,?,?)', (code, 'stock ' + code, '2021-01-04', source, module.SOURCES[source][1]))
            db.commit()
        local = root / 'web-trial/.local'
        catalog = local / 'market-snapshots/test-version'
        catalog.mkdir(parents=True)
        (local / 'local-view.json').write_text(json.dumps({'market_version': 'test-version', 'market_target': today}), encoding='utf8')
        (catalog / 'catalog.json').write_text(json.dumps({'items': [{'stock_code': code} for code in ['600519', '688001', '000001', '601198']]}), encoding='utf8')
        return root, dbpath, evidence, blocked, today, report

    def test_exact_same_day_set_is_exported_with_auditable_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, dbpath, evidence, blocked, today, _ = self.prepare(tmp)
            with patch.object(module, 'ROOT', root):
                result = module.export(dbpath, evidence, blocked, today, root / 'output')
            self.assertEqual(result['counts'], {'SH': 2, 'SZ': 1})
            self.assertFalse(result['complete'])
            doc = json.loads(Path(result['path']).read_text('utf8'))
            self.assertEqual(doc['exclusions'], [{'stock_code': '601198', 'reason': 'not_in_verified_current_exchange_directory'}])
            self.assertEqual(doc['gaps'][0]['market'], 'BJ')
            self.assertTrue(all(s['lifecycle_status'] == 'listed' for s in doc['stocks']))

    def test_upsert_stale_extra_or_partial_report_cannot_claim_current_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, dbpath, evidence, blocked, today, report = self.prepare(tmp)
            with closing(sqlite3.connect(dbpath)) as db:
                db.execute('INSERT INTO stock_listings VALUES(?,?,?,?,?)', ('600000', 'old', '2000-01-01', 'SSE official stock directory', module.SOURCES['SSE official stock directory'][1]))
                db.commit()
            with patch.object(module, 'ROOT', root), self.assertRaisesRegex(ValueError, 'stale extras'):
                module.export(dbpath, evidence, blocked, today, root / 'output')
            report['exchanges'][0]['rejected_rows'] = 1
            evidence.write_text(json.dumps(report), encoding='utf8')
            with patch.object(module, 'ROOT', root), self.assertRaisesRegex(ValueError, 'partial directory'):
                module.export(dbpath, evidence, blocked, today, root / 'output')

    def test_old_evidence_and_conflicts_are_not_current_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, dbpath, evidence, blocked, today, report = self.prepare(tmp)
            report['observed_at'] = '2020-01-01T00:00:00Z'
            evidence.write_text(json.dumps(report), encoding='utf8')
            with patch.object(module, 'ROOT', root), self.assertRaisesRegex(ValueError, 'requested Beijing date'):
                module.export(dbpath, evidence, blocked, today, root / 'output')
            report['observed_at'] = datetime.now(timezone.utc).isoformat()
            report['exchanges'][0]['conflicts'] = ['600519']
            evidence.write_text(json.dumps(report), encoding='utf8')
            with patch.object(module, 'ROOT', root), self.assertRaisesRegex(ValueError, 'partial directory'):
                module.export(dbpath, evidence, blocked, today, root / 'output')


if __name__ == '__main__':
    unittest.main()
