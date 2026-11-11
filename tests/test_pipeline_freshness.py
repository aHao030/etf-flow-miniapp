import os
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))
import pipeline
import db

class FreshnessTest(unittest.TestCase):
    def test_failure_does_not_compute_or_advance(self):
        with patch.object(pipeline.db, 'init'), patch.object(pipeline.db, 'meta_get', return_value=''), \
             patch.object(pipeline.db, 'meta_set'), patch.object(pipeline, 'log'), \
             patch.object(pipeline, 'ensure_inputs'), patch.object(pipeline, 'etl_sse_shares', return_value=1), \
             patch.object(pipeline, 'etl_nav', side_effect=RuntimeError('unavailable')), \
             patch.object(pipeline, 'etl_holdings', return_value=0), patch.object(pipeline, 'etl_floatcap', return_value=0), \
             patch.object(pipeline.compute, 'run_model') as compute:
            result = pipeline.run('2026-10-30')
            self.assertEqual(result['status'], 'degraded')
            compute.assert_not_called()

    def test_target_requires_each_active_fund(self):
        import pandas as pd
        universe = pd.DataFrame({'status': ['active', 'active'], 'fund_code': ['510050', '510300']})
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'flow_by_etf.csv'), 'w', encoding='utf8') as f:
                f.write('date,fund_code\n2026-10-30,510050\n')
            with patch.object(pipeline.config, 'OUTPUTS_DIR', d), patch.object(pipeline, 'load_universe', return_value=universe):
                with self.assertRaisesRegex(RuntimeError, '510300'):
                    pipeline.validate_model_target('2026-10-30')

    def test_ranking_failure_rolls_back_snapshot_and_date(self):
        with tempfile.TemporaryDirectory() as d, patch.object(pipeline.config, 'DB_PATH', os.path.join(d, 'x.db')):
            with db.conn() as c:
                c.executescript(db.SCHEMA)
            db.execute("INSERT INTO flow_daily(trade_date, stock_code) VALUES (?, ?)", ('old', '600000'))
            db.meta_set('latest_date', 'old')
            def ingest():
                db.execute("DELETE FROM flow_daily")
                db.execute("INSERT INTO flow_daily(trade_date, stock_code) VALUES (?, ?)", ('2026-10-30', '600000'))
                return '2026-10-30'
            def fail_ranks():
                db.meta_set('latest_date', 'bad')
                raise RuntimeError('rank failure')
            with patch.object(db, 'init'), patch.object(pipeline, 'log'), patch.object(pipeline, 'ensure_inputs'), \
                 patch.object(pipeline, 'etl_sse_shares', return_value=1), patch.object(pipeline, 'etl_nav', return_value=1), \
                 patch.object(pipeline, 'etl_holdings', return_value=1), patch.object(pipeline, 'etl_floatcap', return_value=1), \
                 patch.object(pipeline, 'last_trade_date_before', return_value=None), \
                 patch.object(pipeline, 'validate_model_target'), patch.object(pipeline, 'load_universe'), \
                 patch.object(pipeline.compute, 'run_model'), patch.object(pipeline.compute, 'ingest', side_effect=ingest), \
                 patch.object(pipeline.compute, 'update_stock_meta'), patch.object(pipeline.compute, 'update_etf_meta'), \
                 patch.object(pipeline.compute, 'build_ranks', side_effect=fail_ranks):
                result = pipeline.run('2026-10-30')
            self.assertEqual(result['status'], 'degraded')
            self.assertEqual(db.meta_get('latest_date'), 'old')
            self.assertEqual(db.query_one('SELECT trade_date FROM flow_daily')['trade_date'], 'old')

if __name__ == '__main__':
    unittest.main()
