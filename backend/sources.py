"""Live quote fetchers for the five supported sources."""
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from backend.core import PAIRS, SCALE, TZ, parse_boc_table, source_meta

BOC_URL = 'https://www.boc.cn/sourcedb/whpj/'
SINA_URL = 'https://hq.sinajs.cn/list=%s'
DAILY_URL = 'https://open.er-api.com/v6/latest/USD'
YAHOO_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=5m&range=1d'
YAHOO_TIMEOUT = 8

SINA_SYMBOLS = {
    'USD': 'fx_susdcny', 'EUR': 'fx_seurcny', 'GBP': 'fx_sgbpcny', 'JPY': 'fx_sjpycny',
    'HKD': 'fx_shkdcny', 'AUD': 'fx_saudcny', 'CAD': 'fx_scadcny', 'SGD': 'fx_ssgdcny',
    'CHF': 'fx_schfcny', 'NZD': 'fx_snzdcny', 'KRW': 'fx_skrwcny', 'THB': 'fx_sthbcny',
    'MYR': 'fx_smyrcny', 'RUB': 'fx_srubcny', 'INR': 'fx_sinrcny', 'TWD': 'fx_stwdcny',
}
CACHE = {}


def _cached(key, ttl, factory):
    item = CACHE.get(key)
    if item and item[0] > time.time():
        return item[1]
    data = factory()
    CACHE[key] = (time.time() + ttl, data)
    return data


def _get(url, headers=None, timeout=25):
    request = urllib.request.Request(url, headers={'User-Agent': 'ExchangeRateMonitor/0.1',
                                                   **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _boc():
    raw = _get(BOC_URL)
    for encoding in ('utf-8', 'gb18030'):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if '美元' in text:
            return parse_boc_table(text)
    raise ValueError('中行页面编码无法识别')


def _sina(symbols):
    query = ','.join(symbols[pair] for pair in symbols)
    text = _get(SINA_URL % query, headers={'Referer': 'https://finance.sina.com.cn'}).decode('gb18030', 'ignore')
    result = {}
    for pair, symbol in symbols.items():
        match = re.search(r'var hq_str_%s="([^"]*)"' % symbol, text)
        if not match:
            continue
        fields = match.group(1).split(',')
        if len(fields) < 18:
            continue
        try:
            price = float(fields[8] or fields[1])
            stamp = f'{fields[17]}T{fields[0]}+08:00'
            datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if 0.000001 < price < 1000:
            result[pair] = (int(round(price * SCALE)), stamp)
    if not result:
        raise ValueError('新浪行情未返回可用数据')
    return result


def _daily():
    payload = json.loads(_get(DAILY_URL).decode('utf-8'))
    rates = payload.get('rates') or {}
    cny = rates.get('CNY')
    if payload.get('result') != 'success' or not cny:
        raise ValueError('每日汇率接口不可用')
    updated = payload.get('time_last_update_utc')
    try:
        stamp = datetime.strptime(updated, '%a, %d %b %Y %H:%M:%S %z').astimezone(TZ).isoformat()
    except (TypeError, ValueError):
        stamp = datetime.now(TZ).isoformat(timespec='seconds')
    result = {}
    for pair in PAIRS:
        rate = rates.get(pair)
        if not rate:
            continue
        price = cny / rate
        if 0.000001 < price < 1000:
            result[pair] = (int(round(price * SCALE)), stamp)
    if not result:
        raise ValueError('每日汇率未返回可用数据')
    return result


def _yahoo_pair(pair):
    symbol = 'CNY=X' if pair == 'USD' else pair + 'CNY=X'
    payload = json.loads(_get(YAHOO_URL % symbol, timeout=YAHOO_TIMEOUT).decode('utf-8'))
    result = payload['chart']['result'][0]
    meta = result.get('meta') or {}
    closes = (result.get('indicators', {}).get('quote') or [{}])[0].get('close') or []
    price = next((value for value in reversed(closes) if value), meta.get('regularMarketPrice'))
    epoch = meta.get('regularMarketTime')
    if not price or not epoch or not 0.000001 < float(price) < 1000:
        raise ValueError('Yahoo 行情不可用')
    stamp = datetime.fromtimestamp(int(epoch), TZ).isoformat()
    return pair, (int(round(float(price) * SCALE)), stamp)


def _yahoo():
    result = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pair: pool.submit(_yahoo_pair, pair) for pair in PAIRS}
        for pair, future in futures.items():
            try:
                pair, quote = future.result(timeout=YAHOO_TIMEOUT + 4)
                result[pair] = quote
            except Exception:
                continue
    if not result:
        raise ValueError('Yahoo 行情不可用')
    return result


def fetch_all(source):
    """Return {pair: (integer price, ISO stamp)} for one source."""
    source_meta(source)
    if source in ('boc_buy', 'boc_cash'):
        table = _cached('boc', 50, _boc)
        return {pair: entry[source] for pair, entry in table.items() if source in entry}
    if source == 'sina':
        return _cached('sina', 20, lambda: _sina(SINA_SYMBOLS))
    if source == 'cnh':
        return _cached('cnh', 20, lambda: _sina({'USD': 'fx_susdcnh'}))
    if source == 'daily':
        return _cached('daily', 1800, _daily)
    if source == 'yahoo':
        return _cached('yahoo', 45, _yahoo)
    raise ValueError('未知的报价来源')
