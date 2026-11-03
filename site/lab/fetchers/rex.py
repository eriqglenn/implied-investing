"""REX Shares reader (ULTI FEPI AIPI CEPI NVII TSII WMTI).

Source: the fund page https://www.rexshares.com/<ticker>/ (server-rendered HTML).
  holdings  <div id="top-ten-holdings">: 'As of MM/DD/YYYY' + rows Symbol / Name / Security Identifier / Weighting / Net Value / Shares Held.
            The page shows the top 10 only, so total = None -- except when the table lists fewer than 10 lines (the
            T-bill + option funds NVII/TSII/WMTI), in which case the table is the complete position list and total = lines listed.
  rate      <div id="distribution-dt">: 'Distribution Rate* As of MM/DD/YYYY' -> 'NN.NN%' (latest distribution x 52 / NAV, as published).
"""
import html as _html
import re

RATES = True

_ROW = re.compile(r'<div class="t-row">(.*?)</div></div>', re.S)
_CELL = re.compile(r'<div class="t-col t-(?:header|data|label)">(.*?)(?=</div>|$)', re.S)


def _url(fund):
    return fund.get('url') or f"https://www.rexshares.com/{fund['t'].lower()}/"


def _page(fund, http):
    url = _url(fund)
    s = http.get(url).text
    if fund['t'].upper() not in s.upper():
        raise RuntimeError(f'{url}: page does not mention {fund["t"]}')
    return url, s


def _text(c):
    return _html.unescape(re.sub(r'<[^>]+>', ' ', c)).replace(' ', ' ').strip()   # labels kept as published (no space collapsing)


def _date(mdy):
    m, d, y = mdy.split('/')
    return f'{int(y):04d}-{int(m):02d}-{int(d):02d}'


def _block(s, div_id):
    i = s.find(f'id="{div_id}"')
    if i < 0:
        raise RuntimeError(f'no element id={div_id!r} on the page')
    j = s.find('<div class="button-row">', i)
    k = s.find('<div class="clearboth">', i)
    end = min(x for x in (j, k, i + 30000) if x > i)
    return s[i:end]


def _num(x):
    return float(x.replace('$', '').replace(',', '').replace('%', '').strip())


def _type(sym, name):
    u = name.upper()
    if 'TREASURY' in u or re.match(r'^B \d', sym or ''):
        return 'treasury'
    if sym in ('Cash&Other', 'FGXXX') or 'CASH' in u or 'GOV OBLIG' in u or 'MONEY MARKET' in u:
        return 'cash'
    if re.search(r'\d{1,2}/\d{1,2}/\d{2,4}.*\d+(\.\d+)? ?[CP]$|[CP]\d+(\.\d+)?$', name.strip()) or re.search(r'\d{6}[CP]\d{8}$', sym or ''):
        return 'option'
    if re.search(r'\bETF\b', u):
        return 'etf'
    return 'stock'


def holdings(fund, http):
    url, s = _page(fund, http)
    seg = _block(s, 'top-ten-holdings')
    m = re.search(r'As of (\d{1,2}/\d{1,2}/\d{4})', seg)
    if not m:
        raise RuntimeError('holdings as-of date not found')
    rows = [[_text(c) for c in _CELL.findall(r)] for r in _ROW.findall(seg)]
    if not rows or rows[0][:4] != ['Symbol', 'Name', 'Security Identifier', 'Weighting']:
        raise RuntimeError(f'unexpected holdings header {rows[0] if rows else None}')
    data = []
    for r in rows[1:]:
        if len(r) != 6 or not r[3].endswith('%'):
            raise RuntimeError(f'malformed holdings row {r}')
        sym, name = r[0], r[1]
        nv = abs(_num(r[4])) if r[4] else 0.0
        data.append(([name, sym or None, _num(r[3]), _type(sym, name)], nv))
    if not data:
        raise RuntimeError('holdings table is empty')
    data.sort(key=lambda x: (-abs(x[0][2]), -x[1]))
    return {'as_of': _date(m.group(1)), 'source_url': url, 'total': len(data) if len(data) < 10 else None,
            'top': [x[0] for x in data[:10]]}


def rate(fund, http):
    url, s = _page(fund, http)
    seg = _block(s, 'distribution-dt')
    m = re.search(r'<div class="t-col t-label">\s*Distribution Rate(?:<sup>|<br>|\*|\s)(.*?)<div class="t-col t-data">([^<]*)</div>', seg, re.S)
    if not m:
        raise RuntimeError('Distribution Rate row not found in distribution-dt block')
    d = re.search(r'As of (\d{1,2}/\d{1,2}/\d{4})', _text(m.group(1)))
    v = _text(m.group(2))
    if not d or not re.fullmatch(r'-?[\d,]+(\.\d+)?%', v):
        raise RuntimeError(f'distribution rate cell unreadable: {_text(m.group(1))!r} {v!r}')
    return {'value_pct': _num(v), 'as_of': _date(d.group(1)), 'source_url': url}
