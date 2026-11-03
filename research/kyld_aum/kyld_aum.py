"""KYLD (Kurv High Income ETF) net assets, month by month (Nov 2025 - Oct 2026).

Sources
  SEC N-PORT-P, Kurv ETF Trust (CIK 1782952): net assets at each fiscal quarter-end (Nov, Feb, May, Aug) and monthly
  sales / redemptions for every month.
  Income Lab daily closes (Yahoo Finance, through 2026-10-13) and Kurv's latest_price.json (Oct 30 NAV + holdings).
Method
  Quarter-end months are the SEC figure exactly. Other months: shares outstanding rebuilt from monthly flows
  (flow / that month's average price), pinned to the SEC share counts at both ends of the quarter, times the
  month-end close. Sep 2026 is an estimate (shares interpolated between Aug 31 and Oct 30). Oct 2026 = Kurv's Oct 30
  holdings total.
  py -3 -B claude_analysis/kyld_aum/kyld_aum.py   -> kyld_aum.csv, kyld_aum.png
"""
import json, urllib.request
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
t = (ROOT / 'site' / 'web' / 'builder_prices.js').read_text(encoding='utf8')
d = json.loads(t[t.index('=') + 1:].strip().rstrip(';'))
k = d['f']['KYLD']; px = {d['dates'][k['s'] + i]: c for i, c in enumerate(k['c'])}
days = sorted(px)
def last_day(ym): return max(x for x in days if x[:7] == ym)
def avg(ym): v = [px[x] for x in days if x[:7] == ym]; return sum(v) / len(v)

FLOWS = {'2025-11': (500000 + 13910261, 0),          # Oct 2025 seed ($0.5M) placed before the Nov 3 listing
         '2025-12': (10436007, 0), '2026-01': (15009808, 0), '2026-02': (2634276, 0),
         '2026-03': (0, 1798542), '2026-04': (1398355, 395342), '2026-05': (421824, 643075),
         '2026-06': (4537652, 0), '2026-07': (4814950, 1039015), '2026-08': (4364112, 0)}
SEC = {'2025-11': 14100855.88, '2026-02': 38421214.22, '2026-05': 41096401.58, '2026-08': 48590922.21}

months = sorted(FLOWS); raw, sh = {}, 0.0
for m in months:
    sh += (FLOWS[m][0] - FLOWS[m][1]) / avg(m); raw[m] = sh
true = {m: SEC[m] / px[last_day(m)] for m in SEC}           # SEC share counts at quarter-ends
aq = sorted(SEC); shares = {}
for m in months:
    if m in SEC:
        shares[m] = true[m]; continue
    a = max(x for x in aq if x < m); b = min(x for x in aq if x > m)
    i, n = months.index(m) - months.index(a), months.index(b) - months.index(a)
    err_a, err_b = true[a] - raw[a], true[b] - raw[b]
    shares[m] = raw[m] + err_a + (err_b - err_a) * i / n
rows = [(m, last_day(m), shares[m] * px[last_day(m)], 'SEC N-PORT' if m in SEC else 'rebuilt from SEC flows') for m in months]

lp = json.load(urllib.request.urlopen('https://web.services.kurvinvest.com/etfdata/KYLD/latest_price.json', timeout=15))
oct_na = sum(h['MarketValueCents'] for h in lp['Holdings']) / 100; oct_nav = lp['NAVCents'] / 100
oct_sh = oct_na / oct_nav; sep_day = last_day('2026-09')
sep_sh = shares['2026-08'] + (oct_sh - shares['2026-08']) * 0.5
rows.append(('2026-09', sep_day, sep_sh * px[sep_day], 'estimate'))
rows.append(('2026-10', lp['lastTradingDate'][:10], oct_na, 'Kurv holdings'))

with open(HERE / 'kyld_aum.csv', 'w', encoding='utf8') as f:
    f.write('month,as_of,net_assets_usd,source\n')
    for m, day, v, src in rows:
        f.write(f'{m},{day},{v:.0f},{src}\n')

# ---- chart: one series, columns, light surface
BLUE, BLUE_EST, INK, INK2, MUTED, GRID, SURF = '#2a78d6', '#a9c8ef', '#0b0b0b', '#52514e', '#8a8984', '#e7e6e2', '#fcfcfb'
labels = [__import__('datetime').date(int(m[:4]), int(m[5:]), 1).strftime('%b\n%Y' if m in ('2025-11', '2026-01') else '%b') for m, *_ in rows]
vals = [v / 1e6 for _, _, v, _ in rows]
fig, ax = plt.subplots(figsize=(12, 6.75), dpi=150); fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
for i, (m, _, v, src) in enumerate(rows):
    est = src == 'estimate'
    ax.bar(i, v / 1e6, width=0.62, color=BLUE_EST if est else BLUE, hatch='////' if est else None, edgecolor=SURF if not est else BLUE, linewidth=0 if not est else 0.0, zorder=3)
show = {0, 3, 6, 9, len(rows) - 1, max(range(len(rows)), key=lambda i: vals[i])}
for i in show:
    ax.text(i, vals[i] + 0.8, f'${vals[i]:.1f}M', ha='center', va='bottom', fontsize=12, color=INK, fontweight='bold')
ax.set_xticks(range(len(rows))); ax.set_xticklabels(labels, fontsize=11, color=INK2)
ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'${y:.0f}M')); ax.tick_params(axis='y', labelsize=11, colors=MUTED, length=0)
ax.tick_params(axis='x', length=0); ax.set_ylim(0, max(vals) * 1.18)
ax.grid(axis='y', color=GRID, linewidth=1, zorder=0)
for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
ax.spines['bottom'].set_color('#c9c8c3')
fig.text(0.06, 0.94, 'KYLD fund size, month by month', fontsize=22, fontweight='bold', color=INK)
fig.text(0.06, 0.895, 'Kurv High Income ETF, net assets at each month-end since its Nov 2025 launch', fontsize=13, color=INK2)
fig.text(0.06, 0.03, 'Sources: SEC N-PORT filings (quarter-end net assets and monthly flows), Kurv (Oct 30 holdings), Yahoo Finance prices.\n'
         'Months between SEC quarter-ends rebuilt from flows; September (hatched) is an estimate; October is Oct 30.', fontsize=9.5, color=MUTED, linespacing=1.5)
fig.text(0.97, 0.94, 'Implied Investing', fontsize=10, color=MUTED, ha='right', fontweight='bold')
plt.subplots_adjust(left=0.06, right=0.97, top=0.85, bottom=0.15)
fig.savefig(HERE / 'kyld_aum.png', facecolor=SURF)
for r in rows: print(f'{r[0]}  {r[1]}  ${r[2]/1e6:6.2f}M  {r[3]}')
