"""Run with python3 -m backend.server. No third party runtime dependencies."""
import argparse
import hashlib
import json
import logging
import math
import os
import secrets
import signal
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend.core import (DEFAULT_PAIR, DEFAULT_SOURCE, PAIRS, SCALE, SOURCES, chart_series,
                          connect, history, init_db, now, pair_meta, record_quote, source_meta,
                          summarize, validate_rule)
from backend.integrations import WechatLoginError, WechatSender, analyze, dispatch, fetch_quote, quote_failure, wx_login
from backend.sources import fetch_all
from backend.storage import BackupManager

LOG = logging.getLogger('rates')
INTERVALS = ((2 * 86400, 60), (7 * 86400, 300), (40 * 86400, 900),
             (200 * 86400, 3600), (800 * 86400, 14400))


def auto_interval(start, end):
    try:
        span = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except (TypeError, ValueError):
        return 60
    for limit, interval in INTERVALS:
        if span <= limit:
            return interval
    return 86400


class Application:
    def __init__(self, path, demo=False):
        self.path, self.demo = path, demo
        self.last_attempt = None
        self.collect_error = None
        self.sender = WechatSender()
        self.backup = None
        self.source_status = {source: {'last_attempt': None, 'error': None} for source in SOURCES}
        init_db(path)

    def collect(self):
        for source in SOURCES:
            self.collect_source(source)
        if not self.demo:
            dispatch(self.path, self.sender)

    def collect_source(self, source):
        status = self.source_status[source]
        status['last_attempt'] = now().isoformat()
        try:
            if self.demo:
                if source != DEFAULT_SOURCE:
                    return
                stamp = now().replace(microsecond=0).isoformat()
                quotes = {'USD': (round((6.85 + .025 * math.sin(time.time() / 900)) * SCALE), stamp)}
            else:
                quotes = fetch_all(source)
            for pair, (price, stamp) in quotes.items():
                record_quote(self.path, price, stamp, source=source, pair=pair)
            status['error'] = None
        except Exception as error:
            status['error'] = quote_failure(error) if source == DEFAULT_SOURCE else '行情获取失败：' + type(error).__name__
            LOG.warning('quote collection failed (%s): %s', source, status['error'])
        if source == DEFAULT_SOURCE:
            self.last_attempt = status['last_attempt']
            self.collect_error = status['error']

    def user(self, token):
        with connect(self.path) as db:
            row = db.execute('SELECT user_id FROM sessions WHERE token=? AND expires>?', (token, now().isoformat())).fetchone()
        return row['user_id'] if row else None

    def latest_rows(self, db, pair):
        rows = db.execute('SELECT source,stamp,price FROM quotes WHERE pair=? ORDER BY stamp DESC', (pair,)).fetchall()
        latest = {}
        for row in rows:
            entry = latest.setdefault(row['source'], {'quote': row, 'previous': None})
            if entry['quote'] is not row and entry['previous'] is None and row['stamp'] != entry['quote']['stamp']:
                entry['previous'] = row
        return latest

    def api(self, method, path, query, body, token):
        def requested_source():
            candidate = (query.get('source') or [DEFAULT_SOURCE])[0]
            source_meta(candidate)
            return candidate

        def requested_pair():
            candidate = (query.get('pair') or [DEFAULT_PAIR])[0]
            pair_meta(candidate)
            return candidate

        if path == '/health' and method == 'GET':
            health = {'ok': True, 'demo': self.demo, 'last_attempt': self.last_attempt,
                      'collection_error': self.collect_error, 'instance': socket.gethostname(),
                      'source_status': self.source_status, 'pairs': len(PAIRS), 'sources': len(SOURCES)}
            if self.backup is not None:
                status = self.backup.status()
                health['backup'] = status
                health['backup_enabled'] = status['enabled']
                health['backup_last_ok_at'] = status['last_ok_at']
                health['backup_last_error'] = status['last_error']
                health['backup_restored_from'] = status['restored_from']
                health['backup_restore_failed'] = status['restore_failed']
            try:
                with connect(self.path) as db:
                    health['db_quotes'] = db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0]
            except Exception:
                health['db_quotes'] = None
            return health
        if path == '/api/login' and method == 'POST':
            code = body.get('code')
            if not isinstance(code, str) or not 1 <= len(code) <= 512:
                raise ValueError('缺少微信登录 code')
            # Demo is opt-in and uses a separate DB; it never sends WeChat notifications.
            openid = 'demo-' + hashlib.sha256(code.encode()).hexdigest() if self.demo else wx_login(code)
            uid, session = uuid.uuid4().hex, secrets.token_urlsafe(32)
            with connect(self.path) as db:
                db.execute('INSERT OR IGNORE INTO users(id,openid) VALUES(?,?)', (uid, openid))
                uid = db.execute('SELECT id FROM users WHERE openid=?', (openid,)).fetchone()[0]
                db.execute('DELETE FROM sessions WHERE expires<=?', (now().isoformat(),))
                db.execute('INSERT INTO sessions VALUES(?,?,?)', (session, uid, (now() + timedelta(days=7)).isoformat()))
            return {'token': session, 'demo': self.demo}
        uid = self.user(token)
        if not uid:
            return 401, {'error': '请先登录'}
        if path == '/api/pairs' and method == 'GET':
            return {'pairs': [{'pair': pair, 'label': meta['label']} for pair, meta in PAIRS.items()],
                    'default': DEFAULT_PAIR}
        if path == '/api/table' and method == 'GET':
            pair = requested_pair()
            with connect(self.path) as db:
                latest = self.latest_rows(db, pair)
            rows = []
            for source, meta in SOURCES.items():
                entry = latest.get(source) or {}
                quote, previous = entry.get('quote'), entry.get('previous')
                change = None
                if quote and previous:
                    change = round((quote['price'] - previous['price']) / SCALE, 6)
                stale = True
                if quote:
                    stale = (now() - datetime.fromisoformat(quote['stamp'])).total_seconds() > meta['stale_after']
                rows.append({
                    'source': source, 'label': meta['label'], 'color': meta['color'],
                    'price': quote['price'] / SCALE if quote else None,
                    'stamp': quote['stamp'] if quote else None,
                    'change': change, 'stale': stale,
                    'error': self.source_status[source]['error'],
                    'last_attempt': self.source_status[source]['last_attempt'],
                })
            return {'pair': pair, 'label': PAIRS[pair]['label'], 'sources': rows, 'server_time': now().isoformat()}
        if path == '/api/chart' and method == 'GET':
            pair = requested_pair()
            requested = (query.get('sources') or [''])[0]
            sources = [item for item in requested.split(',') if item] or list(SOURCES)
            sources = [source for source in sources if source in SOURCES] or list(SOURCES)
            start = (query.get('start') or [])[0]
            end = (query.get('end') or [])[0]
            if not start or not end:
                end = now().isoformat()
                start = (now() - timedelta(days=1)).isoformat()
            interval = (query.get('interval') or [''])[0]
            interval = int(interval) if str(interval).isdigit() else auto_interval(start, end)
            data = chart_series(self.path, pair, sources, start, end, interval)
            data.update({'pair': pair, 'label': PAIRS[pair]['label'], 'start': start, 'end': end})
            return data
        if path == '/api/sources' and method == 'GET':
            pair = (query.get('pair') or [DEFAULT_PAIR])[0]
            pair_meta(pair)
            with connect(self.path) as db:
                latest = self.latest_rows(db, pair)
            payload = []
            for source, meta in SOURCES.items():
                quote = (latest.get(source) or {}).get('quote')
                stale = True
                if quote:
                    stale = (now() - datetime.fromisoformat(quote['stamp'])).total_seconds() > meta['stale_after']
                payload.append({'id': source, 'label': meta['label'], 'color': meta['color'],
                                'stale_after': meta['stale_after'], 'stale': stale,
                                'error': self.source_status[source]['error'],
                                'last_attempt': self.source_status[source]['last_attempt'],
                                'quote': {'price': quote['price'] / SCALE, 'stamp': quote['stamp']} if quote else None})
            return {'sources': payload, 'default': DEFAULT_SOURCE, 'pair': pair}
        if path == '/api/status' and method == 'GET':
            source, pair = requested_source(), requested_pair()
            meta = SOURCES[source]
            with connect(self.path) as db:
                q = db.execute('SELECT * FROM quotes WHERE pair=? AND source=? ORDER BY stamp DESC LIMIT 1',
                               (pair, source)).fetchone()
                credits = db.execute('SELECT credits FROM users WHERE id=?', (uid,)).fetchone()[0]
            stale = not q or (now() - datetime.fromisoformat(q['stamp'])).total_seconds() > meta['stale_after']
            return {'demo': self.demo, 'quote': {'price': q['price'] / SCALE, 'stamp': q['stamp']} if q else None,
                    'source': source, 'source_label': meta['label'], 'stale_after': meta['stale_after'],
                    'pair': pair, 'pair_label': PAIRS[pair]['label'],
                    'stale': stale, 'error': self.source_status[source]['error'], 'poll_seconds': 30,
                    'notifications_ready': not self.demo and self.sender.configured(),
                    'template_id': os.getenv('WX_TEMPLATE_ID', ''), 'subscription_credits': credits}
        if path == '/api/subscriptions' and method == 'POST':
            if self.demo or not self.sender.configured():
                raise ValueError('微信订阅消息尚未接入，演示模式不会发送通知')
            if body.get('accepted') is not True:
                raise ValueError('请先授权微信订阅消息')
            with connect(self.path) as db:
                # A local hint only; WeChat ultimately enforces real subscription quota.
                db.execute('UPDATE users SET credits=min(credits+1,50) WHERE id=?', (uid,))
            return {'ok': True}
        if path == '/api/rules':
            if method == 'GET':
                with connect(self.path) as db:
                    rows = [dict(r) for r in db.execute('SELECT * FROM rules WHERE user_id=? ORDER BY created DESC', (uid,))]
                for row in rows:
                    row['amount'] /= SCALE
                    row['source_label'] = SOURCES.get(row.get('source'), {}).get('label', row.get('source'))
                    row['pair_label'] = PAIRS.get(row.get('pair'), {}).get('label', row.get('pair'))
                    row.pop('user_id')
                return {'rules': rows}
            if method == 'POST':
                values = validate_rule(body)
                rid = uuid.uuid4().hex
                with connect(self.path) as db:
                    if db.execute('SELECT count(*) FROM rules WHERE user_id=?', (uid,)).fetchone()[0] >= 20:
                        raise ValueError('最多保留 20 条监测规则')
                    db.execute('INSERT INTO rules(id,user_id,kind,start,end,amount,direction,source,pair,created) '
                               'VALUES(?,?,?,?,?,?,?,?,?,?)', (rid, uid, *values, now().isoformat()))
                return {'id': rid}
        if path.startswith('/api/rules/') and method == 'DELETE':
            with connect(self.path) as db:
                deleted = db.execute('DELETE FROM rules WHERE id=? AND user_id=?', (path.rsplit('/', 1)[-1], uid)).rowcount
            return (200 if deleted else 404), {'ok': bool(deleted)}
        if path == '/api/alerts' and method == 'GET':
            with connect(self.path) as db:
                rows = [dict(r) for r in db.execute(
                    'SELECT a.id,a.side,a.price,a.stamp,a.status,a.detail,'
                    'r.source AS source, r.pair AS pair FROM alerts a LEFT JOIN rules r ON r.id=a.rule_id '
                    'WHERE a.user_id=? ORDER BY a.id DESC LIMIT 50', (uid,))]
            for row in rows:
                row['price'] /= SCALE
                row['source_label'] = SOURCES.get(row.get('source'), {}).get('label', '')
                row['pair_label'] = PAIRS.get(row.get('pair'), {}).get('label', '')
            return {'alerts': rows}
        if path in ('/api/history', '/api/analysis') and method == 'GET':
            pair = (query.get('pair') or [DEFAULT_PAIR])[0]
            points = history(self.path, query.get('start', [''])[0], query.get('end', [''])[0],
                             requested_source(), pair)
            if path.endswith('analysis'):
                return analyze(summarize(points))
            sampled = points
            if len(points) > 1000:
                sampled = [points[0]]
                size = math.ceil(len(points) / 450)
                for i in range(1, len(points) - 1, size):
                    bucket = points[i:min(i + size, len(points) - 1)]
                    if bucket:
                        pair_map = {p['stamp']: p for p in (min(bucket, key=lambda p: p['price']),
                                                            max(bucket, key=lambda p: p['price']))}
                        sampled.extend(sorted(pair_map.values(), key=lambda p: p['stamp']))
                sampled.append(points[-1])
            return {'points': sampled, 'total': len(points),
                    'stats': summarize(points) if len(points) > 1 else None}
        return 404, {'error': '接口不存在'}


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not log login codes, auth headers or AI prompts.
            pass

        def run_request(self):
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 <= n <= 16384:
                    raise ValueError('请求体过大')
                body = json.loads(self.rfile.read(n)) if n else {}
                if not isinstance(body, dict):
                    raise ValueError('请求必须为 JSON 对象')
                url = urlsplit(self.path)
                result = app.api(self.command, url.path, parse_qs(url.query), body,
                                 self.headers.get('Authorization', '').removeprefix('Bearer '))
                status, payload = result if isinstance(result, tuple) else (200, result)
            except WechatLoginError as error:
                status, payload = error.status, {'error': str(error), 'code': error.code}
                if error.wechat_errcode is not None:
                    payload['wechat_errcode'] = error.wechat_errcode
                LOG.warning('WeChat login/config failed: %s (wechat_errcode=%s)', error.code, error.wechat_errcode)
            except (ValueError, TypeError, KeyError):
                status, payload = 400, {'error': '请求或服务配置无效，请检查时段、金额、登录及配置'}
            except Exception:
                LOG.warning('API operation failed (sensitive details suppressed)')
                status, payload = 502, {'error': '外部服务暂不可用，请稍后重试'}
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        do_GET = run_request
        do_POST = run_request
        do_DELETE = run_request
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db')
    parser.add_argument('--check-quote', action='store_true',
                        help='Fetch a real BOC quote once, without starting the server or requiring WeChat credentials')
    args = parser.parse_args()
    if args.check_quote:
        try:
            price, stamp = fetch_all('boc_buy')['USD']
        except Exception as error:
            parser.exit(1, quote_failure(error) + '\n')
        print(json.dumps({'source': '中国银行美元现汇买入价', 'price': price / SCALE, 'stamp': stamp, 'demo': False},
                         ensure_ascii=False))
        return
    path = args.db or ('.data/demo.sqlite3' if args.demo else '.data/rates.sqlite3')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    backup = None if args.demo else BackupManager(path)
    if backup is not None:
        backup.restore_if_missing()
    app = Application(path, args.demo)
    app.backup = backup
    if backup is not None and backup.start():
        LOG.info('object storage backup enabled (every %ss)', backup.interval)
    if args.demo:
        # Explicit synthetic history, isolated from production data.
        with connect(path) as db:
            if not db.execute('SELECT 1 FROM quotes LIMIT 1').fetchone():
                end = now().replace(microsecond=0)
                for i in range(240):
                    stamp = (end - timedelta(minutes=240 - i)).isoformat()
                    price = round((6.85 + .025 * math.sin((end.timestamp() - (240 - i) * 60) / 900)) * SCALE)
                    db.execute('INSERT INTO quotes(pair,source,stamp,price,observed) VALUES(?,?,?,?,?)',
                               ('USD', DEFAULT_SOURCE, stamp, price, end.isoformat()))

    if os.getenv('DISABLE_COLLECTOR', '').strip() in ('1', 'true', 'yes'):
        LOG.info('collector disabled by DISABLE_COLLECTOR')
    def worker():
        interval = max(15, int(os.getenv('COLLECT_SECONDS', '30')))
        while True:
            try:
                app.collect()
            except Exception:
                LOG.warning('collector cycle failed')
            time.sleep(interval)

    if os.getenv('DISABLE_COLLECTOR', '').strip() in ('1', 'true', 'yes'):
        pass
    else:
        threading.Thread(target=worker, daemon=True).start()
    logging.basicConfig(level=logging.INFO)
    LOG.info('mode=%s port=%s instance=%s', 'SYNTHETIC DEMO' if args.demo else 'LIVE', args.port, socket.gethostname())

    def stop(signum, frame):
        if backup is not None:
            backup.backup_once()
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    ThreadingHTTPServer((args.host, args.port), handler(app)).serve_forever()


if __name__ == '__main__':
    main()
