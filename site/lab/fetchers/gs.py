"""Goldman Sachs Asset Management reader (GPIX GPIQ).

Sources (the same calls the fund page https://am.gs.com/en-us/advisors/funds/detail/<PV>/<CUSIP>/... makes):
  holdings  POST https://am.gs.com/services/funds  (read-only GraphQL query getFundsDetail -> allHoldings)
            weight = issuer field 'portfolio' (page column 'Weighting'); as_of = allHoldings.asAtDate;
            total = number of lines in the issuer list (as in the seed; quickStats 'noOfHoldings' can differ by one).
  rate      The fund page and the fund service publish no annualized distribution rate (only the 12-month trailing
            rate). The issuer-published 'Ann. Distribution Rate' (most recent distribution / NAV x 12) is on the monthly
            Fact Card PDF. The latest Fact Card is found with POST https://am.gs.com/api/documents (the page's document
            list, documentType 'monthlyFactCard'), downloaded from its publicURL and read with the small stdlib PDF text
            reader below: 'Ann. Distribution Rate  x.xx%' and the card's 'As of <Month D, YYYY>'.
"""
import datetime as _dt
import json
import re
import zlib

RATES = True

FUNDS = 'https://am.gs.com/services/funds'
DOCS = 'https://am.gs.com/api/documents'
HDR = {'Content-Type': 'application/json', 'Accept': 'application/json', 'Origin': 'https://am.gs.com',
       'Referer': 'https://am.gs.com/en-us/advisors/funds'}
IDS = {'GPIX': ('PV105258', '38149W622'), 'GPIQ': ('PV105259', '38149W630')}

Q_HOLD = ('query getFundsDetail($fundDetailRequest: FundDetailRequest) { fundsDetail(fundDetailRequest: $fundDetailRequest) '
          '{ fundName ticker cusip allHoldings { asAtDate holdings { holding ticker portfolio marketValue } } } }')


def _ids(fund):
    m = re.search(r'/detail/(PV\d+)/([0-9A-Z]{9})/', fund.get('url') or '')
    if m:
        return m.group(1), m.group(2)
    if fund['t'] in IDS:
        return IDS[fund['t']]
    raise RuntimeError(f'no pvNumber/shareClassId for {fund["t"]}')


def _post(http, url, body, referer=None):
    h = dict(HDR)
    if referer:
        h['Referer'] = referer
    r = http.post(url, data=json.dumps(body), headers=h)
    return r.json()


def _type(name):
    u = name.upper().strip()
    if re.match(r'^[CP]/\S+\s+FLEX\b', u) or re.match(r'^[CP]/', u):
        return 'option'
    if 'TREASURY BILL' in u or u.startswith('UNITED STATES TREASURY') or 'T-BILL' in u:
        return 'treasury'
    if 'MONEY MARKET' in u or 'GOVERNMENT FUND' in u or 'GOVT FUND' in u or u in ('CASH', 'US DOLLAR', 'USD CASH') or u.startswith('CASH'):
        return 'cash'
    if 'SELECT SECTOR' in u or re.search(r'\bETF\b', u) or 'SPDR' in u or 'ISHARES' in u:
        return 'etf'
    return 'stock'


def holdings(fund, http):
    pv, sc = _ids(fund)
    body = {'operationName': 'getFundsDetail', 'query': Q_HOLD,
            'variables': {'fundDetailRequest': {'country': 'us', 'language': 'en', 'audience': 'advisors', 'pvNumber': pv, 'shareClassId': sc}}}
    d = _post(http, FUNDS, body, fund.get('url'))
    if d.get('errors'):
        raise RuntimeError(f'fund service error: {str(d["errors"])[:200]}')
    fd = (d.get('data') or {}).get('fundsDetail') or {}
    if (fd.get('ticker') or '').upper() != fund['t'].upper():
        raise RuntimeError(f'fund service returned {fd.get("ticker")!r} for {fund["t"]}')
    ah = fd.get('allHoldings') or {}
    rows = ah.get('holdings') or []
    asof = ah.get('asAtDate')
    if not rows or not asof:
        raise RuntimeError('allHoldings empty or undated')
    data = []
    for x in rows:
        name = (x.get('holding') or '').strip()
        w = x.get('portfolio')
        if not name or w in (None, '', '--'):
            raise RuntimeError(f'holding row without name/weight: {x}')
        mv = abs(float(x.get('marketValue') or 0))
        data.append(([name, (x.get('ticker') or None), float(str(w).replace('%', '').replace(',', '')), _type(name)], mv))
    data.sort(key=lambda r: (-abs(r[0][2]), -r[1]))
    return {'as_of': str(asof)[:10], 'source_url': FUNDS, 'total': len(data), 'top': [r[0] for r in data[:10]]}


# ----------------------------------------------------------------------------------------------- rate (fact card)
def _fact_card(fund, http):
    pv, sc = _ids(fund)
    body = {'documentType_s': [], 'documentCategory_s': ['fundDocuments'], 'audience_s': ['advisors'], 'language_s': ['en'],
            'country_s': ['us'], 'documentSource_s': [], 'pvNumber_s': [pv], 'latest': True, 'startIndex': 0, 'count': 50}
    docs = _post(http, DOCS, body, fund.get('url'))
    if not isinstance(docs, list):
        raise RuntimeError(f'document list: unexpected response {str(docs)[:200]}')
    best = None
    for x in docs:
        f = {y.get('name'): y.get('values') or [] for y in x.get('LexFields') or []}
        if 'monthlyFactCard' not in f.get('documentType_s', []) or f.get('pvNumber_s') != [pv]:
            continue
        if (f.get('documentStatus_s') or ['active'])[0] != 'active' or not f.get('publicURL'):
            continue
        key = (f.get('displayDateTime_t') or [''])[0]
        if best is None or key > best[0]:
            best = (key, f['publicURL'][0], (f.get('filename') or [''])[0])
    if not best:
        raise RuntimeError('no monthly Fact Card in the issuer document list')
    if fund['t'].upper() not in best[2].upper():
        raise RuntimeError(f'fact card file {best[2]!r} does not name {fund["t"]}')
    return best[1]


def rate(fund, http):
    url = _fact_card(fund, http)
    r = http.get(url)
    if not r.content.startswith(b'%PDF'):
        raise RuntimeError('fact card is not a PDF')
    lines = _pdf_lines(r.content)
    txt = '\n'.join(lines)
    if fund['t'].upper() not in txt.upper() and fund['official_name'].replace('Goldman Sachs ', '').split(' ETF')[0].upper() not in txt.upper():
        raise RuntimeError('fact card text does not mention the fund')
    vals = re.findall(r'Ann\.? ?Distribution Rate\s*\|?\s*(-?\d+(?:\.\d+)?)\s*%', txt)
    dates = re.findall(r'As of ([A-Z][a-z]+ \d{1,2}, \d{4})', txt)
    if len(set(vals)) != 1 or len(set(dates)) != 1:
        raise RuntimeError(f'fact card: rate {vals} / as-of {dates} not unique')
    asof = _dt.datetime.strptime(dates[0], '%B %d, %Y').date().isoformat()
    return {'value_pct': float(vals[0]), 'as_of': asof, 'source_url': url.split('?')[0]}


# ----------------------------------------------------------------------------------------------- minimal PDF text reader
# Enough for the issuer's fact cards: Flate streams, object streams, Type0/simple fonts with ToUnicode CMaps,
# Tj/TJ text placed with Td/TD/Tm. Returns text lines (items on one baseline joined by ' | ').
def _pdf_stream(body):
    i = body.find(b'stream')
    if i < 0:
        return None
    hdr, j = body[:i], i + 6
    j += 2 if body[j:j + 2] == b'\r\n' else (1 if body[j:j + 1] in (b'\n', b'\r') else 0)
    raw = body[j:body.rfind(b'endstream')]
    if b'/FlateDecode' in hdr:
        try:
            return zlib.decompressobj().decompress(raw)
        except Exception:
            return None
    return None if b'/Filter' in hdr else raw


def _pdf_objects(b):
    objs = {}
    for m in re.finditer(rb'(?<![0-9])(\d+)\s+(\d+)\s+obj\b', b):
        e = b.find(b'endobj', m.end())
        if e > 0:
            objs[int(m.group(1))] = b[m.end():e]
    streams = {}
    for n, body in list(objs.items()):
        st = _pdf_stream(body)
        if st is None:
            continue
        streams[n] = st
        hdr = body.split(b'stream', 1)[0]
        if b'/ObjStm' in hdr:
            cnt = int(re.search(rb'/N\s+(\d+)', hdr).group(1)); first = int(re.search(rb'/First\s+(\d+)', hdr).group(1))
            nums = list(map(int, st[:first].split()))
            pairs = [(nums[i], nums[i + 1]) for i in range(0, 2 * cnt, 2)]
            for k, (on, off) in enumerate(pairs):
                end = pairs[k + 1][1] if k + 1 < len(pairs) else len(st) - first
                objs.setdefault(on, st[first + off:first + end])
    return objs, streams


def _pdf_cmap(data):
    mp = {}
    u16 = lambda h: bytes.fromhex(h.decode()).decode('utf-16-be', 'replace')
    for blk in re.findall(rb'beginbfchar(.*?)endbfchar', data, re.S):
        for a, u in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk):
            mp[int(a, 16)] = u16(u)
    for blk in re.findall(rb'beginbfrange(.*?)endbfrange', data, re.S):
        for a, z, rest in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)', blk):
            a, z = int(a, 16), int(z, 16)
            if rest.startswith(b'['):
                for k, u in enumerate(re.findall(rb'<([0-9A-Fa-f]+)>', rest)):
                    mp[a + k] = u16(u)
            else:
                base = int(rest[1:-1], 16)
                for c in range(a, z + 1):
                    if 0 <= base + c - a < 0x110000:
                        mp[c] = chr(base + c - a)
    return mp


def _pdf_dict(objs, s, key):
    m = re.search(rb'/' + key + rb'\s*(<<|\d+\s+\d+\s+R)', s)
    if not m:
        return b''
    if m.group(1) != b'<<':
        return objs.get(int(m.group(1).split()[0]), b'')
    i = k = m.start(1); depth = 0
    while k < len(s):
        if s[k:k + 2] == b'<<':
            depth += 1; k += 2; continue
        if s[k:k + 2] == b'>>':
            depth -= 1; k += 2
            if depth == 0:
                return s[i:k]
            continue
        k += 1
    return b''


def _pdf_decode(tok, font):
    mp, two = font
    if tok.startswith(b'<'):
        h = re.sub(rb'\s', b'', tok[1:-1]).decode()
        raw = bytes.fromhex(h + ('0' if len(h) % 2 else ''))
    else:
        esc = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\b', b'f': b'\f'}
        raw = re.sub(rb'\\([0-7]{1,3}|.)', lambda m: bytes([int(m.group(1), 8) & 255]) if m.group(1)[:1].isdigit()
                     else esc.get(m.group(1), m.group(1)), tok[1:-1], flags=re.S)
    codes = [raw[i] * 256 + raw[i + 1] for i in range(0, len(raw) - 1, 2)] if two else list(raw)
    return ''.join(mp.get(c, '?' if mp else chr(c)) for c in codes)


_OPS = re.compile(rb'/([A-Za-z0-9_+.-]+)\s+[-\d.]+\s+Tf|([-\d.]+)\s+([-\d.]+)\s+T[dD]|(?:[-\d.]+\s+){4}([-\d.]+)\s+([-\d.]+)\s+Tm'
                  rb'|(<[0-9A-Fa-f\s]*>|\((?:\\.|[^\\)])*\))\s*Tj|\[((?:<[0-9A-Fa-f\s]*>|\((?:\\.|[^\\)])*\)|[-\d.\s])*)\]\s*TJ|\bBT\b', re.S)


def _pdf_lines(b):
    objs, streams = _pdf_objects(b)
    out = []
    for p in sorted(n for n, o in objs.items() if re.search(rb'/Type\s*/Page\b', o)):
        po = objs[p]
        fonts = _pdf_dict(objs, _pdf_dict(objs, po, b'Resources'), b'Font')
        fmap = {}
        for name, on in re.findall(rb'/([A-Za-z0-9_+.-]+)\s+(\d+)\s+\d+\s+R', fonts):
            fo = objs.get(int(on), b'')
            tu = re.search(rb'/ToUnicode\s+(\d+)\s+\d+\s+R', fo)
            fmap[name] = (_pdf_cmap(streams.get(int(tu.group(1)), b'')) if tu else {}, b'/Type0' in fo)
        cont = re.search(rb'/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)', po)
        data = b'\n'.join(streams.get(int(i), b'') for i in re.findall(rb'(\d+)\s+\d+\s+R', cont.group(1))) if cont else b''
        items, font, x, y = [], ({}, False), 0.0, 0.0
        for m in _OPS.finditer(data):
            if m.group(0) == b'BT':
                x = y = 0.0
            elif m.group(1):
                font = fmap.get(m.group(1), ({}, False))
            elif m.group(2) is not None:
                x += float(m.group(2)); y += float(m.group(3))
            elif m.group(4) is not None:
                x, y = float(m.group(4)), float(m.group(5))
            else:
                parts = [m.group(6)] if m.group(6) else re.findall(rb'<[0-9A-Fa-f\s]*>|\((?:\\.|[^\\)])*\)', m.group(7))
                items.append((round(-y, 1), x, ''.join(_pdf_decode(t, font) for t in parts)))
        rows = {}
        for yy, xx, s in items:
            rows.setdefault(yy, []).append((xx, s))
        out += [' | '.join(s.strip() for _, s in sorted(rows[k]) if s.strip()) for k in sorted(rows)]
    return out
