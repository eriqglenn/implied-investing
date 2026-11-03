"""GraniteShares (YieldBOOST) issuer reader for the Income Lab refresh. Interface: see lab/fetchers/__init__.py.

Holdings
  The fund page https://graniteshares.com/institutional/us/en-us/etfs/<ticker>/ links the issuer's daily holdings
  file /media/<id>/<ticker>_holdings_file_YYYYMMDD.xls (legacy .xls, read with xlrd). Columns used, by header name:
  'ETF Ticker', 'Ticker/Cusip', 'Security Description', 'Asset Group', 'Percentage Weighting'.
  - weight_pct = 'Percentage Weighting' x 100 (the issuer publishes a fraction of net assets; unit conversion only,
    rounded to 6 dp). Signs are kept: written puts are negative.
  - name = 'Security Description' as the issuer labels it; ticker = 'Ticker/Cusip' (CUSIP for T-bills, the contract
    description for options, the fund ticker for fund shares); None for the cash row.
  - type from the issuer 'Asset Group' code: CU -> cash, B -> treasury, O -> option, S -> etf when the description
    names an ETF (YBST / YBTY hold other YieldBOOST ETFs), else stock; any other code -> other.
  - as_of = the date in the issuer's file name (YYYYMMDD), which matches the fund page's NAV date. The file's own
    'Position Date' column is not used for as_of.
  - total = number of rows in the file (cash row included).
  Raises when the link is missing, the 'ETF Ticker' column does not match the fund, or the columns are not found.

Rates
  The issuer's YieldBOOST distribution table https://graniteshares.com/underlyings/distribution/ lists every fund's
  'Distribution Rate (as of <date>)'. The page is fetched once per run and cached at module level; as_of = the date
  in that column header. A fund missing from the table returns None (reported for manual review).
"""
import datetime as dt
import html as _html
import re
from urllib.parse import urljoin

RATES = True

BASE = 'https://graniteshares.com/'
FUND_PAGE = 'https://graniteshares.com/institutional/us/en-us/etfs/{t}/'
DIST_URL = 'https://graniteshares.com/underlyings/distribution/'
GROUP_TYPES = {'CU': 'cash', 'B': 'treasury', 'O': 'option'}

_dist_cache = {}          # {'rows': {TICKER: (rate_pct, as_of)}} for this run


# ----------------------------------------------------------------------------------------------- holdings
def _holdings_link(fund, http):
    t = fund['t'].lower()
    pat = re.compile(r'/media/[A-Za-z0-9]+/' + re.escape(t) + r'_holdings_file_(\d{8})\.xls', re.I)
    last_err = None
    for url in (FUND_PAGE.format(t=t), fund.get('url')):
        if not url:
            continue
        try:
            page = http.get(url).text
        except Exception as e:
            last_err = e; continue
        found = {(m.group(1), m.group(0)) for m in pat.finditer(page)}
        if found:
            d, path = max(found)                                  # latest file date if several are linked
            return urljoin(BASE, path), d
        last_err = RuntimeError(f'no {t}_holdings_file_YYYYMMDD.xls link on {url}')
    raise RuntimeError(str(last_err))


def holdings(fund, http):
    import xlrd
    t = fund['t'].upper()
    url, ymd = _holdings_link(fund, http)
    as_of = dt.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])).isoformat()
    r = http.get(url)
    if r.content[:4] != b'\xd0\xcf\x11\xe0':
        raise RuntimeError(f'{url} is not an .xls workbook')
    sh = xlrd.open_workbook(file_contents=r.content).sheet_by_index(0)
    hdr = [str(x).strip() for x in sh.row_values(0)]
    need = ['ETF Ticker', 'Ticker/Cusip', 'Security Description', 'Asset Group', 'Percentage Weighting']
    miss = [c for c in need if c not in hdr]
    if miss:
        raise RuntimeError(f'holdings file columns missing {miss}: {hdr}')
    ix = {c: hdr.index(c) for c in need}
    rows = [sh.row_values(i) for i in range(1, sh.nrows)]
    rows = [x for x in rows if any(str(v).strip() for v in x)]
    if not rows:
        raise RuntimeError('holdings file has no rows')
    etf = {str(x[ix['ETF Ticker']]).strip().upper() for x in rows}
    if etf != {t}:
        raise RuntimeError(f'holdings file ETF Ticker column {sorted(etf)} does not match {t}')
    items = []
    for x in rows:
        w = x[ix['Percentage Weighting']]
        if not isinstance(w, (int, float)):
            raise RuntimeError(f'non-numeric weight {w!r}')
        g = str(x[ix['Asset Group']]).strip().upper()
        name = str(x[ix['Security Description']]).strip()
        if g == 'S':
            typ = 'etf' if re.search(r'\bETF\b', name, re.I) else 'stock'
        else:
            typ = GROUP_TYPES.get(g, 'other')
        tick = x[ix['Ticker/Cusip']]
        tick = None if typ == 'cash' or not str(tick).strip() else str(tick).strip()
        items.append([name, tick, round(float(w) * 100, 6), typ])
    items.sort(key=lambda y: -abs(y[2]))
    return {'as_of': as_of, 'source_url': url, 'total': len(rows), 'top': items[:10]}


# ----------------------------------------------------------------------------------------------- rates
def _cells(row):
    return [_html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c))).strip()
            for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S | re.I)]


def _dist_table(http):
    if 'rows' in _dist_cache:
        return _dist_cache['rows']
    s = http.get(DIST_URL).text
    s = re.sub(r'<script.*?</script>|<style.*?</style>', '', s, flags=re.S | re.I)
    out = {}
    for tb in re.findall(r'<table.*?</table>', s, re.S | re.I):
        rows = re.findall(r'<tr.*?</tr>', tb, re.S | re.I)
        if not rows:
            continue
        h = _cells(rows[0])
        ti = next((i for i, c in enumerate(h) if c.lower() == 'ticker'), None)
        ri = next((i for i, c in enumerate(h) if c.lower().startswith('distribution rate')), None)
        if ti is None or ri is None:
            continue
        m = re.search(r'as of\s+([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4})', h[ri])
        if not m:
            raise RuntimeError(f'no as-of date in distribution-rate header {h[ri]!r}')
        mon = m.group(1)[:3].title()
        as_of = dt.datetime.strptime(f'{mon} {m.group(2)} {m.group(3)}', '%b %d %Y').date().isoformat()
        for r in rows[1:]:
            c = _cells(r)
            if len(c) <= max(ti, ri) or not c[ti]:
                continue
            v = re.fullmatch(r'(-?\d+(?:\.\d+)?)\s*%', c[ri].replace(',', ''))
            if not v:
                continue                                          # blank / non-numeric: left for manual review
            out[c[ti].upper()] = (float(v.group(1)), as_of)
    if not out:
        raise RuntimeError(f'no distribution-rate table found on {DIST_URL}')
    _dist_cache['rows'] = out
    return out


def rate(fund, http):
    row = _dist_table(http).get(fund['t'].upper())
    if row is None:
        return None
    return {'value_pct': row[0], 'as_of': row[1], 'source_url': DIST_URL}
