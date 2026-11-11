import os
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))
import config
import db

class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = config.DB_PATH
        config.DB_PATH = os.path.join(self.temp.name, 'test.db')
        with db.conn() as c:
            c.executescript(db.SCHEMA)
        self.snapshot = {}
        with db.conn() as c:
            for table in db.MODEL_SNAPSHOT_TABLES:
                info = list(c.execute(f'PRAGMA table_info("{table}")'))
                columns = [x[1] for x in info if x[3] or x[5]]
                self.snapshot[table] = (columns, [tuple('old' for _ in columns)])
        db.replace_model_snapshot(self.snapshot)

    def tearDown(self):
        config.DB_PATH = self.old
        self.temp.cleanup()

    def test_replaces_stale_rows(self):
        new = {t: (cols, [tuple('new' for _ in cols)]) for t, (cols, _) in self.snapshot.items()}
        db.replace_model_snapshot(new)
        with db.conn() as c:
            for table, (cols, _) in new.items():
                values = list(c.execute(f'SELECT "{cols[0]}" FROM "{table}"'))
                self.assertEqual([x[0] for x in values], ['new'])

    def test_failure_rolls_back_every_table(self):
        bad = dict(self.snapshot)
        cols, rows = bad['flow_daily']
        bad['flow_daily'] = (cols, rows + rows)
        with self.assertRaises(Exception):
            db.replace_model_snapshot(bad)
        with db.conn() as c:
            for table, (cols, _) in self.snapshot.items():
                self.assertEqual(c.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 1)
                self.assertEqual(c.execute(f'SELECT "{cols[0]}" FROM "{table}"').fetchone()[0], 'old')

    def test_empty_snapshot_rejected(self):
        bad = dict(self.snapshot)
        cols, _ = bad['flow_daily']
        bad['flow_daily'] = (cols, [])
        with self.assertRaises(ValueError):
            db.replace_model_snapshot(bad)
        with db.conn() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM flow_daily').fetchone()[0], 1)

if __name__ == '__main__':
    unittest.main()
