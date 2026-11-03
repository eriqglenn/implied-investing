"""Kurv (kurvinvest.com) reader for the Income Lab refresh.

holdings: https://web.services.kurvinvest.com/etfdata/<T>/holdings.csv (the fund page's 'Download Holdings' link);
          weights = issuer '% of fund' column. The CSV carries no date, so as_of = lastTradingDate from
          https://web.services.kurvinvest.com/etfdata/<T>/latest_price.json, the feed the fund page itself uses for its
          price / holdings widgets.
rate:     the distribution rate is not in any web.services JSON (latest_price.json has NAV/returns/holdings only, other
          etfdata paths are not public); it is server-rendered into the fund page https://www.kurvinvest.com/etf/<t>
          (Webflow CMS) under 'Distributions as of M/D/YYYY ... Distribution Rate X%'. Cross-checked against the hero
          'Distribution Rate X%' on the same page.
"""
import csv, datetime as dt, html as htmllib, io, re

RATES = True

DATA = 'https://web.services.kurvinvest.com/etfdata/{t}/'
PAGE = 'https://www.kurvinvest.com/etf/{t}'

ETF_RE = re.compile(r'\b(ETF|Trust ETF|Fund)\b', re.I)
OPT_RE = re.compile(r'(\d{1,2}/\d{1,2}/\d{2,4}\s+[\d.]+\s+[CP]\b)|(\bUS \d{2}/\d{2}/\d{2} [CP][\d.]+)|FUTR OPTN', re.I)


def _classify(tk, name):
    n = name or ''
    if (tk and tk.strip().lower().startswith('cash')) or n.strip().lower().startswith('cash'):
        return 'cash'
    if re.search(r'MMKT|money market', n, re.I):
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


def holdings(fund, http):
    t = fund['t'].upper()
    url = DATA.format(t=t) + 'holdings.csv'
    txt = http.get(url).content.decode('utf-8-sig', errors='replace')
    rdr = csv.DictReader(io.StringIO(txt))
    need = {'Ticker', 'Description', '% of fund'}
    if not rdr.fieldnames or not need.issubset({(c or '').strip() for c in rdr.fieldnames}):
        raise RuntimeError(f'unexpected CSV header for {t}: {rdr.fieldnames!r} / {txt[:120]!r}')
    rows = [{(k or '').strip(): v for k, v in row.items()} for row in rdr]
    rows = [x for x in rows if (x.get('Ticker') or '').strip() or (x.get('Description') or '').strip()]
    if not rows:
        raise RuntimeError(f'empty holdings CSV for {t}')
    j = http.get(DATA.format(t=t) + 'latest_price.json').json()
    if (j.get('Ticker') or '').upper() != t:
        raise RuntimeError(f"latest_price.json ticker {j.get('Ticker')!r} != {t}")
    ltd = j.get('lastTradingDate') or ''
    if not re.match(r'\d{4}-\d{2}-\d{2}', ltd):
        raise RuntimeError(f'no lastTradingDate for {t}: {ltd!r}')
    as_of = dt.date.fromisoformat(ltd[:10]).isoformat()
    items = []
    for x in rows:
        w = _pct(x.get('% of fund'))
        if w is None:
            continue
        name = (x.get('Description') or '').strip()
        typ = _classify(x.get('Ticker'), name)
        items.append([name, _sym(x.get('Ticker'), typ), w, typ])
    if not items:
        raise RuntimeError(f'no weighted rows in holdings CSV for {t}')
    items.sort(key=lambda i: -abs(i[2]))                      # stable: ties keep CSV order
    return dict(as_of=as_of, source_url=url, total=len(rows), top=items[:10])


def _text(page):
    s = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', page)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = htmllib.unescape(s).replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', s)


def rate(fund, http):
    t = fund['t'].upper()
    url = PAGE.format(t=t.lower())
    txt = _text(http.get(url).text)
    if not re.search(r'\b' + re.escape(t) + r'\b', txt):
        raise RuntimeError(f'{t} not found on {url}')
    m = re.search(r'Distributions as of (\d{1,2}/\d{1,2}/\d{4})(.{0,200}?)Distribution Rate\s+(--|-?[\d.]+\s*%)', txt)
    if not m:
        raise RuntimeError(f'distribution-rate block not found on {url}')
    asof = dt.datetime.strptime(m.group(1), '%m/%d/%Y').date().isoformat()
    val = m.group(3).replace(' ', '')
    hero = re.search(r'Distribution Rate\s+(--|-?[\d.]+\s*%)\s+30-Day SEC Yield', txt)
    if hero and hero.group(1).replace(' ', '') != val:
        raise RuntimeError(f'page disagrees with itself: distributions table {val}, headline {hero.group(1)}')
    if val == '--':
        return None                                            # not published yet
    return dict(value_pct=float(val.rstrip('%')), as_of=asof, source_url=url)
