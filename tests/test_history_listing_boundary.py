import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from history import storage
import history_queries


class ListingBoundaryTest(unittest.TestCase):
    def rows(self, listing):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'ETF_HISTORY_DB_PATH': str(Path(temp) / 'history.db')}):
            db = storage.connect()
            try:
                with db:
                    db.execute("INSERT INTO funds(fund_code,market,listed_date,first_seen) VALUES('158076','SZ',?,'2026-11-04')", (listing,))
                    observations = [('2026-11-04', 210), ('2026-11-05', 209), ('2026-11-06', 159), ('2026-11-09', 160)]
                    for day, shares in observations:
                        db.execute('INSERT INTO calendar VALUES (?,?)', (day, 'test'))
                        db.execute('INSERT INTO shares VALUES (?,?,?,?)', ('158076', day, shares, 'test'))
                        db.execute('INSERT INTO nav VALUES (?,?,?,?,?)', ('158076', day, 1, 1, 'test'))
                        db.execute('INSERT INTO nav_verified VALUES (?,?,?)', ('158076', day, 'test'))
                    storage.record(db, 'nav-v2', '158076:2026-11-04:2026-11-09', 'success', 4)
                return history_queries.history_rows(db, '158076', '2026-11-09')
            finally:
                db.close()

    def test_prelisting_and_first_listing_day_are_not_flow(self):
        rows = self.rows('2026-11-06')
        self.assertTrue(all(row[4:] == [None, 'fund_event'] for row in rows[:3]))
        self.assertEqual([row[1] for row in rows], [210, 209, 159, 160])
        self.assertEqual(rows[-1][4:], [1.0, 'estimated'])

    def test_boundary_between_calendar_days_is_excluded(self):
        self.assertEqual(self.rows('2026-11-07')[-1][4:], [None, 'fund_event'])

    def test_unknown_listing_does_not_use_first_seen_as_listing(self):
        rows = self.rows(None)
        self.assertEqual(rows[1][4:], [-1.0, 'estimated'])
        self.assertEqual(rows[2][4:], [-50.0, 'estimated'])


if __name__ == '__main__':
    unittest.main()
