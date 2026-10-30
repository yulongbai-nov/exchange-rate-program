"""BOC USD cashless buying quotes; all prices are integer millionths of CNY/USD."""
import json
import re
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
SCALE = 1_000_000

# Quote sources shown in the mini program. `stale_after` decides when the UI
# warns that the latest quote is old; bank quotes are published much less often
# than the public CNY/CNH market feeds, so their threshold is larger.
SOURCES = {
    'boc':      {'label': '中行现汇买入价', 'bank': '中国银行',       'stale_after': 3600},
    'sina_cny': {'label': '在岸人民币',     'bank': '新浪财经·在岸', 'stale_after': 300},
    'sina_cnh': {'label': '离岸人民币',     'bank': '新浪财经·离岸', 'stale_after': 300},
}
DEFAULT_SOURCE = 'boc'


def source_meta(source):
    meta = SOURCES.get(source or DEFAULT_SOURCE)
    if meta is None:
        raise ValueError('未知的报价来源')
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


def parse_boc(html):
    p = TableParser()
    p.feed(html)
    price_col = None
    for row in p.rows:
        if '现汇买入价' in row:
            price_col = row.index('现汇买入价')
        if row and row[0] == '美元':
            if price_col is None or len(row) < 7:
                raise ValueError('中行表格结构变化：无法确认现汇买入价列')
            price = units(Decimal(row[price_col].replace(',', '')) / 100)
            text = ' '.join(row[-2:])
            match = re.search(r'(\d{4}[./-]\d{2}[./-]\d{2})\s*(\d{2}:\d{2}:\d{2})', text)
            if not match:
                raise ValueError('无法解析中行报价时间')
            normalized = ' '.join(match.groups()).replace('.', '-').replace('/', '-')
            stamp = datetime.strptime(normalized, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ)
            return price, stamp.isoformat()
    raise ValueError('中行页面未找到美元现汇买入价')


def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db(path):
    with connect(path) as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS quotes(source TEXT NOT NULL DEFAULT 'boc', stamp TEXT NOT NULL,
          price INTEGER NOT NULL, observed TEXT NOT NULL, PRIMARY KEY(source, stamp));
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, openid TEXT UNIQUE NOT NULL, credits INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
          start TEXT NOT NULL, end TEXT NOT NULL, amount INTEGER NOT NULL, direction TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'boc');
        CREATE TABLE IF NOT EXISTS rule_days(rule_id TEXT NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
          day TEXT NOT NULL, baseline INTEGER NOT NULL, PRIMARY KEY(rule_id,day));
        CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id TEXT NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL, day TEXT NOT NULL, side TEXT NOT NULL, price INTEGER NOT NULL,
          stamp TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '',
          UNIQUE(rule_id,day,side));
        ''')
        # Migrate databases created before multiple quote sources existed.
        quote_columns = [row[1] for row in db.execute('PRAGMA table_info(quotes)')]
        if 'source' not in quote_columns:
            db.executescript('''
            ALTER TABLE quotes RENAME TO quotes_legacy;
            CREATE TABLE quotes(source TEXT NOT NULL DEFAULT 'boc', stamp TEXT NOT NULL,
              price INTEGER NOT NULL, observed TEXT NOT NULL, PRIMARY KEY(source, stamp));
            INSERT INTO quotes(source,stamp,price,observed)
              SELECT 'boc',stamp,price,observed FROM quotes_legacy;
            DROP TABLE quotes_legacy;
            ''')
        rule_columns = [row[1] for row in db.execute('PRAGMA table_info(rules)')]
        if 'source' not in rule_columns:
            db.execute("ALTER TABLE rules ADD COLUMN source TEXT NOT NULL DEFAULT 'boc'")


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
    return kind, body['start'], body['end'], units(body.get('amount')), direction, source


def window_day(rule, stamp):
    time = stamp.strftime('%H:%M')
    if rule['start'] < rule['end']:
        return stamp.date().isoformat() if rule['start'] <= time <= rule['end'] else None
    if time >= rule['start']:
        return stamp.date().isoformat()
    if time <= rule['end']:
        return (stamp.date() - timedelta(days=1)).isoformat()
    return None


def record_quote(path, price, stamp, clock=None, source=DEFAULT_SOURCE):
    clock = clock or now()
    source_meta(source)
    source_time = datetime.fromisoformat(stamp)
    # Never send current alerts from old, future, duplicate or out-of-order quotes.
    with connect(path) as db:
        latest = db.execute('SELECT max(stamp) FROM quotes WHERE source=?', (source,)).fetchone()[0]
        inserted = db.execute('INSERT OR IGNORE INTO quotes(source,stamp,price,observed) VALUES(?,?,?,?)',
                              (source, stamp, price, clock.isoformat())).rowcount
        if not inserted or (latest and stamp <= latest) or not -60 <= (clock-source_time).total_seconds() <= 600:
            return 0
        count = 0
        for rule in db.execute('SELECT * FROM rules WHERE enabled=1 AND source=?', (source,)).fetchall():
            if stamp < rule['created']:
                continue
            day = window_day(rule, source_time)
            if day is None:
                continue
            db.execute('INSERT OR IGNORE INTO rule_days VALUES(?,?,?)', (rule['id'], day, price))
            base = db.execute('SELECT baseline FROM rule_days WHERE rule_id=? AND day=?', (rule['id'],day)).fetchone()[0]
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
                                    (rule['id'],rule['user_id'],day,side,price,stamp)).rowcount
        return count


def history(path, start, end, source=DEFAULT_SOURCE):
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if a.tzinfo is None or b.tzinfo is None or a > b:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('请选择有效起止时间（含时区）') from None
    source_meta(source)
    with connect(path) as db:
        rows = db.execute('SELECT stamp,price FROM quotes WHERE source=? AND stamp>=? AND stamp<=? ORDER BY stamp',
                          (source, a.astimezone(TZ).isoformat(), b.astimezone(TZ).isoformat())).fetchall()
    return [{'stamp': r['stamp'], 'price': r['price']/SCALE} for r in rows]


def summarize(points):
    if len(points) < 2:
        raise ValueError('所选时段至少需要两个报价，尚无足够历史数据')
    values = [p['price'] for p in points]
    return {'count': len(points), 'first': values[0], 'last': values[-1], 'low': min(values),
            'high': max(values), 'change': round(values[-1]-values[0], 6),
            'change_percent': round((values[-1]/values[0]-1)*100, 4),
            'from': points[0]['stamp'], 'to': points[-1]['stamp']}
