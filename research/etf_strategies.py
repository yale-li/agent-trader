"""Monthly-rebalanced ETF strategies, total return, 2007-2026. Signals at month-end, applied next month.

Run from the repo root: python3 research/etf_strategies.py [--fetch]  (bars cached in backtest/etf_cache/)
Monthly data understates intra-month drawdowns. Sharpe uses rf=0."""
import json, math, sys
from pathlib import Path
sys.path.insert(0, 'skills/trade/scripts')
import backtest as b
CACHE = Path('backtest/etf_cache'); b.CACHE = CACHE
SYMS = ['SPY','QQQ','EFA','IEF','TLT','AGG','SHY','GLD','VNQ','DBC','SSO','QLD']
if '--fetch' in sys.argv: print(b.fetch(SYMS, rng='30y'))  # 'max' returns monthly bars whose dates miss the dividend dates
data = {s: json.loads((CACHE/f'{s}.json').read_text()) for s in SYMS}
COST = 0.0005
spy_dates = [x[0] for x in data['SPY']['bars']]
months = [d for k, d in enumerate(spy_dates[:-1]) if d[:7] != spy_dates[k+1][:7]] + [spy_dates[-1]]
px, tr = {}, {}
for s in SYMS:
    bars = data[s]['bars']; divs = data[s]['divs']; idx = 1.0; prev = None; p = {}; t = {}
    for d, o, h, l, c in bars:
        if prev: idx *= (c + divs.get(d, 0)) / prev
        prev = c; p[d] = c; t[d] = idx
    px[s], tr[s] = p, t
def at(m, s, i): return m[s].get(months[i])
START = '2006-12'
i0 = next(k for k, d in enumerate(months) if d[:7] == START)
def sma10(s, i):
    v = [at(px, s, k) for k in range(i-9, i+1)]
    return None if None in v else sum(v)/10
def ret12(s, i):
    a, z = at(tr, s, i-12), at(tr, s, i)
    return None if None in (a, z) else z/a - 1
def trend(s, i):  # hold s if above its 10-month SMA, else cash (SHY)
    m = sma10(s, i); return {s: 1.0} if m and at(px, s, i) > m else {'SHY': 1.0}
def mix(*parts):
    w = {}
    for frac, sub in parts:
        for s, x in sub.items(): w[s] = w.get(s, 0) + frac * x
    return w
STRATS = {
  'SPY buy&hold':      lambda i: {'SPY': 1},
  'QQQ buy&hold':      lambda i: {'QQQ': 1},
  '60/40 SPY/IEF':     lambda i: {'SPY': .6, 'IEF': .4},
  'Global 60/40':      lambda i: {'SPY': .45, 'EFA': .15, 'AGG': .4},
  'Permanent (4x25)':  lambda i: {'SPY': .25, 'TLT': .25, 'GLD': .25, 'SHY': .25},
  'All Weather (ret.)':lambda i: {'SPY': .3, 'TLT': .4, 'IEF': .15, 'GLD': .075, 'DBC': .075},
  'SPY 10m trend':     lambda i: trend('SPY', i),
  'GTAA5 (Faber)':     lambda i: mix(*[(.2, trend(s, i)) for s in ('SPY','EFA','IEF','VNQ','DBC')]),
  'GEM dual momentum': lambda i: ({max(('SPY','EFA'), key=lambda s: ret12(s, i)): 1}
                                  if ret12('SPY', i) > ret12('SHY', i) else {'AGG': 1}),
  '2x SPY (SSO) hold': lambda i: {'SSO': 1},
  '2x SPY 10m trend':  lambda i: {'SSO': 1} if trend('SPY', i) == {'SPY': 1} else {'SHY': 1},
}
def run(fn, a, z):
    eq, curve, w_prev, switches = 1.0, [1.0], {}, 0
    for i in range(a, z):
        w = fn(i); turn = sum(abs(w.get(s,0)-w_prev.get(s,0)) for s in set(w)|set(w_prev))
        if set(w) != set(w_prev) and w_prev: switches += 1
        r = sum(x*(at(tr, s, i+1)/at(tr, s, i)-1) for s, x in w.items())
        eq *= (1 + r) * (1 - turn*COST if w_prev else 1); curve.append(eq); w_prev = w
    return curve, switches
def st(curve, a, z):
    rs = [curve[k]/curve[k-1]-1 for k in range(1, len(curve))]
    yrs = len(rs)/12; cagr = curve[-1]**(1/yrs)-1
    mu = sum(rs)/len(rs); vol = math.sqrt(sum((r-mu)**2 for r in rs)/len(rs))*math.sqrt(12)
    pk, dd = 1, 0
    for v in curve: pk = max(pk, v); dd = max(dd, 1-v/pk)
    cal = {}
    for k in range(a, z):
        y = months[k+1][:4]; cal[y] = cal.get(y, 1)*(curve[k-a+1]/curve[k-a])
    return cagr, vol, dd, mu*12/vol, cal
def table(a, z, label):
    print(f"\n== {label}: {months[a][:7]} -> {months[z][:7]}  (monthly, 5bp/trade, total return, rf=0 Sharpe)")
    print(f"  {'strategy':<20}{'CAGR':>7}{'vol':>7}{'maxDD':>7}{'Sharpe':>7}{'worst yr':>9}{'2008':>8}{'2020':>8}{'2022':>8}{'sw/yr':>6}")
    for n, fn in STRATS.items():
        c, sw = run(fn, a, z); cagr, vol, dd, sh, cal = st(c, a, z)
        wy = min(cal.values())-1
        g = lambda y: f"{(cal[y]-1)*100:+.0f}%" if y in cal else '-'
        print(f"  {n:<20}{cagr*100:>6.1f}%{vol*100:>6.1f}%{dd*100:>6.1f}%{sh:>7.2f}{wy*100:>8.0f}%{g('2008'):>8}{g('2020'):>8}{g('2022'):>8}{sw/((z-a)/12):>6.1f}")
z = len(months)-1
table(i0, z, 'Full')
i16 = next(k for k, d in enumerate(months) if d[:7] == '2015-12')
table(i16, z, 'Same window as momentum backtest')
