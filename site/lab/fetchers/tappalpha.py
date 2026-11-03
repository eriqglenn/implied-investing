"""TappAlpha reader (tappalphafunds.com). Also used by tuttle.py (Tuttle Capital Income Blast, incomeblastetfs.com).

Both issuers' fund pages load their data from the same public fund API (one JSON per ticker):
  https://jdkfnvgkfwotjlyovbrk.supabase.co/functions/v1/fund-public-api?ticker=<T>&view=all
It carries
  latest.holdings_count, holdings[] (security_name, security_ticker, weight = % of net assets, as_of_date)
  latest.distribution_rate (the distribution rate the fund page shows, in %) and latest.distribution_rate_as_of_date
  latest_distribution (declaration / ex / record / pay dates and amount of the most recent distribution)
"""
import re

RATES = True

FEED = 'https://jdkfnvgkfwotjlyovbrk.supabase.co/functions/v1/fund-public-api?ticker={t}&view=all'

_cache = {}          # ticker -> parsed feed, so holdings() and rate() in one run share one request


def feed_url(fund):
    return FEED.format(t=fund['t'].upper())


def _feed(fund, http):
    t = fund['t'].upper()
    if t not in _cache:
        d = http.get(feed_url(fund), headers={'Accept': 'application/json'}).json()
        if not isinstance(d, dict) or not isinstance(d.get('latest'), dict):
            raise RuntimeError('fund-public-api: unexpected payload (no "latest" block)')
        got = (d['latest'].get('ticker') or (d.get('fund') or {}).get('ticker') or '').upper()
        if got != t:
            raise RuntimeError(f'fund-public-api returned ticker {got!r} for {t}')
        _cache[t] = d
    return _cache[t]


# ----------------------------------------------------------------------------------------------- classification
_OCC = re.compile(r'^[A-Z0-9.]{1,6}\s*\d{6}[CP]\d{8}$')                       # SPXW  261012C07825000
_OPT_NAME = re.compile(r'\b\d{2}/\d{2}/\d{2,4}\s+[CP]\s*\d[\d.]*$')            # SPXW US 10/12/26 C7825
_MMF = re.compile(r'Government Obligations Fund|Treasury Obligations Fund|Money Market|Treasury Portfolio|Government Portfolio', re.I)
_TBILL = re.compile(r'Treasury Bill|\bT-?Bill\b|\bB 0 \d', re.I)
_ETF = re.compile(r'\bETF\b|\bETN\b|\bSPDR\b|iShares|Invesco QQQ', re.I)


def _is_cash_other(name, tick):
    return tick.replace(' ', '').lower() in ('cash&other', 'cash', 'usd') or name.lower().startswith('cash')


def classify(name, tick):
    n, t = name or '', (tick or '').strip()
    if 'TRS' in t.upper() or re.search(r'\bSWAP\b', n, re.I):
        return 'swap'
    if _OCC.match(t) or _OPT_NAME.search(n):
        return 'option'
    if _is_cash_other(n, t) or _MMF.search(n):
        return 'cash'
    if _TBILL.search(n):
        return 'treasury'
    if _ETF.search(n):
        return 'etf'
    return 'stock'


# ----------------------------------------------------------------------------------------------- reader interface
def holdings(fund, http):
    d = _feed(fund, http)
    rows = d.get('holdings') or []
    if not rows:
        raise RuntimeError('fund-public-api: empty holdings array')
    dates = sorted({r.get('as_of_date') for r in rows if r.get('as_of_date')})
    if not dates:
        raise RuntimeError('fund-public-api: holdings carry no as_of_date')
    as_of = dates[-1]
    rows = [r for r in rows if r.get('as_of_date') == as_of]       # only the latest holdings date, never a mix
    top = []
    for r in rows:
        name = (r.get('security_name') or '').strip()
        tick = (r.get('security_ticker') or '').strip()
        w = r.get('weight')
        if not name or not isinstance(w, (int, float)):
            raise RuntimeError(f'fund-public-api: holding row without name/weight: {name!r} {w!r}')
        top.append([name, None if (not tick or _is_cash_other(name, tick)) else tick, w, classify(name, tick)])
    top.sort(key=lambda x: -abs(x[2]))
    total = d['latest'].get('holdings_count')
    return dict(as_of=as_of[:10], source_url=feed_url(fund), total=int(total) if isinstance(total, (int, float)) else len(rows),
                top=top[:10])


def rate(fund, http):
    d = _feed(fund, http)
    L = d['latest']
    v = L.get('distribution_rate')
    if v is None:
        return None                                                   # not published for this fund -> manual review
    # As-of date: the feed's own distribution_rate_as_of_date (the date the fund page prints next to the rate).
    # For these issuers it is the ex-date of the latest distribution the rate was computed from; we only fall back to
    # latest_distribution.ex_date when the feed omits its own date.
    as_of = L.get('distribution_rate_as_of_date') or (d.get('latest_distribution') or {}).get('ex_date')
    if not as_of:
        raise RuntimeError('fund-public-api: distribution_rate has no as-of date')
    return dict(value_pct=float(v), as_of=str(as_of)[:10], source_url=feed_url(fund))
