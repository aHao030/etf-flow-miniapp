import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'history' / 'stock_price_source.py'
spec = importlib.util.spec_from_file_location('stock_price_source', path)
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


def payload(rows=None, code='600519'):
    symbol = 'sh' + code
    return {'code': 0, 'data': {symbol: {'qt': {symbol: ['1', 'stock', code, '99999']},
        'day': rows if rows is not None else [['2021-01-04', '1999.98', '1997', '2004.99', '1983.81', '43514']]}}}


class StockPriceSource(unittest.TestCase):
    def parse(self, body):
        return p.parse_payload(body, '600519', '2020-01-01', '2024-12-31')

    def test_daily_close_not_realtime_price(self):
        self.assertEqual(self.parse(payload()), [['2021-01-04', 1997.0]])

    def test_empty_identity_verified_window_is_not_zero(self):
        self.assertEqual(self.parse(payload([])), [])

    def test_identity_and_adjustment_fail_closed(self):
        body = payload(); body['data']['sh600519']['qt']['sh600519'][2] = '600000'
        with self.assertRaises(ValueError): self.parse(body)
        body = payload(); body['data']['sh600519']['qfqday'] = []
        with self.assertRaises(ValueError): self.parse(body)

    def test_duplicate_future_bad_price_and_truncation(self):
        row = payload()['data']['sh600519']['day'][0]
        for rows in ([row, row], [['2025-01-01', *row[1:]]], [['2021-01-04', '1', '0', '2', '0', '10']], [row] * 2000):
            with self.assertRaises(ValueError): self.parse(payload(rows))

    def test_zero_volume_is_gap_and_bad_ohlc_rejected(self):
        self.assertEqual(self.parse(payload([['2021-01-04','1','1','1','1','0']])), [])
        with self.assertRaisesRegex(ValueError,'OHLC at 2021-01-04'): self.parse(payload([['2021-01-04','1','3','2','1','10']]))

    def test_windows_cap_source_request(self):
        self.assertEqual(p.window_bounds(2020, '2026-11-09'), ('2020-01-01','2024-12-31'))
        self.assertEqual(p.window_bounds(2025, '2026-11-09'), ('2025-01-01','2026-11-09'))
        with self.assertRaises(ValueError): p.parse_payload(payload(), '600519', '1990-01-01','2026-11-09')


if __name__ == '__main__': unittest.main()
