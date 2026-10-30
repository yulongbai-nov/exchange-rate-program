#!/usr/bin/env python3
"""Build data/history-seed.json.gz with 10 years of daily CNY history.

Sources: Yahoo Finance daily closes (market proxy -> source "sina") and ECB
reference rates via frankfurter.app (official daily proxy -> source "daily").
Run once locally; the file is baked into the image and imported once on boot.
"""
import gzip
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'history-seed.json.gz'
TZ = ZoneInfo('Asia/Shanghai')
SCALE = 1_000_000
PAIRS = ['USD', 'EUR', 'GBP', 'JPY', 'HKD', 'AUD', 'CAD', 'SGD', 'CHF',
         'NZD', 'KRW', 'THB', 'MYR', 'RUB', 'INR', 'TWD']
YAHOO = {pair: ('CNY=X' if pair == 'USD' else pair + 'CNY=X') for pair in PAIRS}
ECB = [pair for pair in PAIRS if pair != 'TWD']


def fetch(url, timeout=60):
    request = urllib.request.Request(url, headers={'User-Agent': 'ExchangeRateMonitor/0.1'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def stamp_for(epoch):
    return datetime.fromtimestamp(epoch, TZ).strftime('%Y-%m-%dT15:00:00+08:00')


def main():
    rows = []
    print('== Yahoo Finance daily closes ==')
    for pair, symbol in YAHOO.items():
        try:
            data = fetch(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=10y')
            result = data['chart']['result'][0]
            stamps = result.get('timestamp') or []
            closes = (result['indicators']['quote'][0].get('close') or [])
            count = 0
            for epoch, close in zip(stamps, closes):
                if not close or not 0.000001 < close < 1000:
                    continue
                rows.append({'pair': pair, 'source': 'sina', 'stamp': stamp_for(epoch),
                             'price': int(round(close * SCALE))})
                count += 1
            print(f'{pair}: {count} daily points')
        except Exception as error:
            print(f'{pair}: FAILED {type(error).__name__}: {error}')
    print('== ECB reference rates via frankfurter ==')
    try:
        data = fetch(f'https://api.frankfurter.app/2015-01-01..2026-10-30?from=EUR&to=CNY,{",".join(ECB)}')
        for date, rate in sorted(data.get('rates', {}).items()):
            cny, eur_base = rate.get('CNY'), None
            if not cny:
                continue
            stamp = f'{date}T15:00:00+08:00'
            for pair in ECB:
                value = rate.get(pair)
                if not value or not 0 < value:
                    continue
                price = cny / value
                if 0.000001 < price < 1000:
                    rows.append({'pair': pair, 'source': 'daily', 'stamp': stamp,
                                 'price': int(round(price * SCALE))})
        print(f'daily rows so far: {sum(1 for r in rows if r["source"]=="daily")}')
    except Exception as error:
        print('ECB FAILED', type(error).__name__, error)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, 'wt', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(f'wrote {len(rows)} rows -> {OUT} ({OUT.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
