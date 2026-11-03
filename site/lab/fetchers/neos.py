"""NEOS Investments (neosfunds.com) reader for the Income Lab refresh.

holdings: full-holdings CSV behind the fund page's 'Download Full Holdings' button
          (wp-admin/admin-ajax.php?action=download_holdings_csv). The endpoint answers 403 unless the request carries
          the fund page as Referer and X-Requested-With: XMLHttpRequest. Weightings column = % of net assets;
          Date column = as-of.
rate:     fund page https://neosfunds.com/<t>/ -> 'Distribution Information (as of MM/DD/YYYY)' table,
          'Distribution Rate' row (most recent distribution x 12 / ex-date NAV, as published). Cross-checked against
          the headline 'Distribution Rate ... As of MM/DD/YYYY' block on the same page.
"""
import csv, datetime as dt, html as htmllib, io, re

RATES = True

BASE = 'https://neosfunds.com'
CSV_URL = BASE + '/wp-admin/admin-ajax.php?action=download_holdings_csv&ticker={t}'

ETF_RE = re.compile(r'\b(ETF|Trust ETF|Fund)\b', re.I)
OPT_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{2,4}\s+[\d.]+\s+[CP]\b)|(\bUS \d{2}/\d{2}/\d{2} [CP][\d.]+)|FUTR OPTN', re.I)


def _classify(tk, name, mmflag=''):
    n = name or ''
    if (tk and tk.strip().lower().startswith('cash')) or n.strip().lower().startswith('cash'):
        return 'cash'
    if (mmflag or '').strip().upper() == 'Y' or re.search(r'MMKT|money market', n, re.I):
        return 'cash'
    if OPT_RE.search(n):
        return 'option'
    if re.search(r'swap', n, re.I):
        return 'swap'
    if re.search(r'United States Treasury|Treasury Bill|Treasury Note|T-Bill', n, re.I):
        return 'treasury'
    if ETF_RE.search(n):
        return 'etf'
    if re.search(r'\d+(\.\d+)?% \d{2}/\d{2}/\d{4}', n):          # corporate / other bond
        return 'other'
    return 'stock'


def _sym(tk, typ):
    if typ in ('cash', 'treasury'):
        return None
    tk = re.sub(r'\s+', ' ', (tk or '').strip())
    if not tk:
        return None
    if typ == 'other' and re.fullmatch(r'[0-9A-Z]{9}', tk) and re.search(r'\d', tk):   # bare CUSIP, not a symbol
        return None
    return tk


def _pct(s):
    s = (s or '').strip().replace('%', '').replace(',', '')
    if s in ('', '--', '-'):
        return None
    return float(s)


def _page_url(fund):
    return f"{BASE}/{fund['t'].lower()}/"


def _date(s):
    return dt.datetime.strptime(s.strip(), '%m/%d/%Y').date().isoformat()


def holdings(fund, http):
    t = fund['t'].upper()
    url = CSV_URL.format(t=t)
    r = http.get(url, headers={'Referer': _page_url(fund), 'X-Requested-With': 'XMLHttpRequest',
                               'Accept': 'text/csv,*/*;q=0.8'})
    txt = r.content.decode('utf-8-sig', errors='replace')
    rdr = csv.DictReader(io.StringIO(txt))
    need = {'Date', 'Account', 'StockTicker', 'SecurityName', 'Weightings'}
    if not rdr.fieldnames or not need.issubset({(c or '').strip() for c in rdr.fieldnames}):
        raise RuntimeError(f'unexpected CSV header for {t}: {rdr.fieldnames!r} / {txt[:120]!r}')
    rows = [{(k or '').strip(): v for k, v in row.items()} for row in rdr]
    rows = [x for x in rows if (x.get('Account') or '').strip()]
    if not rows:
        raise RuntimeError(f'empty holdings CSV for {t}')
    accts = {x['Account'].strip().upper() for x in rows}
    if accts != {t}:
        raise RuntimeError(f'CSV for {t} lists account(s) {sorted(accts)}')
    dates = {_date(x['Date']) for x in rows if (x.get('Date') or '').strip()}
    if not dates:
        raise RuntimeError(f'no Date in holdings CSV for {t}')
    items = []
    for x in rows:
        w = _pct(x.get('Weightings'))
        if w is None:
            continue
        name = (x.get('SecurityName') or '').strip()
        typ = _classify(x.get('StockTicker'), name, x.get('MoneyMarketFlag', ''))
        items.append([name, _sym(x.get('StockTicker'), typ), w, typ])
    if not items:
        raise RuntimeError(f'no weighted rows in holdings CSV for {t}')
    items.sort(key=lambda i: -abs(i[2]))                      # stable: ties keep CSV order
    return dict(as_of=max(dates), source_url=url, total=len(rows), top=items[:10])


def _text(page):
    s = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', page)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = htmllib.unescape(s).replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', s)


def rate(fund, http):
    url = _page_url(fund)
    txt = _text(http.get(url).text)
    t = fund['t'].upper()
    if not re.search(r'\b' + re.escape(t) + r'\b', txt):
        raise RuntimeError(f'{t} not found on {url}')
    m = re.search(r'Distribution Information \(as of (\d{1,2}/\d{1,2}/\d{4})\)(.{0,400}?)(?<!Trailing )Distribution Rate\s+(--|-?[\d.]+\s*%)', txt)
    if not m:
        raise RuntimeError(f'distribution-rate table not found on {url}')
    asof, val = _date(m.group(1)), m.group(3).replace(' ', '')
    # headline block: "Distribution Rate 12.21% 30-Day SEC Yield 0.45% As of 09/30/2026"
    h = re.search(r'Distribution Rate\s+(--|-?[\d.]+\s*%)\s+30-Day SEC Yield\s+\S+\s+As of (\d{1,2}/\d{1,2}/\d{4})', txt)
    if h and (h.group(1).replace(' ', '') != val or _date(h.group(2)) != asof):
        raise RuntimeError(f'page disagrees with itself: table {val} as of {asof}, headline {h.group(1)} as of {h.group(2)}')
    if val == '--':
        return None                                            # not published yet (new fund)
    return dict(value_pct=float(val.rstrip('%')), as_of=asof, source_url=url)
