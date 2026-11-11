from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_off_exchange import SCHEMA, store_result


class MembershipRefreshTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO off_exchange_members VALUES(?,?,?,?,?,?,?,?,?)",
                        ("000051", "测试基金A", "000300", "A", "feeder", "https://example.test/profile", "observed", "沪深300", "2026-11-01"))
        self.db.execute("INSERT INTO off_exchange_reports VALUES(?,?,?,?,?,?,?,?,?,?)",
                        ("000051", "2026-07-01", "2026-09-30", 100, 30, 70, 200, "A", "000051", "https://example.test/report"))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def assert_excluded_but_retained(self, status):
        store_result(self.db, {"code": "000051", "status": status})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM off_exchange_members").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM off_exchange_reports").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM off_exchange_reports JOIN off_exchange_members USING(fund_code)").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT status FROM off_exchange_checks").fetchone()[0], status)

    def test_changed_tracking_index_removes_old_membership_keeps_raw_report(self):
        self.assert_excluded_but_retained("other_index")

    def test_confirmed_exchange_etf_cannot_remain_in_off_exchange_members(self):
        self.assert_excluded_but_retained("listed_etf_excluded")

    def test_source_failure_does_not_reclassify_or_zero_verified_history(self):
        store_result(self.db, {"code": "000051", "status": "failed", "error": "HTTP 403"})
        self.assertEqual(self.db.execute("SELECT index_code FROM off_exchange_members").fetchone()[0], "000300")
        self.assertEqual(self.db.execute("SELECT net_subscription_shares FROM off_exchange_reports").fetchone()[0], 70)


if __name__ == "__main__":
    unittest.main()
