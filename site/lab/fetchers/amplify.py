"""Amplify ETFs reader (DIVO IDVO QDVO).

Source: the issuer's own Firestore data feed behind https://amplifyetfs.com/<ticker>/ (public REST, read-only GET):
  holdings  .../funds/<T>/holdings  listed newest first (document id = holdings date); the newest document carrying a
            holdings array is used. Weight = field 'Weightings' (page column 'Weighting (%)'); type from 'holding_type'.
            total = lines in the feed (incl. cash / money-market / written options), as in the seed.
  rate      .../funds/<T>/yields  newest month-end document (id = as-of date): 'Distribution_Yield' (the page's
            Distribution Rate).
"""
import datetime as _dt
import re

RATES = True

BASE = 'https://firestore.googleapis.com/v1/projects/amplify-etfs-data-feed/databases/(default)/documents/funds'


def _un(v):
    for k in ('stringValue', 'doubleValue', 'integerValue', 'booleanValue', 'timestampValue'):
        if k in v:
            return v[k]
    return None


def _pct(x):
    if isinstance(x, (int, float)):
        return float(x)
    return float(str(x).strip().rstrip('%').replace(',', ''))


def _latest_ids(http, t, coll, n=5):
    r = http.get(f'{BASE}/{t}/{coll}', params={'pageSize': n, 'orderBy': '__name__ desc', 'mask.fieldPaths': 'asOfDate'}).json()
    ids = [d['name'].rsplit('/', 1)[1] for d in r.get('documents') or []]
    ids = [i for i in ids if re.fullmatch(r'\d{4}-\d{2}-\d{2}', i)]
    limit = (_dt.date.today() + _dt.timedelta(days=7)).isoformat()      # ignore anything implausibly far ahead
    return [i for i in ids if i <= limit]


def _type(ht, name):
    ht, u = (ht or '').lower(), (name or '').upper()
    if ht == 'option':
        return 'option'
    if ht in ('money_market', 'cash') or 'MONEY MARKET' in u:
        return 'cash'
    if ht in ('treasury', 'tbill', 't_bill') or 'TREASURY BILL' in u:
        return 'treasury'
    if ht == 'internal_fund' or re.search(r'\bETF\b', u):
        return 'etf'
    return 'stock'


def holdings(fund, http):
    t = fund['t'].upper()
    ids = _latest_ids(http, t, 'holdings')
    if not ids:
        raise RuntimeError('no dated holdings documents in the feed')
    for did in ids[:3]:
        url = f'{BASE}/{t}/holdings/{did}'
        f = http.get(url).json().get('fields') or {}
        vals = ((f.get('holdings') or {}).get('arrayValue') or {}).get('values') or []
        if not vals:
            continue
        asof = _un(f.get('asOfDate') or {}) or did
        data = []
        for x in vals:
            r = {k: _un(v) for k, v in ((x.get('mapValue') or {}).get('fields') or {}).items()}
            name, w = r.get('SecurityName'), r.get('Weightings')
            if not name or w in (None, ''):
                raise RuntimeError(f'holding row without name/weight: {r}')
            tk = (r.get('StockTicker') or '').strip() or None
            data.append(([name.strip(), tk, _pct(w), _type(r.get('holding_type'), name)], abs(float(r.get('MarketValue') or 0))))
        data.sort(key=lambda z: (-abs(z[0][2]), -z[1]))
        return {'as_of': str(asof)[:10], 'source_url': url, 'total': len(data), 'top': [z[0] for z in data[:10]]}
    raise RuntimeError(f'latest holdings documents {ids[:3]} carry no holdings array')


def rate(fund, http):
    t = fund['t'].upper()
    ids = _latest_ids(http, t, 'yields', n=3)
    for did in ids:
        url = f'{BASE}/{t}/yields/{did}'
        f = http.get(url).json().get('fields') or {}
        v = _un(f.get('Distribution_Yield') or {})
        if v in (None, '', '-', 'N/A'):
            continue
        if not re.fullmatch(r'-?\d+(\.\d+)?%?', str(v).strip()):
            raise RuntimeError(f'Distribution_Yield unreadable: {v!r}')
        return {'value_pct': _pct(v), 'as_of': did, 'source_url': url}
    return None
