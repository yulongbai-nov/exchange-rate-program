#!/usr/bin/env python3
"""Fast local check for the multi-source backend.

Run before deploying:

    python3 scripts/local_check.py

It fetches each quote source live, builds a throw-away database, and exercises
the HTTP API surface in-process. Nothing is deployed and no WeChat credentials
are needed.
"""
import json
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core import SOURCES, connect, init_db, now, record_quote  # noqa: E402
from backend.server import Application  # noqa: E402
from backend.sources import fetch  # noqa: E402

FAILURES = []


def call(app, method, path, query=None, body=None, token=None):
    result = app.api(method, path, query or {}, body, token)
    if isinstance(result, tuple):
        return result
    return 200, result


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + str(detail)) if detail and not ok else ''))
    if not ok:
        FAILURES.append(name)


def main():
    print('== live sources ==')
    for source in SOURCES:
        try:
            price, stamp = fetch(source)
            print(f'{source:9s} price={price/1_000_000:.4f} stamp={stamp}')
        except Exception as error:
            print(f'{source:9s} ERROR {type(error).__name__}: {error}')
            FAILURES.append('fetch ' + source)

    print('\n== API surface ==')
    tmp = tempfile.TemporaryDirectory()
    path = str(Path(tmp.name) / 'local.db')
    init_db(path)
    with connect(path) as db:
        db.execute("INSERT INTO users VALUES('u','openid-local',0)")
        token = uuid.uuid4().hex
        db.execute("INSERT INTO sessions VALUES(?,?,?)", (token, 'u', (now() + timedelta(days=7)).isoformat()))
    app = Application(path, demo=False)
    for source in SOURCES:
        try:
            price, stamp = fetch(source)
            record_quote(path, price, stamp, source=source)
        except Exception:
            pass

    status, body = call(app, 'GET', '/api/sources', token=token)
    check('/api/sources', status == 200 and len(body.get('sources', [])) == len(SOURCES), body)
    listed = {item['id']: item for item in body.get('sources', [])}
    for source in SOURCES:
        status, one = call(app, 'GET', '/api/status', {'source': [source]}, token=token)
        ok = status == 200 and one.get('source') == source and one.get('source_label')
        check(f'/api/status?source={source}', ok, one)
        if listed.get(source, {}).get('quote'):
            ok = listed[source]['quote']['price'] > 0
            check(f'/api/sources[{source}].quote', ok)
        start = (now() - timedelta(days=3)).isoformat()
        end = (now() + timedelta(hours=1)).isoformat()
        status, history = call(app, 'GET', '/api/history',
                               {'start': [start], 'end': [end], 'source': [source]}, token=token)
        check(f'/api/history?source={source}', status == 200 and 'points' in history, history)
    try:
        call(app, 'GET', '/api/status', {'source': ['nope']}, token=token)
        unknown_ok = False
    except ValueError:
        unknown_ok = True
    check('/api/status rejects unknown source', unknown_ok)
    status, body = call(app, 'POST', '/api/rules', body={'kind': 'target', 'start': '00:00', 'end': '23:59',
                                                         'amount': '6.7', 'direction': 'above',
                                                         'source': 'sina_cny'}, token=token)
    check('/api/rules create with source', status == 200 and body.get('id'), body)
    status, body = call(app, 'GET', '/api/rules', token=token)
    rule = (body.get('rules') or [{}])[0]
    check('/api/rules returns source label', status == 200 and rule.get('source') == 'sina_cny', body)
    tmp.cleanup()

    print()
    if FAILURES:
        print('FAILED:', ', '.join(FAILURES))
        return 1
    print('ALL LOCAL CHECKS PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
