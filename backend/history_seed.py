"""Import bundled multi-year daily history into the local SQLite database.

`data/history-seed.json.gz` is generated offline by tools/build_history_seed.py
(Yahoo Finance daily closes as the market proxy, ECB reference rates as the
daily proxy). Import is idempotent thanks to the quotes primary key.
"""
import gzip
import json
import logging
import os
from pathlib import Path

from backend.core import connect, now

LOG = logging.getLogger('rates')
DEFAULT_SEED = Path(__file__).resolve().parents[1] / 'data' / 'history-seed.json.gz'


def seed_file():
    return Path(os.getenv('SEED_HISTORY_FILE') or DEFAULT_SEED)


def import_seed(path, logger=LOG):
    source = seed_file()
    if not source.exists():
        logger.warning('history seed missing: %s', source)
        return 0
    observed = now().isoformat()
    rows = []
    try:
        with gzip.open(source, 'rt', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                rows.append((item['pair'], item['source'], item['stamp'],
                             int(item['price']), observed))
    except (OSError, ValueError, KeyError) as error:
        logger.warning('history seed unreadable: %s', error)
        return 0
    with connect(path) as db:
        before = db.total_changes
        db.executemany('INSERT OR IGNORE INTO quotes(pair,source,stamp,price,observed) VALUES(?,?,?,?,?)', rows)
        inserted = db.total_changes - before
        total = db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0]
    logger.info('history seed: %s rows scanned, %s inserted, %s total quotes', len(rows), inserted, total)
    return inserted
