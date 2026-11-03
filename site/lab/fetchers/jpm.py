"""J.P. Morgan Asset Management reader (JEPI JEPQ ROCY ROCQ).

Sources (the same feeds the fund page uses):
  holdings  daily holdings Excel  FundsMarketingHandler/excel?type=dailyETFHoldings&cusip=<CUSIP>...
            header row 'Ticker | Security Description | Security Type | ... | % of Market Value | % of Net Assets',
            'As of Date: MM/DD/YYYY' above it. Weight = '% of Net Assets' (ties broken by '% of Market Value').
            total = product-data fundData.numberOfHoldings.
  rate      product-data  FundsMarketingHandler/product-data?cusip=<CUSIP>...  fundData.shareClass:
              JEPI, JEPQ   dailyYield.dividendYield     (page 'Dividend yield', daily, dailyYield.effectiveDate)
              ROCY, ROCQ   yieldMonthEnd.distributionYield (page 'Distribution yield', month-end, yieldMonthEnd.effectiveDate)
            The measure per fund is fixed (it matches the seed); a missing value raises rather than falling back.
"""
import io
import re
import warnings

RATES = True

CUSIP = {'JEPI': '46641Q332', 'JEPQ': '46654Q203', 'ROCY': '46654Q518', 'ROCQ': '46654Q492'}
MEASURE = {'JEPI': ('dailyYield', 'dividendYield'), 'JEPQ': ('dailyYield', 'dividendYield'),
           'ROCY': ('yieldMonthEnd', 'distributionYield'), 'ROCQ': ('yieldMonthEnd', 'distributionYield')}

XLS = 'https://am.jpmorgan.com/FundsMarketingHandler/excel?type=dailyETFHoldings&cusip={c}&country=us&role=adv&fundType=N_ETF'
PD = 'https://am.jpmorgan.com/FundsMarketingHandler/product-data?cusip={c}&country=us&role=adv&language=en&userLoggedIn=false'


def _cusip(fund):
    c = fund.get('cusip') or CUSIP.get(fund['t'])
    if not c:
        raise RuntimeError(f'no CUSIP for {fund["t"]}')
    return c


def _product(fund, http):
    c = _cusip(fund)
    url = PD.format(c=c)
    d = http.get(url, headers={'Accept': 'application/json'}).json()
    fd = d.get('fundData') or {}
    sc = fd.get('shareClass') or {}
    if (sc.get('cusip') or c).upper() != c.upper():
        raise RuntimeError(f'product-data returned share class {sc.get("cusip")} for {c}')
    return url, fd


def _pct(x):
    if x is None or x == '':
        return None
    if isinstance(x, (int, float)):
        return float(x)
    return float(str(x).strip().rstrip('%').replace(',', ''))


def _type(sec_type, name):
    st, u = (sec_type or '').upper(), (name or '').upper()
    if 'MONEY MARKET' in st or st in ('CURRENCIES', 'CASH') or 'CASH' in st:
        return 'cash'
    if 'TREASURY' in st or 'TREASURY' in u or 'T-BILL' in st:
        return 'treasury'
    if 'OPTION' in st or 'EQUITY LINKED' in st or 'ELN' == st:
        return 'option'
    if 'ETF' in st or 'EXCHANGE TRADED' in st:
        return 'etf'
    return 'stock'


def holdings(fund, http):
    import openpyxl
    c = _cusip(fund)
    url = XLS.format(c=c)
    raw = http.get(url).content
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
    asof, hdr, data = None, None, []
    for r in rows:
        cells = [x for x in r if x is not None and str(x).strip() != '']
        if hdr is None:
            for x in cells:
                m = re.search(r'As of Date:\s*(\d{1,2})/(\d{1,2})/(\d{4})', str(x))
                if m:
                    asof = f'{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}'
                if isinstance(x, str) and ' - ' in x and re.search(r' - [0-9A-Z]{9}$', x.strip()) and not x.strip().upper().endswith(c.upper()):
                    raise RuntimeError(f'holdings file is for another fund: {x!r}')
            if r and r[0] == 'Ticker':
                hdr = {str(h).strip(): i for i, h in enumerate(r) if h is not None}
                for need in ('Ticker', 'Security Description', 'Security Type', '% of Net Assets'):
                    if need not in hdr:
                        raise RuntimeError(f'holdings file lacks column {need!r}')
            continue
        name = r[hdr['Security Description']] if len(r) > hdr['Security Description'] else None
        w = r[hdr['% of Net Assets']] if len(r) > hdr['% of Net Assets'] else None
        if not name or w in (None, ''):
            continue                                      # footer / disclaimer lines
        mv = _pct(r[hdr['% of Market Value']]) if '% of Market Value' in hdr else 0.0
        tk = r[hdr['Ticker']]
        tk = str(tk).strip() if tk not in (None, '') else None
        data.append(([str(name).strip(), tk, _pct(w), _type(r[hdr['Security Type']], name)], abs(mv or 0.0)))
    if not hdr or not asof:
        raise RuntimeError('holdings file: header row or As of Date not found')
    if not data:
        raise RuntimeError('holdings file has no rows')
    data.sort(key=lambda x: (-abs(x[0][2]), -x[1]))
    total = None
    try:
        _, fd = _product(fund, http)
        n = fd.get('numberOfHoldings')
        total = int(n) if n is not None else None
    except Exception:
        total = None                                      # count is optional; holdings themselves are good
    return {'as_of': asof, 'source_url': url, 'total': total, 'top': [x[0] for x in data[:10]]}


def rate(fund, http):
    t = fund['t'].upper()
    if t not in MEASURE:
        return None
    block, field = MEASURE[t]
    url, fd = _product(fund, http)
    b = (fd.get('shareClass') or {}).get(block) or {}
    v, d = b.get(field), b.get('effectiveDate')
    if v is None or not d:
        raise RuntimeError(f'product-data shareClass.{block}.{field} missing (value {v!r}, date {d!r})')
    v = float(v)
    if v > 1.5:
        raise RuntimeError(f'unexpected scale for {block}.{field}: {v}')
    return {'value_pct': round(v * 100, 4), 'as_of': str(d)[:10], 'source_url': url}
