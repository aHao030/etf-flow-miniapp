import os
import sys
import unittest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))
import v1_flow
import universe_review

class CoverageTests(unittest.TestCase):
    def run_flow(self, dates, nav_dates):
        uni = pd.DataFrame([dict(fund_code="510050", status="active", listed_date=pd.NaT)])
        weights = pd.DataFrame([dict(fund_code="510050", stock_code="600000", stock_name="x", weight=1, weight_source="index", coverage_pct=100)])
        shares = pd.DataFrame(dict(fund_code=["510050"] * len(dates), stat_date=pd.to_datetime(dates), shares=list(range(100, 100 + 10 * len(dates), 10))))
        nav = pd.DataFrame(dict(fund_code=["510050"] * len(nav_dates), nav_date=pd.to_datetime(nav_dates), unit_nav=[1.0] * len(nav_dates)))
        return v1_flow.compute_flow(uni, weights, shares, nav)

    def test_missing_weekday_does_not_count_multiple_days(self):
        contrib, marks = self.run_flow(["2026-10-26", "2026-10-28"], ["2026-10-26", "2026-10-27"])
        self.assertTrue(contrib.empty)
        self.assertEqual(marks.iloc[0]["flags"], "data_gap")

    def test_weekend_is_contiguous(self):
        contrib, _ = self.run_flow(["2026-10-23", "2026-10-26"], ["2026-10-23"])
        self.assertEqual(contrib.iloc[0]["contribution_yuan"], 10)

    def test_missing_previous_nav_is_not_replaced_by_stale_nav(self):
        contrib, marks = self.run_flow(["2026-10-26", "2026-10-27"], ["2026-10-23"])
        self.assertTrue(contrib.empty)
        self.assertIn("nav_missing", marks.iloc[0]["flags"])

    def test_candidates_require_review(self):
        self.assertEqual(universe_review.classify({"fund_code":"510300", "fund_name":"沪深300ETF"})[0], "pending_review")
        self.assertEqual(universe_review.classify({"fund_code":"159001", "fund_name":"货币ETF"})[0], "excluded")

    def test_window_counts_missing_session_dates(self):
        dates = ["2026-10-19", "2026-10-20", "2026-10-26"]
        contrib = pd.DataFrame({"date": dates, "stock_code": ["600000"] * 3,
                                "stock_name": ["x"] * 3, "fund_code": ["510050"] * 3,
                                "contribution_yuan": [1.0] * 3})
        floatcap = pd.DataFrame({"symbol": ["600000"], "float_mv_yi": [1.0]})
        calendar = pd.bdate_range("2026-10-19", "2026-10-26")
        _, result, _ = v1_flow.aggregate(contrib, floatcap, calendar)
        last = result.iloc[-1]
        self.assertEqual(last["valid_days_5"], 2)
        self.assertTrue(pd.isna(last["flow_5d_yuan"]))

if __name__ == "__main__":
    unittest.main()

