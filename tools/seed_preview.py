#!/usr/bin/env python3
"""Seed a synthetic database for the local visual harness (tools/preview.html).

    python3 tools/seed_preview.py /tmp/preview.db

Generates 30 days of points for all 16 pairs across the live sources so the
table, multi-line chart, gaps and zoom can be inspected without waiting for
real collection.
"""
import math
import random
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import init_db, now  # noqa: E402

BASE = {
    'USD': 6.65, 'EUR': 7.43, 'GBP': 8.91, 'JPY': 0.0422, 'HKD': 0.848,
    'AUD': 4.68, 'CAD': 4.76, 'SGD': 5.21, 'CHF': 8.07, 'NZD': 3.75,
    'KRW': 0.00499, 'THB': 0.197, 'MYR': 1.64, 'RUB': 0.0825, 'INR': 0.0702,
    'TWD': 0.204,
}
SOURCES = {'boc_buy': 0.0000, 'boc_cash': -0.0025, 'sina': 0.0012, 'cnh': 0.0004, 'daily': 0.0008}
SCALE = 1_000_000


def main(path):
    path = str(path)
    init_db(path)
    random.seed(7)
    end = now().replace(second=0, microsecond=0)
    start = end - timedelta(days=30)
    points = []
    step = timedelta(minutes=5)
    stamp, index = start, 0
    while stamp <= end:
        for pair, base in BASE.items():
            drift = base * (1 + 0.004 * math.sin(index / 40.0 + (hash(pair) % 7)))
            for source, offset in SOURCES.items():
                if source == 'cnh' and pair != 'USD':
                    continue
                if source in ('boc_buy', 'boc_cash') and index % 12 != 0:
                    continue
                if pair == 'USD' and 400 <= index % 864 <= 430:
                    continue  # deliberate gap for the chart
                price = drift * (1 + offset) * (1 + random.uniform(-0.0004, 0.0004))
                points.append((pair, source, stamp.isoformat(), int(round(price * SCALE)), stamp.isoformat()))
        index += 1
        stamp += step
    with sqlite3.connect(path) as db:
        db.executemany('INSERT OR IGNORE INTO quotes(pair,source,stamp,price,observed) VALUES(?,?,?,?,?)', points)
    print(f'seeded {len(points)} quotes into {path}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '/tmp/preview.db')
