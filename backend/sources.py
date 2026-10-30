"""Public quote sources. All prices are CNY per USD in integer millionths."""
import re
import urllib.request
from datetime import datetime

from backend.core import SCALE, source_meta
from backend.integrations import fetch_quote

SINA_URL = 'https://hq.sinajs.cn/list=%s'
SINA_SYMBOLS = {'sina_cny': 'fx_susdcny', 'sina_cnh': 'fx_susdcnh'}


def _sina(symbol):
    """Sina public FX feed: time,bid,ask,prev_close,volume,high,...,last,..,date."""
    request = urllib.request.Request(
        SINA_URL % symbol,
        headers={'Referer': 'https://finance.sina.com.cn',
                 'User-Agent': 'ExchangeRateMonitor/0.1'})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read().decode('gb18030', 'ignore')
    match = re.search(r'"([^"]*)"', payload)
    if not match:
        raise ValueError('新浪行情返回格式异常')
    fields = match.group(1).split(',')
    if len(fields) < 18:
        raise ValueError('新浪行情字段不足')
    try:
        price = float(fields[8] or fields[1])
    except ValueError:
        raise ValueError('新浪行情价格格式异常') from None
    stamp = f'{fields[17]}T{fields[0]}+08:00'
    datetime.fromisoformat(stamp)
    if not 1 < price < 100:
        raise ValueError('新浪行情价格超出合理范围')
    return int(round(price * SCALE)), stamp


def fetch(source):
    source_meta(source)
    if source == 'boc':
        return fetch_quote()
    symbol = SINA_SYMBOLS.get(source)
    if symbol:
        return _sina(symbol)
    raise ValueError('未知的报价来源')
