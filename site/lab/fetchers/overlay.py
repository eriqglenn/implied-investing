"""Overlay Shares (Liquid Strategies, lsfunds.com) reader.

Holdings: the fund page (https://lsfunds.com/etfs/<ticker>) prints a "Fund Holdings Data as of <Month D, YYYY>" table
          (Security Name, CUSIP, Ticker, Shares, Weight) in its static HTML, including the index option legs
          ("SPXW US 10/29/26 P7600 ...", signed shares and weights).
Rates:    the same page prints either "<NN>% Target Annual Distribution Rate" (new funds such as OVQ; a target, no as-of
          date) or "<NN>% Distribution Rate**" = the annualized most recent distribution / NAV "as of M/D/YYYY".
"""
import datetime as dt
import re

from .xfunds import _text

RATES = True
OPT = re.compile(r'^(SPXW|SPX|XSP|NDXP|NDX|RUTW|RUT|QQQ|SPY|IWM) US (\d\d/\d\d/\d\d) ([PC])(\d+(?:\.\d+)?)\b')


def _page(fund, http):
    url = fund['url']
    txt = _text(http.get(url).text)
    if not re.search(rf'Ticker Symbol {re.escape(fund["t"])}\b', txt):
        raise RuntimeError(f'fund page does not name {fund["t"]}')
    return url, txt


def _row(pre, shares, w):
    m = OPT.match(pre)
    if m:
        root, exp, pc, k = m.groups()
        side = 'long' if not shares.startswith('-') else 'short'
        return [f'{root} {exp} {k} {"put" if pc == "P" else "call"} ({side})', None, float(w), 'option']
    tok = pre.split()
    if len(tok) < 3:
        raise RuntimeError(f'unparsed holdings row: {pre!r}')
    name, tick = ' '.join(tok[:-2]), tok[-1]
    if tick.lower().startswith('cash') or re.search(r'Government Obligations|Money Market|Treasury Obligations', name, re.I):
        return [name, None if tick.lower().startswith('cash') else tick, float(w), 'cash']
    return [name, tick, float(w), 'etf' if re.search(r'\bETF\b', name) else 'stock']


def holdings(fund, http):
    url, txt = _page(fund, http)
    m = re.search(r'Fund Holdings Data as of ([A-Z][a-z]+ \d{1,2}, \d{4}) Security Name CUSIP Ticker Shares Weight (.*?)\*Holdings are subject', txt)
    if not m:
        raise RuntimeError('holdings table not found on the fund page')
    as_of = dt.datetime.strptime(m.group(1), '%B %d, %Y').date().isoformat()
    rows = re.findall(r'(.+?) (-?[\d,]+(?:\.\d+)?) (-?\d+(?:\.\d+)?)% ?', m.group(2))
    if not rows:
        raise RuntimeError('holdings rows not parsed')
    top = [_row(pre.strip(), sh, w) for pre, sh, w in rows]
    top.sort(key=lambda x: -abs(x[2]))
    return dict(as_of=as_of, source_url=url, total=len(top), top=top[:10])


def rate(fund, http):
    url, txt = _page(fund, http)
    tgt = set(re.findall(r'(\d+(?:\.\d+)?)% Target Annual Distribution Rate', txt))
    if tgt:
        if len(tgt) != 1:
            raise RuntimeError(f'conflicting target rates on the fund page: {sorted(tgt)}')
        return dict(value_pct=float(tgt.pop()), as_of=None, source_url=url)
    # header block "10.53% Distribution Rate** 0.32% 30-Day SEC Yield" and details block "Distribution Rate** 10.53%"
    vals = set(re.findall(r'(\d+(?:\.\d+)?)% Distribution Rate\*+ \d+(?:\.\d+)?% 30-Day SEC Yield', txt)) | \
           set(re.findall(r'Distribution Rate\*+ (\d+(?:\.\d+)?)% Distribution Frequency', txt))
    if not vals:
        return None
    if len(vals) != 1:
        raise RuntimeError(f'conflicting Distribution Rate figures on the fund page: {sorted(vals)}')
    d = re.search(r'Distribution Rate represents .*? NAV as of (\d{1,2}/\d{1,2}/\d{4})', txt)
    as_of = dt.datetime.strptime(d.group(1), '%m/%d/%Y').date().isoformat() if d else None
    return dict(value_pct=float(vals.pop()), as_of=as_of, source_url=url)
