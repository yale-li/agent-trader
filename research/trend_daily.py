"""Index trend-following on daily bars: hold an index (or a 2x fund) while it is above its moving average, else cash.

Run from the repo root:  python3 research/trend_daily.py
Needs daily bars in backtest/etf_cache/ (research/etf_strategies.py --fetch) and, for the momentum comparison,
backtest/cache/ (skills/trade/scripts/backtest.py fetch).

Signal at the close, trade at that close +/- 5 bp (as the live agent trades ~14:00 ET). Total return
(dividends reinvested). Cash is SHY; before SHY existed (2002-07) it earns the 3-month T-bill
average for the year. Drawdowns are daily, so they are deeper
than the month-end figures in etf_strategies.py. Sharpe uses rf=0.

2x before 2006 uses a synthetic fund: 2 x index daily total return - 1 x cash return - 0.95%/yr fee,
checked against the real SSO/QLD over 2006-2026 (see "synthetic check").
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, "skills/trade/scripts")
CACHE = Path("backtest/etf_cache")
COST = 0.0005
FEE_2X = 0.0095
TBILL = {"1996": .0502, "1997": .0506, "1998": .0478, "1999": .0464, "2000": .0582, "2001": .0340, "2002": .0161}


def load(sym):
    d = json.loads((CACHE / f"{sym}.json").read_text())
    divs, idx, prev, close, tr = d["divs"], 1.0, None, {}, {}
    for day, o, h, l, c in d["bars"]:
        if prev:
            idx *= (c + divs.get(day, 0)) / prev
        prev = c
        close[day], tr[day] = c, idx
    return close, tr


CLOSE, TR = {}, {}
for s in ("SPY", "QQQ", "SHY", "SSO", "QLD"):
    CLOSE[s], TR[s] = load(s)
DATES = sorted(CLOSE["SPY"])


def cash_index():
    """SHY total return, spliced onto T-bill accrual before SHY's first bar."""
    out, idx = {}, 1.0
    for k, d in enumerate(DATES):
        if k:
            r = daily_ret("SHY", k) if DATES[k - 1] in TR["SHY"] else TBILL.get(d[:4], 0) / 252
            idx *= 1 + r
        out[d] = idx
    return out


def daily_ret(sym, k):
    a, b = TR[sym].get(DATES[k - 1]), TR[sym].get(DATES[k])
    return b / a - 1 if a and b else 0.0


def synth2x(under):
    """Synthetic 2x fund: daily 2x total return, financed at the SHY return, minus the fee."""
    out = {DATES[0]: 1.0}
    for k in range(1, len(DATES)):
        r = 2 * daily_ret(under, k) - daily_ret("CASH", k) - FEE_2X / 252
        out[DATES[k]] = out[DATES[k - 1]] * (1 + r)
    return out


TR["CASH"] = cash_index()
TR["2xSPY*"], TR["2xQQQ*"] = synth2x("SPY"), synth2x("QQQ")
REAL_2X = {"2xSPY*": "SSO", "2xQQQ*": "QLD"}


def signals(under, rule, n=200, band=0.0):
    """date -> True when invested. rule 'daily': SMA(n) with a +/-band; 'monthly': month-end close vs 10 month-end SMA."""
    closes = [CLOSE[under].get(d) for d in DATES]
    out, inside = {}, False
    if rule == "monthly":
        ends = [k for k in range(len(DATES) - 1) if DATES[k][:7] != DATES[k + 1][:7]]
        me = [k for k in ends if closes[k] is not None]
        for j, k in enumerate(me):
            if j >= 9:
                sma = sum(closes[m] for m in me[j - 9:j + 1]) / 10
                inside = closes[k] > sma
            nxt = me[j + 1] if j + 1 < len(me) else len(DATES)
            for m in range(k, nxt):
                out[DATES[m]] = inside
        return out
    window = []
    for k, c in enumerate(closes):
        if c is None:
            out[DATES[k]] = inside
            continue
        window.append(c)
        if len(window) > n:
            window.pop(0)
        if len(window) == n:
            sma = sum(window) / n
            if c > sma * (1 + band):
                inside = True
            elif c < sma * (1 - band):
                inside = False
        out[DATES[k]] = inside
    return out


def run(hold, under=None, rule=None, n=200, band=0.0, start="2000-01-03", end="2026-11-04"):
    """Equity curve over [start, end]; with no rule it is buy & hold."""
    sig = signals(under, rule, n, band) if rule else None
    ks = [k for k, d in enumerate(DATES) if start <= d <= end]
    eq, curve, prev_in, switches, days_in = 1.0, [], None, 0, 0
    for k in ks:
        if prev_in is not None:  # position decided at yesterday's close earns today's return
            sym = hold if prev_in else "CASH"
            r = (TR[sym][DATES[k]] / TR[sym][DATES[k - 1]] - 1) if DATES[k - 1] in TR[sym] and DATES[k] in TR[sym] else 0.0
            eq *= 1 + r
            days_in += prev_in
        now_in = sig[DATES[k]] if sig else True
        if prev_in is not None and now_in != prev_in:
            eq *= 1 - 2 * COST  # sell one, buy the other
            switches += 1
        curve.append((DATES[k], eq))
        prev_in = now_in
    return curve, switches, days_in / max(1, len(ks) - 1)


def stats(curve, switches=0, invested=1.0):
    vals = [v for _, v in curve]
    rs = [vals[k] / vals[k - 1] - 1 for k in range(1, len(vals))]
    years = len(rs) / 252
    mu = sum(rs) / len(rs)
    vol = math.sqrt(sum((r - mu) ** 2 for r in rs) / len(rs)) * math.sqrt(252)
    peak, dd, under, longest = vals[0], 0.0, 0, 0
    for v in vals:
        if v >= peak:
            peak, under = v, 0
        else:
            under += 1
            longest = max(longest, under)
        dd = max(dd, 1 - v / peak)
    cal = {}
    for (d0, v0), (d1, v1) in zip(curve, curve[1:]):
        cal[d1[:4]] = cal.get(d1[:4], 1) * v1 / v0
    return {"cagr": (vals[-1] / vals[0]) ** (1 / years) - 1, "vol": vol, "dd": dd, "sharpe": mu * 252 / vol,
            "underwater_y": longest / 252, "sw": switches / years, "inv": invested,
            "yr": {y: v - 1 for y, v in cal.items()}}


def period_ret(curve, a, b):
    pts = [v for d, v in curve if a <= d <= b]
    return pts[-1] / pts[0] - 1 if len(pts) > 1 else None


CRISES = [("2000-02", "2000-03-24", "2002-10-09"), ("2008", "2007-10-09", "2009-03-09"),
          ("2020", "2020-02-19", "2020-03-23"), ("2022", "2022-01-03", "2022-12-30")]


def table(title, rows):
    print(f"\n== {title}")
    head = f"  {'strategy':<30}{'CAGR':>7}{'vol':>7}{'maxDD':>7}{'Sharpe':>7}{'underw.':>8}{'sw/yr':>6}{'in%':>5}"
    print(head + "".join(f"{c[0]:>9}" for c in CRISES))
    for name, curve, sw, inv in rows:
        s = stats(curve, sw, inv)
        cr = "".join(f"{period_ret(curve, a, b) * 100:>+8.0f}%" if period_ret(curve, a, b) is not None else f"{'-':>9}"
                     for _, a, b in CRISES)
        print(f"  {name:<30}{s['cagr'] * 100:>6.1f}%{s['vol'] * 100:>6.1f}%{s['dd'] * 100:>6.1f}%{s['sharpe']:>7.2f}"
              f"{s['underwater_y']:>7.1f}y{s['sw']:>6.1f}{s['inv'] * 100:>4.0f}%" + cr)


def lineup(start, end, lev_syms):
    rows = []
    for under, lev in (("SPY", lev_syms[0]), ("QQQ", lev_syms[1])):
        for label, hold in ((under, under), (f"2x {under}", lev)):
            c, sw, inv = run(hold, start=start, end=end)
            rows.append((f"{label} buy & hold", c, sw, inv))
            c, sw, inv = run(hold, under, "monthly", start=start, end=end)
            rows.append((f"{label} 10-month MA (monthly)", c, sw, inv))
            c, sw, inv = run(hold, under, "daily", 200, 0.03, start=start, end=end)
            rows.append((f"{label} 200d MA +/-3% (daily)", c, sw, inv))
    return rows


def main():
    # synthetic 2x vs the real funds
    print("== synthetic check (2006-07 .. 2026-11): CAGR / maxDD, synthetic vs real")
    for syn, real in REAL_2X.items():
        a, b = run(syn, start="2006-07-05")[0], run(real, start="2006-07-05")[0]
        sa, sb = stats(a), stats(b)
        print(f"  {syn:<8} {sa['cagr']:.1%} / {sa['dd']:.1%}   {real}: {sb['cagr']:.1%} / {sb['dd']:.1%}")

    table("2000-01 .. 2026-11, includes the dot-com crash (2x = synthetic)", lineup("2000-01-03", "2026-11-04", ("2xSPY*", "2xQQQ*")))
    table("2006-07 .. 2026-11, real SSO / QLD", lineup("2006-07-05", "2026-11-04", ("SSO", "QLD")))

    # robustness: moving-average length and band
    print("\n== sensitivity, 2000-01 .. 2026-11: CAGR / maxDD / switches per year")
    print(f"  {'rule':<16}" + "".join(f"{h:>24}" for h in ("SPY", "QQQ", "2x QQQ*")))
    for n in (100, 150, 200, 250):
        for band in (0.0, 0.01, 0.03):
            cells = []
            for hold, under in (("SPY", "SPY"), ("QQQ", "QQQ"), ("2xQQQ*", "QQQ")):
                c, sw, inv = run(hold, under, "daily", n, band)
                s = stats(c, sw, inv)
                cells.append(f"{s['cagr']:.1%} / {s['dd']:.0%} / {s['sw']:.1f}")
            print(f"  {f'{n}d +/-{band:.0%}':<16}" + "".join(f"{x:>24}" for x in cells))

    # same window as the momentum backtest
    import backtest as bt
    data = bt.load_bars()
    rows = []
    for wd in range(5):
        res = bt.simulate(data, bt.make_cfg([]), start="2016-01-04", end="2026-11-04", rebalance_weekday=wd)
        curve = [(d, e) for d, e, _ in res["curve"]]
        rows.append((f"momentum strategy ({['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][wd]})", curve, 0, 1.0))
    rows.sort(key=lambda r: stats(r[1])["cagr"])
    rows += lineup("2016-01-04", "2026-11-04", ("SSO", "QLD"))
    table("2016-01 .. 2026-11, same window as the momentum backtest (momentum has survivorship bias)", rows)


if __name__ == "__main__":
    main()
