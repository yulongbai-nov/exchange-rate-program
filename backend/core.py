"""Quotes for popular CNY pairs; prices are integer millionths of CNY per unit."""
import re
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
SCALE = 1_000_000

# 16 popular CNY pairs, in display order.
PAIRS = {
    'USD': {'label': '美元'}, 'EUR': {'label': '欧元'}, 'GBP': {'label': '英镑'},
    'JPY': {'label': '日元'}, 'HKD': {'label': '港币'}, 'AUD': {'label': '澳元'},
    'CAD': {'label': '加元'}, 'SGD': {'label': '新加坡元'}, 'CHF': {'label': '瑞士法郎'},
    'NZD': {'label': '新西兰元'}, 'KRW': {'label': '韩元'}, 'THB': {'label': '泰铢'},
    'MYR': {'label': '林吉特'}, 'RUB': {'label': '卢布'}, 'INR': {'label': '印度卢比'},
    'TWD': {'label': '新台币'},
}
DEFAULT_PAIR = 'USD'

# Up to five quote sources. `accept_age` bounds how old a quote may be before it
# can trigger alerts; `stale_after` decides when the UI marks it outdated.
SOURCES = {
    'boc_buy':  {'label': '中行现汇买入', 'bank': '中国银行', 'color': '#1a73e8',
                 'stale_after': 3600, 'accept_age': 3600},
    'boc_cash': {'label': '中行现钞买入', 'bank': '中国银行', 'color': '#00897b',
                 'stale_after': 3600, 'accept_age': 3600},
    'sina':     {'label': '新浪在岸', 'bank': '新浪财经·在岸', 'color': '#43a047',
                 'stale_after': 300, 'accept_age': 900},
    'cnh':      {'label': '新浪离岸', 'bank': '新浪财经·离岸', 'color': '#f57c00',
                 'stale_after': 300, 'accept_age': 900},
    'yahoo':    {'label': 'Yahoo Finance', 'bank': 'Yahoo Finance', 'color': '#8e44ad',
                 'stale_after': 300, 'accept_age': 900},
    'daily':    {'label': '每日中间价', 'bank': '公开汇率日更', 'color': '#757575',
                 'stale_after': 172800, 'accept_age': 172800},
}
DEFAULT_SOURCE = 'boc_buy'


def source_meta(source):
    meta = SOURCES.get(source or DEFAULT_SOURCE)
    if meta is None:
        raise ValueError('未知的报价来源')
    return meta


def pair_meta(pair):
    meta = PAIRS.get(pair or DEFAULT_PAIR)
    if meta is None:
        raise ValueError('未知的货币对')
    return meta


def now():
    return datetime.now(TZ)


def units(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite() or not 0 < d < 100:
            raise ValueError('金额须大于 0 且小于 100 元')
        result = int((d * SCALE).quantize(Decimal('1')))
        if result < 1:
            raise ValueError('金额精度最多为六位小数')
        return result
    except (InvalidOperation, TypeError):
        raise ValueError('无效金额') from None


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row, self.cell = None, None


BOC_NAMES = {
    '美元': 'USD', '欧元': 'EUR', '英镑': 'GBP', '日元': 'JPY', '港币': 'HKD',
    '澳大利亚元': 'AUD', '加拿大元': 'CAD', '新加坡元': 'SGD', '瑞士法郎': 'CHF',
    '新西兰元': 'NZD', '韩国元': 'KRW', '泰国铢': 'THB', '林吉特': 'MYR',
    '卢布': 'RUB', '印度卢比': 'INR', '新台币': 'TWD',
}


def parse_boc_table(html):
    """Return {pair: {'boc_buy': (price, stamp), 'boc_cash': (price, stamp)}}."""
    p = TableParser()
    p.feed(html)
    cashless = cash = None
    result = {}
    for row in p.rows:
        if '现汇买入价' in row:
            cashless = row.index('现汇买入价')
            cash = row.index('现钞买入价') if '现钞买入价' in row else None
        pair = BOC_NAMES.get(row[0]) if row else None
        if not pair or cashless is None or len(row) <= cashless:
            continue
        text = ' '.join(row[-2:])
        match = re.search(r'(\d{4}[./-]\d{2}[./-]\d{2})\s*(\d{2}:\d{2}:\d{2})', text)
        if not match:
            continue
        normalized = ' '.join(match.groups()).replace('.', '-').replace('/', '-')
        stamp = datetime.strptime(normalized, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ).isoformat()
        entry = {}
        for source, index in (('boc_buy', cashless), ('boc_cash', cash)):
            if index is None or index >= len(row):
                continue
            try:
                entry[source] = (units(Decimal(row[index].replace(',', '')) / 100), stamp)
            except (ValueError, InvalidOperation):
                pass
        if entry:
            result[pair] = entry
    if not result:
        raise ValueError('中行页面未找到可识别的外汇牌价')
    return result


def parse_boc(html):
    """Backwards-compatible helper: cashless USD buying price from a BOC page."""
    quote = parse_boc_table(html).get('USD', {}).get('boc_buy')
    if not quote:
        raise ValueError('中行页面未找到美元现汇买入价')
    return quote


def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db(path):
    with connect(path) as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS quotes(pair TEXT NOT NULL DEFAULT 'USD',
          source TEXT NOT NULL DEFAULT 'boc_buy', stamp TEXT NOT NULL,
          price INTEGER NOT NULL, observed TEXT NOT NULL,
          PRIMARY KEY(pair, source, stamp));
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, openid TEXT UNIQUE NOT NULL, credits INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
          start TEXT NOT NULL, end TEXT NOT NULL, amount INTEGER NOT NULL, direction TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'boc_buy', pair TEXT NOT NULL DEFAULT 'USD');
        CREATE TABLE IF NOT EXISTS rule_days(rule_id TEXT NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
          day TEXT NOT NULL, baseline INTEGER NOT NULL, PRIMARY KEY(rule_id,day));
        CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id TEXT NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL, day TEXT NOT NULL, side TEXT NOT NULL, price INTEGER NOT NULL,
          stamp TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '',
          UNIQUE(rule_id,day,side));
        ''')
        # Migrate older databases: v0 (stamp PK) and v1 (source + stamp PK).
        columns = [row[1] for row in db.execute('PRAGMA table_info(quotes)')]
        if 'source' not in columns:
            db.executescript('''
            ALTER TABLE quotes RENAME TO quotes_legacy;
            CREATE TABLE quotes(pair TEXT NOT NULL DEFAULT 'USD', source TEXT NOT NULL DEFAULT 'boc_buy',
              stamp TEXT NOT NULL, price INTEGER NOT NULL, observed TEXT NOT NULL,
              PRIMARY KEY(pair, source, stamp));
            INSERT INTO quotes(pair,source,stamp,price,observed)
              SELECT 'USD','boc_buy',stamp,price,observed FROM quotes_legacy;
            DROP TABLE quotes_legacy;
            ''')
        elif 'pair' not in columns:
            db.executescript('''
            ALTER TABLE quotes RENAME TO quotes_legacy;
            CREATE TABLE quotes(pair TEXT NOT NULL DEFAULT 'USD', source TEXT NOT NULL DEFAULT 'boc_buy',
              stamp TEXT NOT NULL, price INTEGER NOT NULL, observed TEXT NOT NULL,
              PRIMARY KEY(pair, source, stamp));
            INSERT INTO quotes(pair,source,stamp,price,observed)
              SELECT 'USD',source,stamp,price,observed FROM quotes_legacy;
            DROP TABLE quotes_legacy;
            ''')
        rule_columns = [row[1] for row in db.execute('PRAGMA table_info(rules)')]
        if 'source' not in rule_columns:
            db.execute("ALTER TABLE rules ADD COLUMN source TEXT NOT NULL DEFAULT 'boc_buy'")
        if 'pair' not in rule_columns:
            db.execute("ALTER TABLE rules ADD COLUMN pair TEXT NOT NULL DEFAULT 'USD'")


def validate_rule(body):
    kind = body.get('kind')
    if kind not in ('movement', 'target'):
        raise ValueError('请选择波动或目标价提醒')
    for key in ('start', 'end'):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(body.get(key, ''))):
            raise ValueError('时段格式为 HH:MM')
    if body['start'] == body['end']:
        raise ValueError('开始和结束时间不能相同；全天可设置 00:00–23:59')
    direction = body.get('direction', 'above')
    if direction not in ('above', 'below'):
        raise ValueError('目标方向无效')
    source = body.get('source') or DEFAULT_SOURCE
    source_meta(source)
    pair = (body.get('pair') or DEFAULT_PAIR).upper()
    pair_meta(pair)
    return kind, body['start'], body['end'], units(body.get('amount')), direction, source, pair


def window_day(rule, stamp):
    time = stamp.strftime('%H:%M')
    if rule['start'] < rule['end']:
        return stamp.date().isoformat() if rule['start'] <= time <= rule['end'] else None
    if time >= rule['start']:
        return stamp.date().isoformat()
    if time <= rule['end']:
        return (stamp.date() - timedelta(days=1)).isoformat()
    return None


def record_quote(path, price, stamp, clock=None, source=DEFAULT_SOURCE, pair=DEFAULT_PAIR):
    clock = clock or now()
    source_meta(source)
    pair_meta(pair)
    source_time = datetime.fromisoformat(stamp)
    accept_age = SOURCES[source]['accept_age']
    # Never send current alerts from old, future, duplicate or out-of-order quotes.
    with connect(path) as db:
        latest = db.execute('SELECT max(stamp) FROM quotes WHERE pair=? AND source=?', (pair, source)).fetchone()[0]
        inserted = db.execute('INSERT OR IGNORE INTO quotes(pair,source,stamp,price,observed) VALUES(?,?,?,?,?)',
                              (pair, source, stamp, price, clock.isoformat())).rowcount
        age = (clock - source_time).total_seconds()
        if not inserted or (latest and stamp <= latest) or not -60 <= age <= accept_age:
            return 0
        count = 0
        for rule in db.execute('SELECT * FROM rules WHERE enabled=1 AND pair=? AND source=?', (pair, source)).fetchall():
            if stamp < rule['created']:
                continue
            day = window_day(rule, source_time)
            if day is None:
                continue
            db.execute('INSERT OR IGNORE INTO rule_days VALUES(?,?,?)', (rule['id'], day, price))
            base = db.execute('SELECT baseline FROM rule_days WHERE rule_id=? AND day=?', (rule['id'], day)).fetchone()[0]
            side = None
            if rule['kind'] == 'movement':
                if price - base >= rule['amount']:
                    side = 'up'
                elif base - price >= rule['amount']:
                    side = 'down'
            elif rule['direction'] == 'above' and price >= rule['amount']:
                side = 'above'
            elif rule['direction'] == 'below' and price <= rule['amount']:
                side = 'below'
            if side:
                count += db.execute('INSERT OR IGNORE INTO alerts(rule_id,user_id,day,side,price,stamp) VALUES(?,?,?,?,?,?)',
                                    (rule['id'], rule['user_id'], day, side, price, stamp)).rowcount
        return count


def _range(start, end):
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if a.tzinfo is None or b.tzinfo is None or a > b:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('请选择有效起止时间（含时区）') from None
    return a.astimezone(TZ).isoformat(), b.astimezone(TZ).isoformat()


def history(path, start, end, source=DEFAULT_SOURCE, pair=DEFAULT_PAIR):
    source_meta(source)
    pair_meta(pair)
    a, b = _range(start, end)
    with connect(path) as db:
        rows = db.execute('SELECT stamp,price FROM quotes WHERE pair=? AND source=? AND stamp>=? AND stamp<=? ORDER BY stamp',
                          (pair, source, a, b)).fetchall()
    return [{'stamp': r['stamp'], 'price': r['price'] / SCALE} for r in rows]


def chart_series(path, pair, sources, start, end, interval=60):
    """Aligned buckets for a multi-source line chart (nulls keep real gaps)."""
    pair_meta(pair)
    sources = sources or [DEFAULT_SOURCE]
    for source in sources:
        source_meta(source)
    a, b = _range(start, end)
    interval = max(30, min(int(interval or 60), 2592000))
    with connect(path) as db:
        rows = db.execute('SELECT source,stamp,price FROM quotes WHERE pair=? AND stamp>=? AND stamp<=? '
                          'AND source IN (%s) ORDER BY stamp' % ','.join('?' * len(sources)),
                          (pair, a, b, *sources)).fetchall()
    if not rows:
        return {'buckets': [], 'series': [], 'interval': interval}
    def epoch(iso):
        return int(datetime.fromisoformat(iso).timestamp())
    first = epoch(rows[0]['stamp']) // interval * interval
    last = epoch(rows[-1]['stamp']) // interval * interval
    buckets = list(range(first, last + interval, interval))
    if len(buckets) > 600:
        raise ValueError('所选时段数据过多，请缩小范围或增大粒度')
    lookup = {bucket: {} for bucket in buckets}
    for row in rows:
        lookup[epoch(row['stamp']) // interval * interval][row['source']] = row['price'] / SCALE
    series = []
    for source in sources:
        meta = SOURCES[source]
        series.append({'source': source, 'label': meta['label'], 'color': meta['color'],
                       'values': [lookup[bucket].get(source) for bucket in buckets]})
    return {'buckets': [datetime.fromtimestamp(bucket, TZ).isoformat() for bucket in buckets],
            'series': series, 'interval': interval}


def summarize(points):
    if len(points) < 2:
        raise ValueError('所选时段至少需要两个报价，尚无足够历史数据')
    values = [p['price'] for p in points]
    return {'count': len(values), 'first': values[0], 'last': values[-1], 'low': min(values),
            'high': max(values), 'change': round(values[-1] - values[0], 6),
            'change_percent': round((values[-1] / values[0] - 1) * 100, 4),
            'from': points[0]['stamp'], 'to': points[-1]['stamp']}
