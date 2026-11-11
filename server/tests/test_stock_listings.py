from datetime import date, timedelta
import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "collect_stock_listings.py"
spec = importlib.util.spec_from_file_location("collect_stock_listings", MODULE)
listings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(listings)


class ListingValidationTests(unittest.TestCase):
    def test_missing_future_dates_and_wrong_identifiers_are_not_fabricated(self):
        rows, rejected = listings.validated_rows([
            {"code": "1", "date": "1991-04-03"},
            {"code": "000002", "date": None},
            {"code": "000003", "date": (date.today() + timedelta(days=1)).isoformat()},
            {"code": "BAD0004", "date": "2020-01-01"},
        ], "code", "date", "verified exchange", "https://exchange.example/directory")
        self.assertEqual(rows, [("000001", "1991-04-03", "verified exchange", "https://exchange.example/directory", None)])
        self.assertEqual(rejected, 3)

    def test_conflicting_dates_fail_instead_of_selecting_one(self):
        with self.assertRaises(ValueError):
            listings.validated_rows([
                {"code": "000001", "date": "1991-04-03"},
                {"code": "000001", "date": "2000-01-01"},
            ], "code", "date", "verified exchange", "https://exchange.example/directory")


if __name__ == "__main__":
    unittest.main()
