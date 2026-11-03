"""YieldMax ETFs (yieldmaxetfs.com) reader, plus the shared parser for Tidal Financial Group holdings CSVs.

Holdings: the fund page links a daily Tidal-format CSV
  https://yieldmaxetfs.com/wp-content/uploads/funds/<TICKER>/TidalFG_Holdings_<TICKER>.csv
  columns: Date, Account, StockTicker, CUSIP, SecurityName, Shares, Price, MarketValue, Weightings, NetAssets,
           SharesOutstanding, CreationUnits      (Weightings = % of net assets, e.g. "17.09%"; Date = as-of, M/D/YYYY)
The same file format is used by XFUNDS (nicholasx.com) and Quantify (quantifyfunds.com); their readers import
tidal_holdings() from here.

Rates: on 2026-10-13 every yieldmaxetfs.com fund page (and the Our ETFs table) shows "-" for Distribution Rate, and the
WordPress REST record for each fund carries no rate field. Rates are published only in weekly press releases, which are
not a reliable machine-readable issuer feed, so RATES = False (rates stay a manual update).
"""
import csv, datetime as dt, io, re

RATES = False
URL = 'https://yieldmaxetfs.com/wp-content/uploads/funds/{t}/TidalFG_Holdings_{t}.csv'

NEED = ('Date', 'Account', 'StockTicker', 'SecurityName', 'Weightings')
MAX_AGE_DAYS = 14                     # an older file means the issuer stopped updating it: treat as a failure

_OCC = re.compile(r'^\d?[A-Z][A-Z.]{0,5}\d{6}[CP]\d{8}$')                       # OCC symbol (spaces removed), e.g. 2MSTR261120P00135010
_OPT_NAME = re.compile(r'\b\d{1,2}/\d{1,2}/\d{2,4}\s+[\d.]+\s*[CP]\b'               # "MSTR 12/18/2026 150.01 C"
                       r'|\b\d{1,2}/\d{1,2}/\d{2}\s+[CP][\d.]+\b')                    # "MSTR US 10/16/26 C157.5", "... P135.01 FLX"
_TSY = re.compile(r'\b(United States|U\.?S\.?) Treasury\b|\bTreasury (Bill|Note|Bond|Strip)', re.I)
_CASH = re.compile(r'Government Obligations Fund|Money Market|Treasury Obligations Fund|Govt Oblig', re.I)
_ETF = re.compile(r'\bETF\b|\bETP\b|\bETN\b|iShares|SPDR|Grayscale|ProShares|Invesco QQQ|\bFund\b', re.I)
_TRUST_ETP = re.compile(r'\bTrust\b.*\b(Bitcoin|Ether|Ethereum|Gold|Silver|Solana|XRP|Crypto|Platinum|Palladium)\b'
                        r'|\b(Bitcoin|Ether|Ethereum|Gold|Silver|Solana|XRP|Crypto|Platinum|Palladium)\b.*\bTrust\b', re.I)


def classify(name, ticker):
    n = name or ''; t = (ticker or '').replace(' ', '').upper()
    if 'TRS' in t or 'SWAP' in n.upper():
        return 'swap'
    if _OCC.match(t) or _OPT_NAME.search(n):
        return 'option'
    if t in ('CASH&OTHER', 'CASH', 'FGXXX') or n.lower().startswith('cash') or _CASH.search(n):
        return 'cash'
    if _TSY.search(n):
        return 'treasury'
    if _ETF.search(n) or _TRUST_ETP.search(n):
        return 'etf'
    return 'stock'


def _weight(s):
    s = (s or '').strip().replace(',', '')
    if not s.endswith('%'):
        raise ValueError(f'weight {s!r} is not a percentage')
    return float(s[:-1])


def _iso(s):
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', (s or '').strip())
    if not m:
        raise ValueError(f'unexpected date {s!r}')
    return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))


def tidal_holdings(ticker, url, http):
    """Parse a Tidal Financial Group daily holdings CSV -> the dict holdings() must return."""
    r = http.get(url)
    text = r.content.decode('utf-8-sig', errors='strict')
    rd = csv.DictReader(io.StringIO(text))
    cols = [c.strip() for c in (rd.fieldnames or [])]
    if any(c not in cols for c in NEED):
        raise RuntimeError(f'not a Tidal holdings CSV (columns: {cols[:12]})')
    rows = []
    for raw in rd:
        x = {(k or '').strip(): (v or '').strip() for k, v in raw.items() if k}
        if x.get('SecurityName'):
            rows.append(x)
    if not rows:
        raise RuntimeError('holdings file has no rows')
    accts = {x['Account'].upper() for x in rows}
    if accts != {ticker.upper()}:
        raise RuntimeError(f'file is for {sorted(accts)}, expected {ticker}')
    dates = {_iso(x['Date']) for x in rows}
    if len(dates) != 1:
        raise RuntimeError(f'file mixes dates {sorted(str(d) for d in dates)}')
    as_of = dates.pop()
    age = (dt.date.today() - as_of).days
    if age > MAX_AGE_DAYS:
        raise RuntimeError(f'holdings file is stale (as of {as_of}, {age} days old)')
    items = []
    for x in rows:
        name = re.sub(r'\s+', ' ', x['SecurityName'])
        tick = x['StockTicker'] or None
        typ = classify(name, tick)
        if tick and tick.replace(' ', '').upper() in ('CASH&OTHER', 'CASH'):
            tick = None
        items.append([name, tick, _weight(x['Weightings']), typ])
    tot = sum(i[2] for i in items)
    if abs(tot - 100) > 5:                                  # weights are % of net assets: they must net to ~100
        raise RuntimeError(f'weights sum to {tot:.2f}%, not ~100%')
    items.sort(key=lambda i: -abs(i[2]))
    return dict(as_of=as_of.isoformat(), source_url=url, total=len(items), top=items[:10])


def holdings(fund, http):
    t = fund['t'].upper()
    return tidal_holdings(t, URL.format(t=t), http)
