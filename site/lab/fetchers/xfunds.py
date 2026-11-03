"""XFUNDS / Nicholas Wealth (nicholasx.com) reader.

Holdings: daily Tidal-format CSV https://nicholasx.com/wp-content/uploads/data/TidalFG_Holdings_<TICKER>.csv
          (parsed by yieldmax.tidal_holdings).
Rates:    each fund page fills its "Fund Information" box from the site's live-data endpoint
          https://nicholasx.com/wp-json/twm/v1/data?type=fund-info-table&post_id=<id>  (post id taken from the page's
          data-twm-type="fund-info-table" element). The box has a "Distribution Rate**" row. The issuer prints no as-of date
          for that figure (the 30-Day SEC Yield next to it is dated, the rate is not), so as_of is None. A fund whose box
          shows "-%" (no rate published) returns None -> manual review.
"""
import html as _html, re

from .yieldmax import tidal_holdings

RATES = True
URL = 'https://nicholasx.com/wp-content/uploads/data/TidalFG_Holdings_{t}.csv'
API = 'https://nicholasx.com/wp-json/twm/v1/data'


def holdings(fund, http):
    t = fund['t'].upper()
    return tidal_holdings(t, URL.format(t=t), http)


def _text(fragment):
    s = re.sub(r'<(script|style)\b.*?</\1>', ' ', fragment, flags=re.S | re.I)
    return re.sub(r'\s+', ' ', _html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


def twm_info_rows(page_url, http, api_default):
    """Fund-info rows {label: value} from a Tidal 'twm' WordPress site, via the page's fund-info-table post id."""
    page = http.get(page_url).text
    ids = set(re.findall(r'data-twm-type="fund-info-table"[^>]*?data-post-id="(\d+)"', page))
    if len(ids) != 1:
        raise RuntimeError(f'fund-info-table post id not found on {page_url} ({sorted(ids)})')
    m = re.search(r'"restUrl"\s*:\s*"([^"]+)"', page)
    api = m.group(1).replace('\\/', '/') if m else api_default
    js = http.get(api, params={'type': 'fund-info-table', 'post_id': ids.pop()}).json()
    frag = js.get('html') if isinstance(js, dict) else None
    if not frag:
        raise RuntimeError('fund-info-table feed returned no html')
    rows = {}
    for tr in re.findall(r'<tr\b.*?</tr>', frag, flags=re.S | re.I):
        cells = [_text(c) for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', tr, flags=re.S | re.I)]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            rows[cells[0]] = cells[-1]
    return rows, api


def rate(fund, http):
    rows, api = twm_info_rows(fund['url'], http, API)
    tick = next((v for k, v in rows.items() if k.strip().lower() == 'ticker'), None)
    if (tick or '').upper() != fund['t'].upper():
        raise RuntimeError(f'fund-info box is for {tick!r}, expected {fund["t"]}')
    vals = [v for k, v in rows.items() if re.match(r'distribution rate\b', k.strip(' *'), re.I)]
    if len(vals) != 1:
        raise RuntimeError(f'no single Distribution Rate row in fund-info box ({list(rows)})')
    v = vals[0].replace(' ', '')
    if re.fullmatch(r'[-–—]*%?|N/?A%?', v, flags=re.I):
        return None                                         # issuer shows "-%": nothing published
    m = re.fullmatch(r'(\d+(?:\.\d+)?)%', v)
    if not m:
        raise RuntimeError(f'unreadable Distribution Rate {vals[0]!r}')
    return dict(value_pct=float(m.group(1)), as_of=None, source_url=fund['url'])
