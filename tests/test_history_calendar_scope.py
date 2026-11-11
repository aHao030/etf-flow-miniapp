from datetime import datetime
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from history import storage
import history_queries as h

class Scope(unittest.TestCase):
    def test_closed_day_observation_is_retained_in_storage_not_displayed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'ETF_HISTORY_DB_PATH':str(Path(directory)/'h.db')}):
            db=storage.connect()
            with db:
                db.execute("INSERT INTO funds(fund_code,market) VALUES('510050','SH')")
                db.execute("INSERT INTO calendar VALUES('2005-02-23','test')")
                for day in ['2005-02-23','2005-02-26']:
                    db.execute('INSERT INTO shares VALUES(?,?,?,?)',('510050',day,100,'test'))
            self.assertEqual([r[0] for r in h.history_rows(db,'510050','2005-02-28')],['2005-02-23'])
            self.assertEqual(db.execute('SELECT count(*) FROM shares').fetchone()[0],2)
            self.assertEqual(h.catalog(db,'2005-02-28')[0]['shares']['count'],1)
            meta=h.metadata(db,'2005-02-28')
            self.assertEqual(meta['source_days']['test']['records'],1)
            self.assertEqual(meta['market_days']['SH']['observed_days'],1)
            db.close()
    def test_future_calendar_does_not_make_future_data_current(self):
        import sqlite3
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE calendar(trade_date TEXT)')
        db.executemany('INSERT INTO calendar VALUES(?)',[(x,) for x in ['2026-11-09','2026-11-10','2026-12-31']])
        class Clock:
            @staticmethod
            def now(tz): return datetime(2026,11,10,12,tzinfo=tz)
        with patch.object(h,'datetime',Clock): self.assertEqual(h.target_end(db),'2026-11-09')
        db.close()

if __name__=='__main__': unittest.main()
