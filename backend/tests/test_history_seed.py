import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.core import connect, init_db
from backend.history_seed import import_seed


class HistorySeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'seed.db')
        init_db(self.db)
        self.seed = Path(self.tmp.name) / 'seed.json.gz'
        rows = [
            {'pair': 'USD', 'source': 'sina', 'stamp': '2016-10-31T15:00:00+08:00', 'price': 6783400},
            {'pair': 'EUR', 'source': 'daily', 'stamp': '2016-10-31T15:00:00+08:00', 'price': 7420000},
        ]
        with gzip.open(self.seed, 'wt', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row) + '\n')

    def test_import_is_idempotent(self):
        with mock.patch.dict(os.environ, {'SEED_HISTORY_FILE': str(self.seed)}):
            self.assertEqual(import_seed(self.db), 2)
            self.assertEqual(import_seed(self.db), 0)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0], 2)

    def test_missing_seed_is_tolerated(self):
        with mock.patch.dict(os.environ, {'SEED_HISTORY_FILE': str(Path(self.tmp.name) / 'nope.gz')}):
            self.assertEqual(import_seed(self.db), 0)


if __name__ == '__main__':
    unittest.main()
