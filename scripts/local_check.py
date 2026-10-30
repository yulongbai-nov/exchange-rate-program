#!/usr/bin/env python3
"""Fast local end-to-end check for the multi-pair / multi-source backend.

    python3 scripts/local_check.py

Fetches live quotes, writes them into a throw-away database, then exercises the
HTTP API in-process. Nothing is deployed and no WeChat credentials are needed.
"""
import json
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import PAIRS, SOURCES, connect, init_db, now  # noqa: E402
from backend.server import Application  # noqa: E402

FAILURES = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + json.dumps(detail, ensure_ascii=False)[:200]) if not ok else ''))
    if not ok:
        FAILURES.append(name)


def call(app, method, path, query=None, body=None, token=None):
    result = app.api(method, path, query or {}, body, token)
    if isinstance(result, tuple):
        return result
    return 200, result


def main():
    print('== live collection (all sources, all pairs) ==')
    tmp = tempfile.TemporaryDirectory()
    path = str(Path(tmp.name) / 'local.db')
    init_db(path)
    with connect(path) as db:
        db.execute("INSERT INTO users VALUES('u','openid-local',0)")
        token = uuid.uuid4().hex
        db.execute("INSERT INTO sessions VALUES(?,?,?)", (token, 'u', (now() + timedelta(days=7)).isoformat()))
    app = Application(path, demo=False)
    app.collect()
    for source in SOURCES:
        status = app.source_status[source]
        check(f'collect {source}', status['error'] is None, status)

    status, body = call(app, 'GET', '/api/pairs', token=token)
    check('/api/pairs returns 16 pairs', status == 200 and len(body.get('pairs', [])) == len(PAIRS), body)
    check('TWD included', any(p['pair'] == 'TWD' for p in body.get('pairs', [])), body)

    status, table = call(app, 'GET', '/api/table', {'pair': ['USD']}, token=token)
    rows = table.get('sources', [])
    check('/api/table has all sources', status == 200 and len(rows) == len(SOURCES), table)
    check('/api/table quoted sources', sum(1 for row in rows if row['price'] is not None) >= 3,
          [row['source'] for row in rows if row['price'] is None])
    check('/api/table colors', all(row.get('color') for row in rows), rows)
    for pair in ('EUR', 'JPY', 'TWD'):
        _, one = call(app, 'GET', '/api/table', {'pair': [pair]}, token=token)
        check(f'/api/table?pair={pair}', one.get('pair') == pair and len(one.get('sources', [])) == len(SOURCES), one)

    start = (now() - timedelta(hours=2)).isoformat()
    end = (now() + timedelta(minutes=5)).isoformat()
    status, chart = call(app, 'GET', '/api/chart', {'pair': ['USD'], 'start': [start], 'end': [end], 'interval': ['60']}, token=token)
    check('/api/chart buckets', status == 200 and len(chart.get('buckets', [])) > 0, chart)
    check('/api/chart series', len(chart.get('series', [])) == len(SOURCES) and all('color' in s for s in chart['series']), chart)
    check('/api/chart aligned values', all(len(s['values']) == len(chart['buckets']) for s in chart['series']), chart)

    status, status_body = call(app, 'GET', '/api/status', {'source': ['sina'], 'pair': ['USD']}, token=token)
    check('/api/status', status == 200 and status_body.get('source') == 'sina', status_body)
    try:
        call(app, 'GET', '/api/status', {'source': ['nope']}, token=token)
        check('/api/status rejects unknown source', False)
    except ValueError:
        check('/api/status rejects unknown source', True)
    try:
        call(app, 'GET', '/api/table', {'pair': ['NOPE']}, token=token)
        check('/api/table rejects unknown pair', False)
    except ValueError:
        check('/api/table rejects unknown pair', True)

    status, body = call(app, 'POST', '/api/rules', body={
        'kind': 'target', 'start': '00:00', 'end': '23:59', 'amount': '6.7',
        'direction': 'above', 'source': 'sina', 'pair': 'USD'}, token=token)
    check('/api/rules create with pair/source', status == 200 and body.get('id'), body)
    _, body = call(app, 'GET', '/api/rules', token=token)
    rule = (body.get('rules') or [{}])[0]
    check('/api/rules carries labels', rule.get('source') == 'sina' and rule.get('pair') == 'USD'
          and rule.get('pair_label') == '美元', body)

    tmp.cleanup()
    print()
    if FAILURES:
        print('FAILED:', ', '.join(FAILURES))
        return 1
    print('ALL LOCAL CHECKS PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
