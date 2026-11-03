"""Quantify Funds (quantifyfunds.com) reader.

Holdings: daily Tidal-format CSV https://quantifyfunds.com/wp-content/uploads/data/TidalFG_Holdings_<TICKER>.csv
          (parsed by yieldmax.tidal_holdings).
Rates:    the fund page (https://quantifyfunds.com/optionsbasedincome/<ticker>/) prints a "Distribution Rate" figure in its
          header block (static page HTML; the site's live-data fund-info feed has no rate row). The issuer gives no as-of
          date for it, so as_of is None. A page showing "-" returns None -> manual review.
"""
import re

from .xfunds import _text
from .yieldmax import tidal_holdings

RATES = True
URL = 'https://quantifyfunds.com/wp-content/uploads/data/TidalFG_Holdings_{t}.csv'


def holdings(fund, http):
    t = fund['t'].upper()
    return tidal_holdings(t, URL.format(t=t), http)


def rate(fund, http):
    url = fund['url']
    page = http.get(url).text
    title = re.search(r'<title>(.*?)</title>', page, flags=re.S | re.I)
    if not title or not re.search(rf'\b{re.escape(fund["t"])}\b', title.group(1), flags=re.I):
        raise RuntimeError(f'page title does not name {fund["t"]}: {title.group(1)[:80] if title else None!r}')
    body = re.sub(r'<span class="tt-content">.*?</span>', ' ', page, flags=re.S | re.I)      # drop tooltip texts
    body = re.sub(r'<head\b.*?</head>', ' ', body, flags=re.S | re.I)
    txt = _text(body)
    hits = re.findall(r'Distribution Rate\*{0,2}\s*(?:i\s+)?(\d+(?:\.\d+)?\s*%|[-–—]+\s*%?)', txt)
    vals = {h.replace(' ', '') for h in hits}
    if not vals:
        raise RuntimeError('Distribution Rate figure not found on the fund page')
    if len(vals) != 1:
        raise RuntimeError(f'conflicting Distribution Rate figures on the fund page: {sorted(vals)}')
    v = vals.pop()
    if not v[0].isdigit():
        return None
    return dict(value_pct=float(v.rstrip('%')), as_of=None, source_url=url)
